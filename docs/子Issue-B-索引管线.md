# 子 Issue B：索引管线模块

> 父 Issue：#1 总功能文档
> 位置：Python / FastAPI（`rag-service/`）
> 对应考察点：RAG 与信息检索（设计文档必答题 1 后半、2 前半）
> 前置：子 Issue A 已定数据契约（chunk 字段、编排次序）；`docs/参考调研-切片与元数据.md` 已定方向（B-1 不预留 parent 字段、B-2 传统关键词先行）

## 一、功能描述

索引管线是派生物的制造者：输入是 Java 送来的原文与 chunk，输出是可检索的向量索引与词法索引。它不持久化任何业务真相——一切可从 MySQL 重算（架构不变量二）。

### 1. 切片（chunk 端点）

**策略：标题层级感知切分，超长小节二次切分。**

```
第一刀：按 Markdown 标题层级（#/##/###，不更深）
        每个小节 = 候选 chunk，继承标题路径（heading_path）
第二刀：小节超过 max_tokens（默认 512）时，在小节内部按段落递归二分，
        二分出的块共享同一个 heading_path
        首选段落边界（空行），段落边界不命中时回退句子边界，再不命中回退硬切
```

**两条从实践中来的硬约束：**

- **代码栅栏识别**：解析器必须先识别 ``` / ~~~ 栅栏（结束栅栏同种字符且不短于开始栅栏），栅栏内部的 `#`、空行、疑似标题一概不当结构。知识库实测代码块 1173 个，技术笔记的代码注释里 `#` 极常见——不识别栅栏，切片会在代码中间乱切。
- **char_start/char_end 是切片的输出**：所有边界以原文偏移量为准记录。参考调研已确认 LlamaIndex Node 自带偏移字段、LangChain 可开 `add_start_index`——业界既有做法，但标题切分器是否原生提供偏移需落地时验证，不提供则切分后用原文回定位补算。

**参数**：`max_tokens=512`、`min_tokens=64`（低于此并入相邻块，避免碎片）。首版参数是经验值，M2 基线跑完后按黄金集调优——每个调整都是一条带数字的 PR。

### 2. Embedding（embed 端点）

- 可配置：`EMBEDDING_PROVIDER`（ollama / openai-compatible / deepseek）+ `EMBEDDING_MODEL` + `EMBEDDING_DIM`，启动时读配置
- 默认 `bge-m3`（Ollama 本地）；`nomic-embed-text` 作为对照组，跑同一黄金集出对比数字
- **维度守门**：embed 时校验返回向量维度 == `EMBEDDING_DIM`，不一致立即报错拒绝写入——否则维度错配的报错会在检索时才爆，且表现为距离计算异常，极难定位
- **换模型 = 全量重建**：Chroma collection 以 `embedding_model` 命名（如 `easyrag_bge-m3`），换模型即换 collection，旧 collection 留作对照后删。配置中的模型名与 collection 名不符时拒绝服务并提示重建——把"换模型"变成一个显式动作而不是隐性事故

### 3. 索引维护（Chroma + BM25）

**向量索引（Chroma）**：
- 持久化目录 `rag-service/data/chroma/`（.gitignore 已挡）
- 写入：`chunk_id` 作 document id，正文 + `heading_path` 拼接后 embed（标题路径进 embedding 文本——层级语义直接参与向量），metadata 存 `document_id / tags / seq`
- 删除：按 `document_id` 的 where 过滤批量删（Chroma 原生支持——这也是选它不选 FAISS 的主因：FAISS 无元数据过滤，按文档删除要自建 id 反查表）
- 全量重建：由 Java 编排（见 §三「全量重建的编排」）——Python 只暴露 `POST /reset` 清空与幂等 `/embed`，**不反向拉取 chunk、不读 MySQL**。这是不变量二的落地，也是换 embedding 模型的入口

**词法索引（BM25，rank-bm25 库，内存态）**：
- 中文分词用 jieba；索引内容 = chunk 正文 + 标题路径
- 进程内存构建，**空启动**（0 条），不依赖 Java 即可起进程；全量内容由 Java 在触发重建时分批推送经 `/embed` 构建
- 与向量索引同生命周期：写入/删除/重建均同步操作两份
- M2 就建好但只用向量检索；M4 混合检索（RRF 融合）启用它——**现在建是为了让"更新同步"从一开始就同时覆盖两份索引**，避免 M4 时补一套 BM25 的失效逻辑

### 4. 健康检查

`GET /health`：Python 进程、Chroma 可读写、embedding 端点可达（含当前模型名）、BM25 条数。Java 侧 A-1 的重试与 FAILED 判定依赖此端点。

## 二、数据原型（伪代码）

本模块不拥有数据库表。持有的两份派生数据：

```
Chroma collection "easyrag_<embedding_model>"
  id       = chunk_id (BIGINT 字符串化)
  document = chunk 正文 + "\n" + heading_path   # embedding 输入
  metadata = { document_id, tags[], seq, heading_path }

  # 重要：拼接只用于生成向量。返回给 Java 的 chunks[].text 与落库的
  #   chunk.text 永远是原文子串（text == 原文[char_start:char_end]），
  #   拼接结果不回写任何真相源——否则 A 的契约测试立即失败。

BM25 (rank-bm25, 进程内存)
  语料 = [jieba 分词(正文 + 标题路径)] × chunk_id 索引表
  # 同一 chunk 在两份索引中用同一 chunk_id 关联

配置（环境变量，.env.example 入库）
  EMBEDDING_PROVIDER = ollama | openai | deepseek
  EMBEDDING_MODEL    = bge-m3
  EMBEDDING_DIM      = 1024
  EMBEDDING_BASE_URL = http://localhost:11434  # openai 兼容端点时使用
  CHUNK_MAX_TOKENS   = 512
  CHUNK_MIN_TOKENS   = 64
  EMBED_BATCH_SIZE   = 64                      # 单次 /embed 的 chunk 上限，Java 分批依此设定
  # 无 JAVA_BASE_URL：推模式下 Python 不知道 Java 在哪，也不需要知道
```

## 三、对外接口（伪代码）

### Java → Python（对 A 的契约实现）

```
POST /chunk
  { document_id, text, title }
  → { chunks: [{ seq, text, char_start, char_end, heading_path, token_count }] }
     # 契约同子 Issue A §三；实现保证：char_start/char_end 截出的子串与 text 逐字符相等

POST /embed
  { document_id, chunks: [{ chunk_id, text, heading_path, tags }] }
  → { indexed: int }
     # 幂等：同 document_id 重复调用先清后写

DELETE /index/{document_id}
  → { removed: int }        # 向量 + BM25 同步清

POST /reset
  → { reset: bool }         # 清空当前 collection 与 BM25，供全量重建用（见下）

GET /health
  → { status, chroma, embedding: { model, reachable }, bm25: { chunks } }
```

### 全量重建的编排（Java 侧行为，非 Python 接口）

推模式下 Python 保持纯被动，**不反向调用 Java、不读 MySQL**。重建的触发与数据搬运都在 Java：

```
触发：手动 rebuild（换模型后 / 怀疑索引与库不一致）或 Java 启动自检发现索引缺失
1. Java → Python: GET /health，确认 embedding 可用且模型名 == 配置
2. Java → Python: POST /reset（模型已变则 collection 天然为空，可跳过）
3. Java:   从 MySQL 分页读全部未删除 chunk
4. Java → Python: 分批 POST /embed（每批固定大小，幂等）
5. Java:   汇总 indexed 计数，与 MySQL chunk 总数核对，不一致则告警
```

边界声明（与总 Issue §五、子 Issue A §四一致）：**B 与 C 永不反向调用 A，不读写 MySQL**。全量重建的编排是 Java 的职责，归属与上传索引的状态机一致——跨服务搬运数据本就是"数据权威层"该做的事。


## 四、模块边界

**B 负责**：切片算法与参数、embedding 调用与维度校验、两份索引的构建/删除/重建、模型与索引元信息的一致性守门。

**B 不负责**：chunk 落库（A）、何时触发索引（A 的状态机编排）、检索时的查询与融合（C，读 B 的索引）、评估指标计算（D）。B 的索引对 C 是只读消费。

**B 的无状态边界**：两份索引均为派生缓存，删除 `data/chroma/` 与重启进程不丢任何真相，代价只是重建时间（29 篇秒级，几百篇分钟级）。

## 五、验收

- [ ] 一篇含代码块的多级标题笔记：所有代码块未被栅栏外的 `#` 切碎（抽查黄金集语料中代码密度最高的一篇）
- [ ] 每个返回的 chunk：`text == 原文[char_start:char_end]` 逐字符相等（契约测试，A 的验收也依赖它）
- [ ] 超长小节被二分且子块共享 heading_path；短小节按 min_tokens 并入
- [ ] 同一文档重复调 /embed：结果幂等（Chroma 无重复 id，BM25 无重复条目）
- [ ] DELETE 后该文档在 Chroma 与 BM25 均查不到
- [ ] 删除 `data/chroma/` + 重启 Python：进程正常起、`/health` 绿、BM25 为 0 条（空启动，不依赖 Java）
- [ ] 上述状态下由 Java 触发全量重建：两份索引条数与 MySQL chunk 总数一致
- [ ] Python 代码中不存在指向 Java 的出站调用（边界的可验证形式：grep 无 Java 地址、无 MySQL 驱动依赖）
- [ ] 配置 EMBEDDING_DIM 与实际返回维度不符 → embed 立即报错，索引无脏写入
- [ ] 换 embedding 模型名：服务拒绝在旧 collection 上写入，提示需 rebuild
- [ ] /health 四项全绿；embedding 端点停掉时 health 如实变红（A 依赖它判 FAILED）

## 六、已裁决（汇总）

| # | 问题 | 结论 |
|---|---|---|
| B-1 | chunk 表预留 parent_chunk_id | 不预留；M4 采用父子分段时一次迁移改清楚（用户已定） |
| B-2 | 自动元数据路线 | 传统关键词（jieba/TF-IDF）先行，不够再上 LLM 抽取（用户已定）；M4 候选 |
| B-3 | 向量库 | **Chroma**（用户已定）。理由：原生元数据过滤与按 document_id 删除；持久化开箱即用；几百篇规模下 FAISS 性能优势无意义而元数据能力缺失是实痛 |
| B-4 | embedding 可配置 | 已定。provider + model + dim 三元组配置；换模型走显式 rebuild，不用旧 collection |
| B-5 | BM25 时机 | M2 就建。避免 M4 补失效逻辑；成本是启动秒级重建 |
| B-6 | 全量重建的依赖方向 | **推模式**（用户已定）：Java 编排，`/reset` + 分批 `/embed`；Python 不反向拉取。理由：边界无例外比带例外好讲；重建是编排行为，归属与上传状态机一致；拉模式省下的 Java 代码在几百篇规模下不构成收益 |
| B-7 | BM25 能否空启动 | **能**。Python 启动零外部依赖，避免两服务互等；M2 只用向量检索，Chroma 持久化重启不空，BM25 空着不影响 |

## 七、待议

| # | 问题 | 背景 |
|---|---|---|
| B-8 | heading_path 是否进 embedding 输入 | 当前设计是进（正文+标题拼接后 embed）。反方观点：拼接会稀释正文语义。可做成开关，用黄金集 A/B。默认进——参考调研中 LlamaIndex 的做法即此类拼接 |
| B-9 | Chroma 的 distance metric | 默认 cosine。bge-m3 官方建议 cosine；无需提前裁决，写死 cosine 起，异常再议 |

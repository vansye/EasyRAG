# 子 Issue A：资料管理模块

> 父 Issue：#1 总功能文档
> 位置：Spring Boot（`server/`）
> 对应考察点：知识组织与管理（设计文档必答题 1 的前半）

## 一、功能描述

资料管理是系统的入口与数据权威层。它负责把外部资料收进来、判断是否变化、持久化原文与切片元数据、编排索引流程，并对外提供全部 REST 接口。

### 1. 收录

两种来源，统一落到同一张 `document` 表：

- **上传**：md / txt 文件，multipart 提交
- **URL**：提交链接，服务端抓取正文

收录时提取标题与可选元数据。**设计前提：不要求用户改变记笔记的习惯**——核心链路只依赖普遍存在的结构信号（文档标题、Markdown 标题层级），frontmatter 属于可选增强。

```
title:  frontmatter.title → 正文第一个 # 一级标题 → 文件名（去扩展名）/ URL 的 <title>
tags:   frontmatter.tags → 空数组（无 frontmatter 的用户不受影响，只是少一种过滤维度）
```

frontmatter（`title / created / updated / tags / type`）在 Obsidian、Hugo、Jekyll 用户的笔记里常见，但不是普遍习惯。因此：

- **有 frontmatter**：`tags` 进入结构化过滤，检索多一个维度
- **无 frontmatter**：系统功能完整，检索依赖正文语义与 `heading_path` 的层级结构

单篇正文上限 **1 MB**。超限时返回明确提示（告知实际大小与上限，建议拆分），而不是静默截断或泛化的 500。常规笔记远达不到这个量级，触发上限本身就是一个值得提醒用户的信号。

**URL 抓取失败不入库**，直接返回 4xx。理由：没有正文的 document 对检索毫无意义，只会在列表里留下空壳，而空壳的清理成本高于重试成本。

### 2. 变更检测

`content_hash = SHA-256(规范化后的正文)`。规范化 = 统一换行符 + 去首尾空白（避免编辑器差异造成的假变更）。

- 更新时哈希不变 → 跳过重索引，只更新 `updated_at`
- 哈希变化 → 旧 chunk 全删、重新切片、重建索引

### 3. 索引编排

上传接口不等待索引完成，立即返回 `PENDING`，后台异步推进状态机：

```
PENDING ──► INDEXING ──► INDEXED
                   └───► FAILED（记 index_error，可手动重试）
```

选异步而非同步的理由：U1 的体验是"丢进去立刻出现在列表里"，而 embedding 依赖外部模型服务、耗时不可控。状态机同时给"更新重索引"和"手动重索引"提供统一入口。

编排次序（与模块 B 的两跳调用）：

```
1. Java: 存原文 + 哈希，status = PENDING
2. Java → Python: POST /chunk  {document_id, text}
3. Python → Java: chunks[]（无 id，带 seq / char_start / char_end / heading_path）
4. Java: 落库 chunk（生成 chunk_id），status = INDEXING
5. Java → Python: POST /embed  {chunks: [{chunk_id, text}]}
6. Python: embedding + 写向量/词法索引（派生）
7. Java: status = INDEXED, indexed_at = now
```

任一步失败 → status = FAILED，index_error 记具体原因。失败路径上**无条件调一次 `DELETE /index/{document_id}`**（Python 不可达时忽略该调用的失败，索引本就没写进去）：否则第 6 步部分成功会留下半成品索引，表现为"FAILED 的文档仍被检索命中"，而这种不一致查起来极难。已落库的 chunk 保留，供排查与下次重索引前清空。

**全量重建**：换 embedding 模型或怀疑索引与库不一致时，由 Java 编排——`GET /health` 校验 → `POST /reset` → 分页读 MySQL → 分批 `POST /embed` → 计数核对。Python 不反向拉取（见子 Issue B §三与 B-6）。

### 4. 删除

`document` 软删（`deleted_at`），`chunk` 硬删，并通知 Python 清除该文档的索引。

- 软删 document：删了什么可追溯、可恢复，且历史问答记录里的文档引用不会变成悬空 id
- 硬删 chunk：chunk 是派生物，没有保留价值；删掉后检索自然引用不到它 —— 这就是 U2 的实现

## 二、数据原型（伪代码）

```
document
  id            BIGINT        PK, auto
  source_type   ENUM          UPLOAD | URL          -- 预留 FOLDER（见 #1 收录入口裁决）
  source_uri    VARCHAR(1024) 上传=原始文件名，URL=链接
  title         VARCHAR(512)  见降级顺序
  content       LONGTEXT      原文（真相源，溯源展示与全量重建都依赖它）
  content_hash  CHAR(64)      SHA-256(规范化正文)
  tags          JSON          frontmatter.tags，用于结构化过滤
  index_status  ENUM          PENDING | INDEXING | INDEXED | FAILED
  index_error   VARCHAR(1024) NULL
  chunk_count   INT           default 0     -- MySQL 中该文档现存 chunk 行数（库内事实，非索引事实）
  created_at    DATETIME
  updated_at    DATETIME
  indexed_at    DATETIME      NULL
  deleted_at    DATETIME      NULL              -- 软删标记

  INDEX (index_status)
  INDEX (deleted_at)
  -- 不对 content_hash 加唯一约束：同内容不同来源是两份资料，
  --   去重会让"上传成功但列表里没有"变成难以解释的行为。重复内容仅提示，不阻止。

chunk
  id            BIGINT        PK, auto            -- 即 chunk_id，由 Java 生成（唯一写入者）
  document_id   BIGINT        FK → document.id
  seq           INT           文档内序号，从 0 起
  text          TEXT          切片正文
  char_start    INT           在 document.content 中的起始偏移
  char_end      INT           结束偏移（前端据此高亮原文，溯源的落点）
  heading_path  VARCHAR(512)  NULL  所属标题路径，如 "三、工作机制 > 1. 推理阶段划分"
  token_count   INT
  created_at    DATETIME

  INDEX (document_id, seq)
  -- char_start/char_end 与 heading_path 是溯源与结构化检索的基础：
  --   前者让引用能定位到原文那一段，后者给切片带上层级语义。
```

`tags` 用 JSON 列而不是 `tag` / `document_tag` 两张表：当前只需要"按标签过滤"，反范式够用；等真的要做标签聚合再规范化，避免一开始就维护两个真相源。

`tags` 允许为空数组，且空数组不影响任何核心功能——见 §一.1 的设计前提。`heading_path` 才是核心链路依赖的结构信号，它来自 Markdown 标题层级，对所有笔记普遍成立。

## 三、对外接口（伪代码）

### 前端 ↔ Spring Boot

```
POST   /api/documents
       multipart: file                       # 上传 md/txt
       或 json:   { url: string }            # 提交链接
  → 201 { id, title, source_type, index_status: "PENDING" }
  → 400 抓取失败 / 不支持的类型 / 空内容

GET    /api/documents?status=&page=&size=
  → 200 { total, items: [{ id, title, source_type, tags,
                           index_status, chunk_count, updated_at }] }
     # chunk_count 语义：MySQL 中现存的 chunk 行数，不是索引里的条数。
     #   PENDING  —— 0（首次）或上一轮的遗留数（重索引前不清）
     #   INDEXING —— 本轮切片的最终条数（切片原子返回、一次落库，不会中途增长）
     #   INDEXED  —— 与两份索引的条数一致
     #   FAILED   —— 与索引侧可能不一致，以本字段为准（库内是真相）

GET    /api/documents/{id}
  → 200 { id, title, content, tags, source_type, source_uri,
          index_status, index_error, chunk_count, created_at, updated_at }
  → 404 不存在或已删除

PUT    /api/documents/{id}
       multipart: file  或  json: { content: string }
  → 200 { id, index_status }                 # 哈希不变则 status 保持 INDEXED
  → 404

DELETE /api/documents/{id}
  → 204                                      # 软删 document + 硬删 chunk + 清索引
  → 404

POST   /api/documents/{id}/reindex
  → 202 { id, index_status: "PENDING" }      # 手动重索引，用于 FAILED 恢复

GET    /api/documents/{id}/chunks
  → 200 { items: [{ id, seq, text, char_start, char_end, heading_path, token_count }] }
       # 透明度接口：让"切成什么样"可见，也是调试切分策略的入口
```

### Spring Boot → Python（本模块只声明契约，实现见子 Issue B）

```
POST /chunk   { document_id, text, title }
  → { chunks: [{ seq, text, char_start, char_end, heading_path, token_count }] }
     # text 必须是原文子串：text == 原文[char_start:char_end] 逐字符相等

POST /embed   { document_id, chunks: [{ chunk_id, text, heading_path, tags }] }
  → { indexed: int }

DELETE /index/{document_id}
  → { removed: int }
```

## 四、模块边界

**A 负责**

- 全部对前端的 HTTP 入口（Python 不暴露给前端）
- 原文与 chunk 元数据的持久化，MySQL 的唯一写入者
- 内容哈希与变更判定
- 索引状态机与编排（含失败恢复入口）
- 为模块 C 提供 `chunk_id → 原文定位` 的补全能力（问答响应里的出处）

**A 不负责**

- 怎么切、切多大、按什么切（模块 B）
- 检索、改写、拒答、生成（模块 C）
- 评估指标的计算（模块 D）

**依赖方向**：A → B（HTTP 调用），A → C（HTTP 调用）。B 与 C 不反向调用 A，不读写 MySQL。

## 五、验收

- [ ] 上传一篇 md，列表中出现该条目，状态从 PENDING 变为 INDEXED（U1）
- [ ] 该文档的 `/chunks` 返回切片，每条 `char_start/char_end` 能在原文中截出对应文本
- [ ] 重新上传内容完全相同的文件 → 不触发重索引（`indexed_at` 不变）
- [ ] 修改正文后更新 → 旧 chunk 全部消失、新 chunk 生成、`chunk_count` 变化（U3 的前半）
- [ ] 删除文档 → 列表不再返回、`/chunks` 返回 404、索引侧该文档的向量被清除（U2 的前半）
- [ ] 抓取失败的 URL → 返回 4xx，`document` 表不新增行
- [ ] frontmatter 有 title 时用它；无 frontmatter 时回落到一级标题；都没有时用文件名
- [ ] **一篇完全没有 frontmatter 的 md 能正常收录、索引、被检索命中**（不写 frontmatter 的用户不受影响）
- [ ] 超过 1 MB 的文件 → 返回提示，含实际大小与上限说明
- [ ] 进程重启时处于 INDEXING 的文档 → 启动后被重置为 PENDING 并重新排队
- [ ] Python 服务不可用时 → status = FAILED 且 `index_error` 有具体原因，reindex 可恢复

## 六、已裁决

| # | 问题 | 结论 | 理由 |
|---|---|---|---|
| A-1 | 异步索引实现 | **Spring `@Async` + 线程池** | 单用户规模下 MQ 与任务表调度都是过度设计 |
| A-2 | 重启时卡在 INDEXING 的文档 | **启动时扫描并重置为 PENDING，重新排队** | 留给手动 reindex 会让文档永久卡住且看不出原因 |
| A-3 | 单篇正文体积上限 | **1 MB，超限返回明确提示** | 常规笔记远达不到；触发上限本身是值得提醒用户的信号（建议拆分），不静默截断 |
| A-4 | 全量重建的依赖方向 | **推模式**：Java 编排 reset + 分批 embed，Python 不反向调用 | 边界"B/C 不反向调用 A"保持无例外；重建是编排行为，与上传状态机同归属。详见子 Issue B B-6 |
| A-5 | FAILED 前是否清索引 | **无条件调一次 `DELETE /index/{id}`**，Python 不可达时忽略失败 | 半成品索引会造成"FAILED 却仍被检索命中"的不一致，排查成本远高于失败路径上多一次 HTTP |
| A-6 | `chunk_count` 的语义 | **MySQL 现存 chunk 行数**（库内事实，非索引事实），四态含义写进接口说明 | 两个来源都叫"chunk 数"必然产生歧义；固定为库内口径，索引条数由 `/health` 单独暴露 |

## 七、原待议项的去向（子 Issue B 已给出结论）

| # | 问题 | 结论 |
|---|---|---|
| A-7 | 无 frontmatter 用户的结构化过滤如何生效 | 归入 **B-2**：先走传统关键词抽取（jieba / TF-IDF，零成本、确定性）自动填充主题词，不够再上 LLM 抽取，届时有实测支撑。列为 M4 候选，不进 M2 基线 |
| A-8 | chunk 结构是否需要调整 | **够用，不调整**。B 的标题层级切分天然产出 `heading_path`，二次切分的子块共享同一路径；`char_start/char_end` 由切片输出并有逐字符契约测试兜底。B-1 已定不预留 `parent_chunk_id`，等 M4 真采用父子分段时一次迁移 |


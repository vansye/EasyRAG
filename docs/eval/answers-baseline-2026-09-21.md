# 在线问答验收报告（子 Issue D）

> 运行时间：2026-09-21T23:43:05+08:00。真实判定/生成模型：openai / `agnes-3.0-flash`；检索策略：dense；embedding：`bge-m3` / 1024 维；top_k=5；引用支持率核对：开启。

## 验收边界

样例语料在纯 ASCII 临时目录建隔离索引，经 B 的真实切片/embedding 与 C 的真实判定/生成得到结果；不读取业务 MySQL，不打开业务 Chroma，不写入历史。模型输出每次运行可能不同，数字是本次样本，不是概率保证。
拒答正确率、错源率与引用支持率都由本脚本在线得到；离线召回报告不能替代它们。

## 判定与状态

| 类别 | 题数 | 期望判定 | 判定符合 | 判定分布 | 状态分布 | 技术失败 |
|---|---:|---|---:|---|---|---:|
| Q（直答） | 22 | SUFFICIENT / PARTIAL | 22/22（100.0%） | {'SUFFICIENT': 18, 'PARTIAL': 4} | {'ANSWERED': 18, 'PARTIAL': 4} | 0 |
| R（需改写） | 8 | SUFFICIENT / PARTIAL | 8/8（100.0%） | {'PARTIAL': 2, 'SUFFICIENT': 6} | {'PARTIAL': 2, 'ANSWERED': 6} | 0 |
| N（库外） | 10 | NONE | 10/10（100.0%） | {'NONE': 10} | {'REFUSED': 10} | 0 |
| P（部分覆盖） | 2 | PARTIAL | 1/2（50.0%） | {'PARTIAL': 1, 'SUFFICIENT': 1} | {'PARTIAL': 1, 'ANSWERED': 1} | 0 |

N 类“判定符合”即拒答正确率；Q/R 类不符合即误拒；P 类判 SUFFICIENT 记为冒充完整、判 NONE 记为误拒。技术失败（如 INVALID_CITATIONS）不计入拒答。

## 出处

- 标注出处进入候选（Q+R）：29/30（96.7%）；未进入：Q21。
- 错源率（已作答且有引用的 Q/R 中，引用片段全部不属于标注出处）：1/30（3.3%）；错源题：Q21。

错源的答案引用合法、内容也可能没有编造，但答的不是用户资料里的那一篇；判定器与引用校验都抓不住它，只有检索层能防。

## 引用支持率

- 核对答案数：32；带引用的句子：支持 192、不支持 29、核对输出无效 0。
- 引用支持率：86.9%；存在不支持句子的题：Q2, Q3, Q5, Q7, Q10, Q11, Q12, Q13, R1, R2, R3, R4, R6, Q17, Q19, Q20, Q21, Q22, R7。
- 句子总数 287，其中无引用句 66（无引用句不核对，只计数）。

核对方式：答案去掉围栏代码与列表/标题标记后按句号/问号/感叹号/换行切句，以冒号结尾的引导句和纯加粗标题不算句子；每个带 [n] 的句子连同所引片段交给同一模型判断“是否完全由片段支持”。它衡量的是生成是否越出片段，不衡量答案是否正确。

## 耗时（毫秒）

| 指标 | 中位数 | 最大值 |
|---|---:|---:|
| 首段非空文本（含检索与判定，生成型问题） | 2645 | 12589 |
| 完整请求（全部问题） | 6559 | 23805 |
| 拒答完整请求 | 1747 | — |
| 问题 embedding | 943 | — |
| 向量检索 | 9 | — |
| 判定调用 | 758 | — |
| 生成调用 | 5804 | — |

- 生成提示字符数中位数：1846；答案字符数中位数：668。
- 模型调用：问答 79 次（判定 + 生成，含重试），引用核对 221 次；因上游不可用重试过的题 3，重试后仍不可用 0。
- 耗时为本机脚本内测量，不含浏览器网络与渲染；首段文本时间是用户能看到首字的下界；重试过的题只保留最后一次尝试的耗时。

## 参数与数据

- 语料：29 篇、222 个片段；语料指纹：`3d4b803355606400354a4b4f73f4fde54fc92e352f43ac80e1dd60ffda123425`。
- 黄金集：`golden-set-v1.md` SHA-256 `69b4e8f1a979305f33008c76a2bf45ea8aa5bc77ffe6b3e36ec11ab3239347e3`；`golden-set-v2-heldout.md` SHA-256 `f35262e05a6785f03a78d2738fa64337440e5dfa424d15a40f49da123909bbfd`。
- 切片：max_tokens=512；min_tokens=64；tokenizer SHA-256 `21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08`。
- 索引构建（真实 embedding）：39.5 秒。

## 复跑

在仓库的 `rag-service/` 目录执行；需要本地 Ollama 已拉取 embedding 模型，且 `.env` 或 `config/llm.json` 配好回答模型。

```powershell
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe -m scripts.eval_answers `
  --golden-set '../docs/eval/golden-set-v1.md' '../docs/eval/golden-set-v2-heldout.md' `
  --tokenizer 'data/tokenizers/bge-m3-5617a9f61b028005a4858fdac845db406aefb181.json' `
  --model 'bge-m3' `
  --dimensions 1024 `
  --top-k 5 `
  --faithfulness `
  --pause 1.0 --cooldown 15.0 --max-attempts 3 `
  --output '../docs/eval/answers-baseline-2026-09-21.md'
```

## 逐题明细

| 题号 | 类别 | 判定 | 状态 | 标注出处名次 | 引用名次 | 错源 | 支持/不支持 | 首字 ms | 完整 ms | 答案开头 |
|---|---|---|---|---:|---|---|---|---:|---:|---|
| Q1 | Q | SUFFICIENT | ANSWERED | 1 | 1,2,3 | 否 | 11/0 | 3701 | 8472 | **ACID 的定义** ACID 是数据库事务正确执行的四个关键特性的首字母缩写 [1]。它是关系型数据库（RDBMS |
| Q2 | Q | SUFFICIENT | ANSWERED | 1 | 1,2,3,5 | 否 | 7/1 | 2214 | 6453 | 根据提供的知识库片段，KV Cache 的定义及其解决的问题如下：  **1. KV Cache 是什么？** KV C |
| Q3 | Q | SUFFICIENT | ANSWERED | 1 | 1,4,5 | 否 | 5/2 | 5850 | 8623 | Redis 的持久化方式主要有 **RDB（快照持久化）** 和 **AOF（追加日志持久化）** 两种 [4]。它们的 |
| Q4 | Q | SUFFICIENT | ANSWERED | 1 | 1 | 否 | 2/0 | 3624 | 5533 | 根据提供的知识库片段，回答如下：  1.  **事务的隔离级别**：     SQL 标准定义了四个隔离级别（从低到高） |
| Q5 | Q | PARTIAL | PARTIAL | 1 | 1,2,3 | 否 | 5/1 | 12589 | 17892 | 基于提供的知识库片段，Docker 镜像与容器的关系主要体现为**模板与实例**的关系，具体说明如下：  1.  **镜 |
| Q6 | Q | SUFFICIENT | ANSWERED | 1 | 1,2,4 | 否 | 11/0 | 2678 | 9011 | LCEL（LangChain Expression Language，LangChain 表达式语言）是 LangCha |
| Q7 | Q | SUFFICIENT | ANSWERED | 1 | 1,2 | 否 | 8/1 | 9116 | 14452 | **Prompt Template 是什么？**  `PromptTemplate` 是 LangChain 中最基础的 |
| Q8 | Q | SUFFICIENT | ANSWERED | 1 | 1,2 | 否 | 2/0 | 2613 | 3219 | Pinia 是 Vue3 官方推荐的状态管理库 [1]，它替代了 Vuex [2]。它用于管理 Vue3 应用的全局状态 |
| Q9 | Q | SUFFICIENT | ANSWERED | 1 | 1,2 | 否 | 4/0 | 8097 | 9385 | **Docker 数据卷的定义：** Docker 数据卷是用于持久化和共享容器数据的机制 [1]。它是实现容器数据持久 |
| Q10 | Q | SUFFICIENT | ANSWERED | 1 | 1,2,3,4,5 | 否 | 4/2 | 3485 | 8736 | 根据提供的知识库片段，Bean 的定义和核心要点如下：  1.  **基本定义**：Bean 通常指**被 Spring |
| Q11 | Q | PARTIAL | PARTIAL | 1 | 1,2,3,4,5 | 否 | 6/1 | 2419 | 6043 | 基于提供的知识库片段，关于 URL 编码及其原因的回答如下：  **URL 编码是什么：** URL 编码用于把特殊字符 |
| Q12 | Q | PARTIAL | PARTIAL | 1 | 1,2,3 | 否 | 10/2 | 2173 | 8203 | 根据提供的知识库片段，Redis 在内存不足时通过**内存淘汰（Eviction）**机制来处理数据。以下是基于片段内容 |
| Q13 | Q | SUFFICIENT | ANSWERED | 1 | 1,2,3,4 | 否 | 7/1 | 9797 | 13005 | 拦截器（Interceptor）和过滤器（Filter）的主要区别及执行阶段如下：  **主要区别：**  1.  ** |
| Q14 | Q | PARTIAL | PARTIAL | 1 | 1,2,3 | 否 | 7/0 | 8132 | 16318 | 根据提供的知识库片段，Nginx 反向代理的定义及其与正向代理的区别回答如下：  **1. Nginx 反向代理是什么？ |
| Q15 | Q | SUFFICIENT | ANSWERED | 1 | 1,2,3 | 否 | 4/0 | 2134 | 3689 | 根据知识库片段，`create_agent` 的功能及其取代的旧版函数如下：  1.  **功能**：`create_a |
| Q16 | Q | SUFFICIENT | ANSWERED | 1 | 1 | 否 | 3/0 | 2058 | 5629 | 根据知识库片段，大模型“开卷考试”是对 **RAG（检索增强生成）** 技术的通俗比喻 [1]。  具体含义如下： *  |
| N1 | N | NONE | REFUSED | — | — | — | — | — | 1754 | 知识库中没有找到能回答这个问题的内容。 |
| N2 | N | NONE | REFUSED | — | — | — | — | — | 1294 | 知识库中没有找到能回答这个问题的内容。 |
| N3 | N | NONE | REFUSED | — | — | — | — | — | 3963 | 知识库中没有找到能回答这个问题的内容。 |
| N4 | N | NONE | REFUSED | — | — | — | — | — | 5400 | 知识库中没有找到能回答这个问题的内容。 |
| N5 | N | NONE | REFUSED | — | — | — | — | — | 1927 | 知识库中没有找到能回答这个问题的内容。 |
| N6 | N | NONE | REFUSED | — | — | — | — | — | 1341 | 知识库中没有找到能回答这个问题的内容。 |
| N7 | N | NONE | REFUSED | — | — | — | — | — | 1597 | 知识库中没有找到能回答这个问题的内容。 |
| P1 | P | PARTIAL | PARTIAL | — | 1,2 | — | 0/0 | 2574 | 6197 | 根据提供的知识库片段，关于“Transformer 的多头注意力机制”的信息非常有限。片段主要涉及对传统多头注意力（MH |
| R1 | R | PARTIAL | PARTIAL | 2 | 1,2,3,4,5 | 否 | 6/1 | 2067 | 6664 | 根据提供的知识库片段，大模型推理速度慢的原因及相应的优化手段如下：  **1. 速度变慢的原因** *   **KV C |
| R2 | R | SUFFICIENT | ANSWERED | 1 | 1,2,3,4 | 否 | 1/3 | 2083 | 22034 | 基于提供的知识库片段，关于“服务器断电了，Redis 数据会不会丢？”的回答如下：  **如果 Redis 仅使用默认配 |
| R3 | R | SUFFICIENT | ANSWERED | 1 | 2,4 | 否 | 2/1 | 2449 | 4634 | 根据提供的知识库片段，页面刷新后 Pinia 里的状态是否保留，取决于是否使用了持久化方案：  1.  **默认情况下， |
| R4 | R | PARTIAL | PARTIAL | 1 | 1,2,3,4,5 | 否 | 5/1 | 2133 | 4736 | ### 基于知识库片段的回答  防止数据库被 SQL 注入攻击的措施主要包含以下几点：  1. **避免直接拼接**：不 |
| R5 | R | SUFFICIENT | ANSWERED | 1 | 1,2 | 否 | 9/0 | 4023 | 9718 | **不一定。** Docker 容器删除后，数据是否丢失取决于存储方式。如果使用**数据卷（Volume）**，容器删除 |
| R6 | R | SUFFICIENT | ANSWERED | 1 | 1,4 | 否 | 9/1 | 5516 | 9782 | 根据提供的知识库片段，Redis 与数据库的配合方式及缓存不一致的处理方法如下：  **1. Redis 与数据库的配合 |
| Q17 | Q | SUFFICIENT | ANSWERED | 1 | 1,2,3,4,5 | 否 | 6/2 | 2038 | 6059 | 基于提供的知识库片段，回答如下：  1.  **登录状态的保持机制**：     HTTP 本身是无状态协议，同一个用户 |
| Q18 | Q | SUFFICIENT | ANSWERED | 1 | 1,3,4,5 | 否 | 11/0 | 1947 | 8144 | Redis 的 key 设置过期时间后，到期并不会被立即删除。实际上，“过期”仅表示设置了过期标记，具体的删除操作依赖于 |
| Q19 | Q | SUFFICIENT | ANSWERED | 1 | 1,2 | 否 | 6/2 | 2149 | 8095 | 基于提供的知识库片段，Redis 事务与 MySQL 事务的区别以及 Redis 事务出错是否回滚的回答如下：  **1 |
| Q20 | Q | SUFFICIENT | ANSWERED | 1 | 1,2,3,4 | 否 | 8/1 | 4312 | 12313 | 根据提供的知识库片段，epoll 相比 select/poll 的优势及高并发服务器使用它的原因如下：  **1. 优势 |
| Q21 | Q | SUFFICIENT | ANSWERED | — | 1,2,3 | 是 | 9/2 | 2393 | 23805 | ### 为什么文件写完并返回成功，断电后数据还是可能丢？  根据提供的知识库片段，原因主要涉及 Redis 数据常驻内存 |
| Q22 | Q | SUFFICIENT | ANSWERED | 1 | 1,2,4 | 否 | 7/1 | 2712 | 8968 | 根据提供的知识库片段，回答如下：  **1. `onMounted` 的执行时机** `onMounted` 是 Vue |
| N8 | N | NONE | REFUSED | — | — | — | — | — | 1611 | 知识库中没有找到能回答这个问题的内容。 |
| N9 | N | NONE | REFUSED | — | — | — | — | — | 2389 | 知识库中没有找到能回答这个问题的内容。 |
| N10 | N | NONE | REFUSED | — | — | — | — | — | 1740 | 知识库中没有找到能回答这个问题的内容。 |
| P2 | P | SUFFICIENT | ANSWERED | — | 1 | — | 5/0 | 7930 | 13465 | 根据提供的知识库片段，vLLM 的 PagedAttention 工作机制及其借鉴的操作系统机制如下：  **借鉴的操作 |
| R7 | R | SUFFICIENT | ANSWERED | 1 | 2,3,4,5 | 否 | 10/3 | 2935 | 9537 | Redis 中设置了一万个相同过期时间的 key，到期后内存占用没有降下来，主要存在两个层面的原因，包括 Redis 内 |
| R8 | R | SUFFICIENT | ANSWERED | 1 | 2 | 否 | 2/0 | 2549 | 4210 | 根据提供的知识库片段，回答如下：  1.  **让模型边生成边出结果的方法**：     使用 **`stream`（流 |

候选与完整答案见同名 `.json`。

## 环境与代码指纹

- Python: `3.14.6`
- chromadb: `1.5.9`
- tokenizers: `0.23.2`
- langchain: `1.3.17`
- langchain-openai: `1.6.0`

- `retrieval.split_markdown` SHA-256: `489fe26f5016b1a107bc2a49ad756f4d7b53fe0f112cdd03b472f3e355f8e4cf`
- `app/modules/qa/public.py` SHA-256: `47ed64d37c1cc7a280211d5223535006b33620934b93a3474e31bbb887a93b6f`
- `scripts/eval_answers.py` SHA-256: `b127624f8326c3d474c839ddfc41ef7af875bc6d5814b6765573c2b78cd06ba0`
- `scripts/eval_retrieval.py` SHA-256: `048100e46dcdeefce55b6ce3a96d53887324ff0d2f9b0bd4ba7654e3087767af`

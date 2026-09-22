# 在线问答验收报告（子 Issue D）

> 运行时间：2026-09-22T21:17:40+08:00。真实判定/生成模型：openai / `agnes-3.0-flash`；检索策略：hybrid；embedding：`bge-m3` / 1024 维；top_k=5；引用支持率核对：开启。

## 验收边界

样例语料在纯 ASCII 临时目录建隔离索引，经 B 的真实切片/embedding 与 C 的真实判定/生成得到结果；不读取业务 MySQL，不打开业务 Chroma，不写入历史。模型输出每次运行可能不同，数字是本次样本，不是概率保证。
拒答正确率、错源率与引用支持率都由本脚本在线得到；离线召回报告不能替代它们。

## 判定与状态

| 类别 | 题数 | 期望判定 | 判定符合 | 判定分布 | 状态分布 | 技术失败 |
|---|---:|---|---:|---|---|---:|
| Q（直答） | 22 | SUFFICIENT / PARTIAL | 22/22（100.0%） | {'SUFFICIENT': 16, 'PARTIAL': 6} | {'ANSWERED': 16, 'PARTIAL': 6} | 0 |
| R（需改写） | 8 | SUFFICIENT / PARTIAL | 8/8（100.0%） | {'PARTIAL': 3, 'SUFFICIENT': 5} | {'PARTIAL': 3, 'ANSWERED': 5} | 0 |
| N（库外） | 10 | NONE | 10/10（100.0%） | {'NONE': 10} | {'REFUSED': 10} | 0 |
| P（部分覆盖） | 2 | PARTIAL | 1/2（50.0%） | {'PARTIAL': 1, 'SUFFICIENT': 1} | {'PARTIAL': 1, 'ANSWERED': 1} | 0 |

N 类“判定符合”即拒答正确率；Q/R 类不符合即误拒；P 类判 SUFFICIENT 记为冒充完整、判 NONE 记为误拒。技术失败（如 INVALID_CITATIONS）不计入拒答。

## 出处

- 标注出处进入候选（Q+R）：30/30（100.0%）；未进入：无。
- 错源率（已作答且有引用的 Q/R 中，引用片段全部不属于标注出处）：0/30（0.0%）；错源题：无。

错源的答案引用合法、内容也可能没有编造，但答的不是用户资料里的那一篇；判定器与引用校验都抓不住它，只有检索层能防。

## 证据压缩

- 作答的 32 题共 160 个候选，判定器标为相关并送入生成的 76 个（47.5%）；只送子集的答案 32 题。
- 标注出处进了候选却被判定器排除的题：无。

判定器未输出 relevant 时按全部候选计，因此比例既含模型主动压缩也含未压缩。

## 引用支持率

- 核对答案数：32；带引用的句子：支持 165、不支持 24、核对输出无效 0。
- 引用支持率：87.3%；存在不支持句子的题：Q2, Q3, Q5, Q7, Q9, Q10, R1, R3, R6, Q18, Q19, Q21, R7, R8。
- 句子总数 225，其中无引用句 36（无引用句不核对，只计数）。

核对方式：答案去掉围栏代码与列表/标题标记后按句号/问号/感叹号/换行切句，以冒号结尾的引导句和纯加粗标题不算句子；每个带 [n] 的句子连同所引片段交给同一模型判断“是否完全由片段支持”。它衡量的是生成是否越出片段，不衡量答案是否正确。

## 耗时（毫秒）

| 指标 | 中位数 | 最大值 |
|---|---:|---:|
| 首段非空文本（含检索与判定，生成型问题） | 3355 | 139690 |
| 完整请求（全部问题） | 6116 | 143204 |
| 拒答完整请求 | 1698 | — |
| 问题 embedding | 885 | — |
| 向量检索 | 9 | — |
| 判定调用 | 956 | — |
| 生成调用 | 5013 | — |

- 生成提示字符数中位数：1096；答案字符数中位数：452。
- 模型调用：问答 78 次（判定 + 生成，含重试），引用核对 189 次；因上游不可用重试过的题 2，重试后仍不可用 0。
- 耗时为本机脚本内测量，不含浏览器网络与渲染；首段文本时间是用户能看到首字的下界；重试过的题只保留最后一次尝试的耗时。

## 参数与数据

- 语料：29 篇、222 个片段；语料指纹：`3d4b803355606400354a4b4f73f4fde54fc92e352f43ac80e1dd60ffda123425`。
- 黄金集：`golden-set-v1.md` SHA-256 `69b4e8f1a979305f33008c76a2bf45ea8aa5bc77ffe6b3e36ec11ab3239347e3`；`golden-set-v2-heldout.md` SHA-256 `f35262e05a6785f03a78d2738fa64337440e5dfa424d15a40f49da123909bbfd`。
- 切片：max_tokens=512；min_tokens=64；tokenizer SHA-256 `21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08`。
- 索引构建（真实 embedding）：40.6 秒。

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
  --strategy hybrid `
  --faithfulness `
  --pause 1.0 --cooldown 15.0 --max-attempts 3 `
  --output '../docs/eval/answers-relevant-2026-09-22.md'
```

## 逐题明细

| 题号 | 类别 | 判定 | 状态 | 标注出处名次 | 引用名次 | 错源 | 支持/不支持 | 首字 ms | 完整 ms | 答案开头 |
|---|---|---|---|---:|---|---|---|---:|---:|---|
| Q1 | Q | SUFFICIENT | ANSWERED | 1 | 1,3 | 否 | 7/0 | 3542 | 9051 | **什么是 ACID？** ACID 是数据库事务正确执行的四个关键特性的首字母缩写，包括原子性（Atomicity）、 |
| Q2 | Q | SUFFICIENT | ANSWERED | 1 | 1,5 | 否 | 4/1 | 3132 | 22257 | 根据提供的知识库片段，回答如下：  **1. KV Cache 是什么？** KV Cache（键值缓存）是在自回归语言 |
| Q3 | Q | SUFFICIENT | ANSWERED | 1 | 3,5 | 否 | 13/1 | 3057 | 9246 | 根据提供的知识库片段，Redis 的持久化方式主要包含 **RDB**、**AOF** 以及基于两者的 **混合持久化* |
| Q4 | Q | SUFFICIENT | ANSWERED | 1 | 1 | 否 | 3/0 | 2342 | 3637 | 根据知识库片段，事务的隔离级别及并发性能情况如下：  1.  **隔离级别**：SQL 标准定义了四个隔离级别，从低到高 |
| Q5 | Q | PARTIAL | PARTIAL | 1 | 1,3 | 否 | 2/1 | 2405 | 23122 | 根据提供的知识库片段，Docker 镜像和容器的关系如下：  1.  **模板与实例关系**：Docker 镜像是用于创 |
| Q6 | Q | SUFFICIENT | ANSWERED | 1 | 1,3,4 | 否 | 6/0 | 3657 | 15666 | 根据提供的知识库片段，LCEL（LangChain Expression Language，LangChain 表达式语 |
| Q7 | Q | PARTIAL | PARTIAL | 1 | 1 | 否 | 6/2 | 4378 | 14320 | 基于提供的知识库片段，关于 Prompt Template 的定义及其作用回答如下：  **1. Prompt Temp |
| Q8 | Q | SUFFICIENT | ANSWERED | 1 | 1,3 | 否 | 2/0 | 3231 | 3675 | Pinia 是 Vue3 官方推荐的状态管理库 [1][3]。它用于管理 Vue3 应用的全局状态，以实现组件间的数据共 |
| Q9 | Q | SUFFICIENT | ANSWERED | 1 | 1,2 | 否 | 5/1 | 3478 | 3880 | 根据提供的知识库片段，对问题的回答如下：  **1. Docker 数据卷是什么？** Docker 数据卷（Volum |
| Q10 | Q | PARTIAL | PARTIAL | 1 | 1,2 | 否 | 6/1 | 2645 | 7628 | 根据提供的知识库片段，对“什么是 Bean”的回答如下：  Bean 通常指被 Spring 容器管理的对象。只要对象交 |
| Q11 | Q | SUFFICIENT | ANSWERED | 1 | 1 | 否 | 3/0 | 5099 | 5890 | URL 编码是一种将特殊字符（如中文、空格、&、= 等）转换为浏览器和服务器都能正确识别的格式的技术 [1]。  进行编 |
| Q12 | Q | PARTIAL | PARTIAL | 1 | 1,2,3 | 否 | 9/0 | 4920 | 8552 | 基于提供的知识库片段，关于 Redis 内存不足时的淘汰机制及策略回答如下：  **1. 内存不足时的淘汰机制** 当  |
| Q13 | Q | SUFFICIENT | ANSWERED | 1 | 1,3 | 否 | 8/0 | 4972 | 8638 | 根据提供的知识库片段，拦截器（Interceptor）和过滤器（Filter）的主要区别及执行阶段如下：  **主要区别 |
| Q14 | Q | PARTIAL | PARTIAL | 1 | 1,2,3 | 否 | 7/0 | 2033 | 15079 | **Nginx 反向代理是什么**  Nginx 是一个高性能的 HTTP 和反向代理服务器，也被描述为轻量级、高性能的 |
| Q15 | Q | SUFFICIENT | ANSWERED | 1 | 1,2,3 | 否 | 2/0 | 11294 | 11698 | `create_agent` 是 LangChain 1.0 构建 Agent 的唯一入口 [1]。它取代了旧版的 `c |
| Q16 | Q | SUFFICIENT | ANSWERED | 1 | 1 | 否 | 1/0 | 2222 | 3076 | 大模型“开卷考试”是对RAG（检索增强生成）技术的通俗比喻。它指的是模型在生成回答之前，不需要死记硬背所有知识，而是可以 |
| N1 | N | NONE | REFUSED | — | — | — | — | — | 2078 | 知识库中没有找到能回答这个问题的内容。 |
| N2 | N | NONE | REFUSED | — | — | — | — | — | 1776 | 知识库中没有找到能回答这个问题的内容。 |
| N3 | N | NONE | REFUSED | — | — | — | — | — | 2043 | 知识库中没有找到能回答这个问题的内容。 |
| N4 | N | NONE | REFUSED | — | — | — | — | — | 1428 | 知识库中没有找到能回答这个问题的内容。 |
| N5 | N | NONE | REFUSED | — | — | — | — | — | 6091 | 知识库中没有找到能回答这个问题的内容。 |
| N6 | N | NONE | REFUSED | — | — | — | — | — | 1597 | 知识库中没有找到能回答这个问题的内容。 |
| N7 | N | NONE | REFUSED | — | — | — | — | — | 1619 | 知识库中没有找到能回答这个问题的内容。 |
| P1 | P | PARTIAL | PARTIAL | — | 1,2 | — | 2/0 | 2272 | 3647 | 根据提供的知识库片段，Transformer 的多头注意力机制（MHA，即传统多头注意力）的特征是每个注意力头都有独立的 |
| R1 | R | PARTIAL | PARTIAL | 1 | 1,2,4 | 否 | 6/2 | 2154 | 8054 | 基于提供的知识库片段，大模型推理速度慢的原因及优化手段如下：  **1. 推理速度慢的原因（主要指显存瓶颈导致的限制）* |
| R2 | R | SUFFICIENT | ANSWERED | 1 | 1,2,4 | 否 | 4/0 | 2211 | 4202 | 根据提供的知识库片段，回答如下：  1. **Redis 数据常驻内存，断电即失** [1]。 2. **内存不是持久存 |
| R3 | R | SUFFICIENT | ANSWERED | 1 | 1,2,4 | 否 | 2/1 | 2554 | 4126 | 默认情况下，Pinia Store 的状态只在内存中，页面刷新后会丢失 [2]。  如果需要页面刷新后状态仍然保留，可以 |
| R4 | R | PARTIAL | PARTIAL | 1 | 1 | 否 | 3/0 | 4422 | 6141 | 防止数据库被 SQL 注入攻击的措施包括：  - 不要直接拼接用户输入到 SQL 字符串 [1] - 优先使用预编译语句 |
| R5 | R | SUFFICIENT | ANSWERED | 1 | 1,2 | 否 | 7/0 | 15158 | 19484 | 不，Docker 容器删除后数据并不一定丢失，可以使用**数据卷（Volume）**来保留数据。  以下是基于知识库片段 |
| R6 | R | PARTIAL | PARTIAL | 1 | 1 | 否 | 8/3 | 4689 | 14409 | 基于提供的知识库片段，Redis 与数据库的配合方式及缓存不一致的处理方法如下：  **1. Redis 与数据库的配合 |
| Q17 | Q | PARTIAL | PARTIAL | 1 | 1,2 | 否 | 4/0 | 13616 | 15800 | **回答：**  HTTP 本身是无状态协议，同一个用户的多次请求默认不会自动关联，因此需要借助会话技术（Cookie  |
| Q18 | Q | SUFFICIENT | ANSWERED | 1 | 2,4,5 | 否 | 6/3 | 10352 | 15502 | Redis 的 key 设置过期时间后，并不会在到期时立即被删除；“过期”仅表示设置了过期标记，实际删除依赖特定的策略  |
| Q19 | Q | SUFFICIENT | ANSWERED | 1 | 1,2 | 否 | 9/1 | 4051 | 9047 | 根据提供的知识库片段，Redis 事务和 MySQL 事务的区别以及 Redis 事务出错时的回滚机制回答如下：  ** |
| Q20 | Q | SUFFICIENT | ANSWERED | 1 | 1,2,4 | 否 | 10/0 | 2429 | 7107 | 根据提供的知识库片段，epoll 相比 select/poll 的优势以及高并发服务器使用它的原因如下：  1. **管 |
| Q21 | Q | SUFFICIENT | ANSWERED | 4 | 1,3,4 | 否 | 4/1 | 2518 | 5338 | 文件写完并返回成功，但断电后数据仍可能丢失，是因为操作系统采用了 **Write-back（写回）** 写入模式。在这种 |
| Q22 | Q | SUFFICIENT | ANSWERED | 1 | 1,3 | 否 | 5/0 | 6597 | 9067 | 根据提供的知识库片段，回答如下：  **1. `onMounted` 的执行时机** `onMounted` 在组件完成 |
| N8 | N | NONE | REFUSED | — | — | — | — | — | 2986 | 知识库中没有找到能回答这个问题的内容。 |
| N9 | N | NONE | REFUSED | — | — | — | — | — | 1342 | 知识库中没有找到能回答这个问题的内容。 |
| N10 | N | NONE | REFUSED | — | — | — | — | — | 1551 | 知识库中没有找到能回答这个问题的内容。 |
| P2 | P | SUFFICIENT | ANSWERED | — | 1,3 | — | 6/0 | 139690 | 143204 | 根据提供的知识库片段，vLLM 的 PagedAttention 工作机制及其借鉴的机制如下：  1.  **借鉴机制* |
| R7 | R | SUFFICIENT | ANSWERED | 1 | 2,3,5 | 否 | 1/5 | 2258 | 6064 | 内存占用没有立刻降下来，是因为 Redis 的过期 key 不会在过期的那一刻被立即删除，而是通过**惰性删除**和** |
| R8 | R | SUFFICIENT | ANSWERED | 1 | 1 | 否 | 4/1 | 2930 | 4775 | 在 LangChain 中，让模型边生成边出结果的方法是使用 **stream（流式调用）** [1]。该方法适用于聊天 |

候选与完整答案见同名 `.json`。

## 环境与代码指纹

- Python: `3.14.6`
- chromadb: `1.5.9`
- tokenizers: `0.23.2`
- langchain: `1.3.17`
- langchain-openai: `1.6.0`

- `retrieval.split_markdown` SHA-256: `489fe26f5016b1a107bc2a49ad756f4d7b53fe0f112cdd03b472f3e355f8e4cf`
- `app/modules/qa/public.py` SHA-256: `4da51028646185b6f309c777d39f142c468def0bebb704919b300b4344b801d3`
- `scripts/eval_answers.py` SHA-256: `2bf6d1754dc32715f616daf6c26d678983249d8c99d73f63af9077f009c066f4`
- `scripts/eval_retrieval.py` SHA-256: `73c8519cc07deef5347c4c437e01d0fb2f986c40086ea1e04c9adf4c4cf51cfb`

# 在线问答验收报告（子 Issue D）

> 运行时间：2026-09-22T11:26:59+08:00。真实判定/生成模型：openai / `agnes-3.0-flash`；检索策略：hybrid；embedding：`bge-m3` / 1024 维；top_k=5；引用支持率核对：开启。

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

## 引用支持率

- 核对答案数：32；带引用的句子：支持 203、不支持 34、核对输出无效 0。
- 引用支持率：85.7%；存在不支持句子的题：Q3, Q4, Q7, Q11, Q12, Q14, Q15, P1, R3, R4, R5, R6, Q17, Q18, Q19, Q20, P2, R7, R8。
- 句子总数 295，其中无引用句 58（无引用句不核对，只计数）。

核对方式：答案去掉围栏代码与列表/标题标记后按句号/问号/感叹号/换行切句，以冒号结尾的引导句和纯加粗标题不算句子；每个带 [n] 的句子连同所引片段交给同一模型判断“是否完全由片段支持”。它衡量的是生成是否越出片段，不衡量答案是否正确。

## 耗时（毫秒）

| 指标 | 中位数 | 最大值 |
|---|---:|---:|
| 首段非空文本（含检索与判定，生成型问题） | 3505 | 70181 |
| 完整请求（全部问题） | 8927 | 193212 |
| 拒答完整请求 | 1900 | — |
| 问题 embedding | 957 | — |
| 向量检索 | 7 | — |
| 判定调用 | 978 | — |
| 生成调用 | 7780 | — |

- 生成提示字符数中位数：1881；答案字符数中位数：579。
- 模型调用：问答 79 次（判定 + 生成，含重试），引用核对 237 次；因上游不可用重试过的题 4，重试后仍不可用 0。
- 耗时为本机脚本内测量，不含浏览器网络与渲染；首段文本时间是用户能看到首字的下界；重试过的题只保留最后一次尝试的耗时。

## 参数与数据

- 语料：29 篇、222 个片段；语料指纹：`3d4b803355606400354a4b4f73f4fde54fc92e352f43ac80e1dd60ffda123425`。
- 黄金集：`golden-set-v1.md` SHA-256 `69b4e8f1a979305f33008c76a2bf45ea8aa5bc77ffe6b3e36ec11ab3239347e3`；`golden-set-v2-heldout.md` SHA-256 `f35262e05a6785f03a78d2738fa64337440e5dfa424d15a40f49da123909bbfd`。
- 切片：max_tokens=512；min_tokens=64；tokenizer SHA-256 `21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08`。
- 索引构建（真实 embedding）：32.7 秒。

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
  --output '../docs/eval/answers-hybrid-2026-09-22.md'
```

## 逐题明细

| 题号 | 类别 | 判定 | 状态 | 标注出处名次 | 引用名次 | 错源 | 支持/不支持 | 首字 ms | 完整 ms | 答案开头 |
|---|---|---|---|---:|---|---|---|---:|---:|---|
| Q1 | Q | SUFFICIENT | ANSWERED | 1 | 1,2,3 | 否 | 6/0 | 2069 | 7270 | ACID 是数据库事务正确执行的四个关键特性的首字母缩写，它是关系型数据库（RDBMS）的核心基石，用于保证一组数据库操 |
| Q2 | Q | SUFFICIENT | ANSWERED | 1 | 1,5 | 否 | 4/0 | 2895 | 13818 | **KV Cache 的定义** KV Cache 是在自回归语言模型（如 GPT、LLaMA）推理过程中，将前面已生成 |
| Q3 | Q | SUFFICIENT | ANSWERED | 1 | 2,3,5 | 否 | 10/3 | 5942 | 11680 | Redis 的持久化方式主要有 **RDB（快照持久化）** 和 **AOF（追加日志持久化）** 两种 [2]。此外， |
| Q4 | Q | SUFFICIENT | ANSWERED | 1 | 1 | 否 | 1/1 | 4535 | 5596 | 根据知识库片段 [1]，SQL 标准定义了四个隔离级别，从低到高依次为： 1. 读未提交 2. 读已提交 3. 可重复读 |
| Q5 | Q | PARTIAL | PARTIAL | 1 | 1,2,3,4 | 否 | 6/0 | 1993 | 57922 | 根据提供的知识库片段，Docker 镜像与容器之间的关系主要体现为**“只读模板”与“基于模板创建的实例”**的关系。具 |
| Q6 | Q | SUFFICIENT | ANSWERED | 1 | 1,3,4 | 否 | 10/0 | 2578 | 7812 | LCEL 的全称是 **LangChain Expression Language（LangChain 表达式语言）** |
| Q7 | Q | PARTIAL | PARTIAL | 1 | 1,2,3,4,5 | 否 | 11/2 | 3026 | 12741 | 基于提供的知识库片段，回答如下：  **Prompt Template 是什么？**  1.  **定义与核心用法**： |
| Q8 | Q | SUFFICIENT | ANSWERED | 1 | 1,3,5 | 否 | 3/0 | 3592 | 5211 | 根据提供的知识库片段，回答如下：  **Pinia 是什么：** Pinia 是 Vue3 官方推荐的状态管理库，它替代 |
| Q9 | Q | SUFFICIENT | ANSWERED | 1 | 1,2 | 否 | 3/0 | 5641 | 6814 | Docker 数据卷是 Docker 中用于持久化和共享容器数据的机制，属于实现容器数据持久化与共享的核心机制 [1]。 |
| Q10 | Q | SUFFICIENT | ANSWERED | 1 | 1,2 | 否 | 6/0 | 2261 | 3585 | Bean 通常指被 Spring 容器管理的对象 [1]。只要对象交给容器创建、装配和管理，它就可以被称为 Bean [ |
| Q11 | Q | PARTIAL | PARTIAL | 1 | 1,2,5 | 否 | 3/2 | 9858 | 12718 | **URL 编码是什么** URL 编码用于把特殊字符转换成浏览器和服务器都能正确识别的格式 [1]。  **为什么要编 |
| Q12 | Q | PARTIAL | PARTIAL | 1 | 1,2,3 | 否 | 7/2 | 2559 | 8806 | **回答：**  根据提供的知识库片段，Redis 在内存不足时通过**内存淘汰（Eviction）**机制来释放空间  |
| Q13 | Q | SUFFICIENT | ANSWERED | 1 | 1,2 | 否 | 8/0 | 2122 | 7233 | 拦截器（Interceptor）和过滤器（Filter）的主要区别及执行阶段如下：  **主要区别：** 1.  **规 |
| Q14 | Q | PARTIAL | PARTIAL | 1 | 1,2,3 | 否 | 7/1 | 2070 | 11595 | 基于提供的知识库片段，关于 Nginx 反向代理及其与正向代理的区别，回答如下：  ### 1. Nginx 反向代理是 |
| Q15 | Q | SUFFICIENT | ANSWERED | 1 | 1,2,3 | 否 | 8/1 | 5485 | 18722 | 根据提供的知识库片段，回答如下：  **`create_agent` 是干什么的？** `create_agent()` |
| Q16 | Q | SUFFICIENT | ANSWERED | 1 | 1 | 否 | 4/0 | 2279 | 5172 | 大模型“开卷考试”是对 **RAG（检索增强生成）** 技术的一种通俗理解 [1]。  具体含义如下： 1.  它指的是 |
| N1 | N | NONE | REFUSED | — | — | — | — | — | 1960 | 知识库中没有找到能回答这个问题的内容。 |
| N2 | N | NONE | REFUSED | — | — | — | — | — | 1461 | 知识库中没有找到能回答这个问题的内容。 |
| N3 | N | NONE | REFUSED | — | — | — | — | — | 2003 | 知识库中没有找到能回答这个问题的内容。 |
| N4 | N | NONE | REFUSED | — | — | — | — | — | 1579 | 知识库中没有找到能回答这个问题的内容。 |
| N5 | N | NONE | REFUSED | — | — | — | — | — | 5104 | 知识库中没有找到能回答这个问题的内容。 |
| N6 | N | NONE | REFUSED | — | — | — | — | — | 3196 | 知识库中没有找到能回答这个问题的内容。 |
| N7 | N | NONE | REFUSED | — | — | — | — | — | 1453 | 知识库中没有找到能回答这个问题的内容。 |
| P1 | P | PARTIAL | PARTIAL | — | 1,2,5 | — | 4/2 | 2503 | 6663 | 基于提供的知识库片段，关于 Transformer 的多头注意力机制（MHA），可以总结如下：  在传统的 MHA（Mu |
| R1 | R | PARTIAL | PARTIAL | 1 | 1,2,3,4,5 | 否 | 7/0 | 70181 | 79704 | 基于提供的知识库片段，关于大模型推理速度慢的原因及优化手段回答如下：  **1. 速度慢的原因：显存瓶颈** KV Ca |
| R2 | R | SUFFICIENT | ANSWERED | 1 | 1,2,4 | 否 | 4/0 | 15964 | 17612 | 根据提供的知识库片段，回答如下：  **会丢失数据。**  Redis 的数据常驻内存，而内存不是持久存储，因此断电会导 |
| R3 | R | SUFFICIENT | ANSWERED | 1 | 1,2,4 | 否 | 6/1 | 4067 | 7432 | **默认情况下，页面刷新后 Pinia 里的状态会丢失。**  根据知识库片段 [2]，默认情况下，Pinia Stor |
| R4 | R | PARTIAL | PARTIAL | 1 | 1,2,3,4 | 否 | 4/1 | 10326 | 18558 | 基于提供的知识库片段，防止数据库被 SQL 注入攻击的方法主要如下：  1. **避免直接拼接用户输入**：不要直接将用 |
| R5 | R | SUFFICIENT | ANSWERED | 1 | 1,2,4 | 否 | 6/2 | 9361 | 26365 | **不，Docker 容器删除并不总是意味着数据消失，使用数据卷（Volume）可以保留数据。**  根据知识库片段，具 |
| R6 | R | PARTIAL | PARTIAL | 1 | 1,2,3,4,5 | 否 | 8/5 | 6589 | 193212 | 基于提供的知识库片段，针对“Redis 和数据库怎么配合”以及“缓存不一致怎么处理”的回答如下：  ### 1. Red |
| Q17 | Q | PARTIAL | PARTIAL | 1 | 1,2,3,4,5 | 否 | 5/1 | 4679 | 8406 | **回答：**  登录状态通过会话技术保存，例如使用 Cookie 和 Session 来维持登录状态、用户标识等信息  |
| Q18 | Q | SUFFICIENT | ANSWERED | 1 | 1,2,4,5 | 否 | 7/2 | 12688 | 26880 | Redis 的 key 设置过期时间后，到期**不会**立即被删除 [1]。  实际的删除策略是**惰性删除**与**定 |
| Q19 | Q | SUFFICIENT | ANSWERED | 1 | 1,2 | 否 | 11/1 | 2903 | 9321 | 基于提供的知识库片段，Redis 事务与 MySQL 事务的区别以及 Redis 事务出错后的处理机制如下：  **1. |
| Q20 | Q | SUFFICIENT | ANSWERED | 1 | 1,2,3,4,5 | 否 | 11/1 | 5974 | 13038 | 根据提供的知识库片段，epoll 相比 select/poll 的优势以及高并发服务器采用它的原因如下：  **1. 核 |
| Q21 | Q | SUFFICIENT | ANSWERED | 4 | 1,3,4 | 否 | 5/0 | 2817 | 22966 | **为什么文件写完并返回成功，断电后数据还是可能丢？**  因为操作系统通常采用 **Write-back（写回）**  |
| Q22 | Q | SUFFICIENT | ANSWERED | 1 | 1,2,3,4 | 否 | 9/0 | 2625 | 9595 | 根据提供的知识库片段，回答如下：  **1. Vue 的 onMounted 是什么时机执行的？**  *   `onM |
| N8 | N | NONE | REFUSED | — | — | — | — | — | 9049 | 知识库中没有找到能回答这个问题的内容。 |
| N9 | N | NONE | REFUSED | — | — | — | — | — | 1840 | 知识库中没有找到能回答这个问题的内容。 |
| N10 | N | NONE | REFUSED | — | — | — | — | — | 1364 | 知识库中没有找到能回答这个问题的内容。 |
| P2 | P | SUFFICIENT | ANSWERED | — | 1 | — | 5/1 | 2695 | 9699 | vLLM 的 PagedAttention 工作机制及借鉴的机制如下：  1.  **借鉴机制**：PagedAtten |
| R7 | R | SUFFICIENT | ANSWERED | 1 | 1,2,3,5 | 否 | 6/4 | 3419 | 11334 | 根据提供的知识库片段，Redis 中批量设置相同过期时间的 Key 到期后内存未立即释放的原因及机制如下：  **1.  |
| R8 | R | SUFFICIENT | ANSWERED | 1 | 1,2 | 否 | 8/1 | 6209 | 12054 | 根据提供的知识库片段，针对您的问题回答如下：  **1. 如何边生成边出结果** 在 LangChain 中，可以使用  |

候选与完整答案见同名 `.json`。

## 环境与代码指纹

- Python: `3.14.6`
- chromadb: `1.5.9`
- tokenizers: `0.23.2`
- langchain: `1.3.17`
- langchain-openai: `1.6.0`

- `retrieval.split_markdown` SHA-256: `489fe26f5016b1a107bc2a49ad756f4d7b53fe0f112cdd03b472f3e355f8e4cf`
- `app/modules/qa/public.py` SHA-256: `47ed64d37c1cc7a280211d5223535006b33620934b93a3474e31bbb887a93b6f`
- `scripts/eval_answers.py` SHA-256: `6867fb291592b8d3a7e6e3e4958c7a60620751748c023757bb76399423622940`
- `scripts/eval_retrieval.py` SHA-256: `73c8519cc07deef5347c4c437e01d0fb2f986c40086ea1e04c9adf4c4cf51cfb`

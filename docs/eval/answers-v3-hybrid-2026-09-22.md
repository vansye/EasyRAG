# 在线问答验收报告（子 Issue D）

> 运行时间：2026-09-22T23:00:59+08:00。真实判定/生成模型：openai / `agnes-3.0-flash`；检索策略：hybrid；embedding：`bge-m3` / 1024 维；top_k=5；引用支持率核对：开启。

## 验收边界

样例语料在纯 ASCII 临时目录建隔离索引，经 B 的真实切片/embedding 与 C 的真实判定/生成得到结果；不读取业务 MySQL，不打开业务 Chroma，不写入历史。模型输出每次运行可能不同，数字是本次样本，不是概率保证。
拒答正确率、错源率与引用支持率都由本脚本在线得到；离线召回报告不能替代它们。

## 判定与状态

| 类别 | 题数 | 期望判定 | 判定符合 | 判定分布 | 状态分布 | 技术失败 |
|---|---:|---|---:|---|---|---:|
| Q（直答） | 12 | SUFFICIENT / PARTIAL | 12/12（100.0%） | {'SUFFICIENT': 9, 'PARTIAL': 3} | {'ANSWERED': 9, 'PARTIAL': 3} | 0 |
| R（需改写） | 3 | SUFFICIENT / PARTIAL | 3/3（100.0%） | {'SUFFICIENT': 3} | {'ANSWERED': 3} | 0 |
| N（库外） | 0 | NONE | 0/0 | {} | {} | 0 |
| P（部分覆盖） | 4 | PARTIAL | 3/4（75.0%） | {'SUFFICIENT': 1, 'PARTIAL': 3} | {'ANSWERED': 1, 'PARTIAL': 3} | 0 |

N 类“判定符合”即拒答正确率；Q/R 类不符合即误拒；P 类判 SUFFICIENT 记为冒充完整、判 NONE 记为误拒。技术失败（如 INVALID_CITATIONS）不计入拒答。

## 出处

- 标注出处进入候选（Q+R）：15/15（100.0%）；未进入：无。
- 错源率（已作答且有引用的 Q/R 中，引用片段全部不属于标注出处）：0/15（0.0%）；错源题：无。

错源的答案引用合法、内容也可能没有编造，但答的不是用户资料里的那一篇；判定器与引用校验都抓不住它，只有检索层能防。

## 证据压缩

- 作答的 19 题共 95 个候选，判定器标为相关并送入生成的 39 个（41.1%）；只送子集的答案 19 题。
- 标注出处进了候选却被判定器排除的题：无。

判定器未输出 relevant 时按全部候选计，因此比例既含模型主动压缩也含未压缩。

## 引用支持率

- 核对答案数：19；带引用的句子：支持 61、不支持 16、核对输出无效 0。
- 引用支持率：79.2%；存在不支持句子的题：Q26, Q28, Q29, Q30, Q31, P5, P6, R9, R10, R11。
- 句子总数 96，其中无引用句 19（无引用句不核对，只计数）。

核对方式：答案去掉围栏代码与列表/标题标记后按句号/问号/感叹号/换行切句，以冒号结尾的引导句和纯加粗标题不算句子；每个带 [n] 的句子连同所引片段交给同一模型判断“是否完全由片段支持”。它衡量的是生成是否越出片段，不衡量答案是否正确。

## 耗时（毫秒）

| 指标 | 中位数 | 最大值 |
|---|---:|---:|
| 首段非空文本（含检索与判定，生成型问题） | 3208 | 44141 |
| 完整请求（全部问题） | 9901 | 44742 |
| 拒答完整请求 | — | — |
| 问题 embedding | 899 | — |
| 向量检索 | 10 | — |
| 判定调用 | 1287 | — |
| 生成调用 | 4439 | — |

- 生成提示字符数中位数：1045；答案字符数中位数：303。
- 模型调用：问答 38 次（判定 + 生成，含重试），引用核对 77 次；因上游不可用重试过的题 0，重试后仍不可用 0。
- 耗时为本机脚本内测量，不含浏览器网络与渲染；首段文本时间是用户能看到首字的下界；重试过的题只保留最后一次尝试的耗时。

## 参数与数据

- 语料：29 篇、222 个片段；语料指纹：`3d4b803355606400354a4b4f73f4fde54fc92e352f43ac80e1dd60ffda123425`。
- 黄金集：`golden-set-v3-hard.md` SHA-256 `75a3ac40bb9d23d52c33c92433ad266661f580f0fe57c7887d9fa350975b35c5`。
- 切片：max_tokens=512；min_tokens=64；tokenizer SHA-256 `21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08`。
- 索引构建（真实 embedding）：41.1 秒。

## 复跑

在仓库的 `rag-service/` 目录执行；需要本地 Ollama 已拉取 embedding 模型，且 `.env` 或 `config/llm.json` 配好回答模型。

```powershell
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe -m scripts.eval_answers `
  --golden-set '../docs/eval/golden-set-v3-hard.md' `
  --tokenizer 'data/tokenizers/bge-m3-5617a9f61b028005a4858fdac845db406aefb181.json' `
  --model 'bge-m3' `
  --dimensions 1024 `
  --top-k 5 `
  --strategy hybrid `
  --faithfulness `
  --pause 1.0 --cooldown 15.0 --max-attempts 3 `
  --output '../docs/eval/answers-v3-hybrid-2026-09-22.md'
```

## 逐题明细

| 题号 | 类别 | 判定 | 状态 | 标注出处名次 | 引用名次 | 错源 | 支持/不支持 | 首字 ms | 完整 ms | 答案开头 |
|---|---|---|---|---:|---|---|---|---:|---:|---|
| Q23 | Q | SUFFICIENT | ANSWERED | 1 | 1,2,3 | 否 | 3/0 | 2507 | 4225 | `redis-check-aof --fix` 用于检查并修复损坏的 AOF 文件，其过程为交互式修复，会提示将要截断的 |
| Q24 | Q | PARTIAL | PARTIAL | 1 | 1 | 否 | 1/0 | 2679 | 11906 | 根据提供的知识库片段，Linux 里的 `pdflush` 线程属于内核后台线程，其负责定期将缓冲区中的脏页写入磁盘（即 |
| Q25 | Q | SUFFICIENT | ANSWERED | 1 | 1 | 否 | 3/0 | 14692 | 24625 | activeExpireCycle 每秒的执行次数由参数 `hz` 控制。默认值为 10，即每秒执行 10 次 [1]。 |
| Q26 | Q | PARTIAL | PARTIAL | 1 | 1 | 否 | 5/1 | 2069 | 7090 | 基于提供的知识库片段，MQA 和 GQA 的区别及各自的代价如下：  **1. MQA 和 GQA 的区别** *    |
| Q27 | Q | SUFFICIENT | ANSWERED | 1 | 1 | 否 | 3/0 | 2761 | 4313 | epoll 的 ET 和 LT 的主要区别及默认模式如下：  1.  **LT（水平触发，默认）**：只要文件描述符（F |
| Q28 | Q | SUFFICIENT | ANSWERED | 1 | 1 | 否 | 4/2 | 5462 | 9902 | Redis 的 WATCH 命令用于实现**乐观锁**，其作用是监视一个或多个键 [1]。如果在执行 EXEC 之前，被 |
| Q29 | Q | SUFFICIENT | ANSWERED | 1 | 1,2 | 否 | 2/2 | 4530 | 8596 | 根据提供的知识库片段，删除 Docker 的 volume（数据卷）并不会自动删除容器，容器依然存在，但会导致容器无法访 |
| Q30 | Q | PARTIAL | PARTIAL | 1 | 1,2 | 否 | 6/1 | 3178 | 6377 | 根据提供的知识库片段，Cookie 和 Session 的区别及存储位置如下：  **区别：** *   **数据存储量 |
| Q31 | Q | SUFFICIENT | ANSWERED | 1 | 5 | 否 | 1/1 | 44141 | 44742 | 网络数据在真正发出去之前，先暂存在 **Socket Buffer（套接字缓冲区）** 中 [5]。该缓冲区用于网络通信 |
| Q32 | Q | SUFFICIENT | ANSWERED | 1 | 1,3 | 否 | 2/0 | 2940 | 3573 | Tomcat 不是数据库，它属于 Web 容器 [1]。Tomcat 和 Servlet 的关系是：Tomcat 用来部 |
| Q33 | Q | SUFFICIENT | ANSWERED | 1 | 1 | 否 | 5/0 | 14318 | 17410 | 使用 **pinia-plugin-persistedstate** 插件可以将 Pinia 的 state 持久化到  |
| Q34 | Q | SUFFICIENT | ANSWERED | 1 | 1 | 否 | 1/0 | 25035 | 25592 | 在提供的 Nginx 反向代理配置示例中，`proxy_pass` 后面写的是 `http://localhost:80 |
| P3 | P | SUFFICIENT | ANSWERED | — | 1,2 | — | 6/0 | 1971 | 14292 | 根据提供的知识库片段，关于 Docker 数据卷的备份方法及其注意事项回答如下：  **备份方法** 备份数据卷可以使用 |
| P4 | P | PARTIAL | PARTIAL | — | 1 | — | 2/0 | 5326 | 7471 | 根据提供的知识库片段，片段 [1] 中提到了 Linux 的 `pdflush` 作为内核后台线程用于定期将缓冲区中的脏 |
| P5 | P | PARTIAL | PARTIAL | — | 1 | — | 1/1 | 25751 | 27592 | 根据提供的知识库片段，**无法确定** pinia-plugin-persistedstate 是否支持将状态存到 In |
| P6 | P | PARTIAL | PARTIAL | — | 1,2,3 | — | 3/1 | 9230 | 13287 | 根据提供的知识库片段，无法回答“epoll 在 Windows 上怎么用”这个问题，因为片段中仅涉及 Linux 环境下 |
| R9 | R | SUFFICIENT | ANSWERED | 1 | 1,2,4 | 否 | 4/1 | 2794 | 4568 | 能救回来。根据知识库片段，损坏的 AOF 文件可以使用 `redis-check-aof --fix` 进行修复 [4] |
| R10 | R | SUFFICIENT | ANSWERED | 1 | 1,2,3 | 否 | 2/3 | 2149 | 3785 | 在 LangChain 中，想让模型只记住最近几轮对话，应使用 **`ConversationBufferWindowM |
| R11 | R | SUFFICIENT | ANSWERED | 1 | 2,5 | 否 | 7/3 | 3208 | 9901 | 根据提供的知识库片段，将一个普通 Python 函数变成模型能调用的工具（Tool）的方法如下：  1.  **使用 ` |

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

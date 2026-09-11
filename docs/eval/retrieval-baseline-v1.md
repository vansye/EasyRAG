# 单轮召回基线 v1（子 Issue B 验收）

> 运行时间：2026-09-07T23:15:51+08:00。由离线 CLI 生成，未调用 LLM。

## 验收边界

本报告只验证切片与向量索引的离线召回，不代表 Java 收录、更新同步、业务问答或模块 D 评估平台已完成。
不实现 Agent、改写、BM25、重排、拒答或部分覆盖判断；不读取 MySQL，不打开业务持久化 Chroma。

## 召回结果

| 类别 | 命中/题数 | hit@5 |
|---|---:|---:|
| Q | 16/16 | 100.00% |
| R | 6/6 | 100.00% |
| Q+R | 22/22 | 100.00% |

漏召回题：无。
N 类 7 题、P 类 1 题只展示召回，不进入上述分母，也不据此判定拒答或部分覆盖能力。

**计分口径**：Q/R 均使用原问题；前五个 chunk 任意一个来自唯一标注文档即命中。
不先按文档去重，不把同主题次要文档算正确，不把第六名计为命中。
命中文档不等于片段足以完整回答；本次不评估答案质量或引用忠实度。

## 参数与数据

- 模型：`bge-m3:latest`；请求名：`bge-m3`；维度：1024。
- Ollama 模型 digest：`7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab`。
- 端点：`http://127.0.0.1:11434`；batch_size=16；超时=180 秒；不重试。
- tokenizer 文件：`data/tokenizers/bge-m3-5617a9f61b028005a4858fdac845db406aefb181.json`；SHA-256：`21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08`。
- tokenizer 来源：https://huggingface.co/BAAI/bge-m3/resolve/5617a9f61b028005a4858fdac845db406aefb181/tokenizer.json。
- tokenizer 禁用 truncation/padding；Ollama `/api/embed` 明确传 `truncate=false`。
- 切片：前三层 ATX 标题；max_tokens=512；min_tokens=64；无重叠。
- token 计数包含特殊 token 与实际 embedding 输入：原文正文 + 换行 + 标题路径（无标题时只用正文）。
- 超长节按段落、句子、Unicode 码点硬切逐级回退；栅栏内部不识别标题、段落或句子边界，超长代码块才硬切。
- 短片段在不超上限时与相邻片段合并，跨节保留共同标题路径；原文中的标题仍保留。下限是合并目标，不是硬约束。
- `byte_start/byte_end` 为原文 UTF-8 字节偏移（左闭右开）；正文不规范化换行。Java/HTTP 跨语言契约尚未验收。
- 索引：独立 Chroma 内存 collection，cosine 距离，检索后只删除本次 collection；无阈值过滤。
- 语料：29 篇、143506 字节；222 个 chunk。
- 实际 chunk token 范围：64–490；低于软下限：0 个。
- 文档 embedding 输入合计 43892 token；全部查询合计 437 token。
- 语料指纹（按排序后的路径与原文 SHA-256 汇总）：`45f1bf18c58a1f84af9852cb50fcd61be5a589db41e096e6dd67f14449dc0b4b`。
- 黄金集原文件 SHA-256：`69b4e8f1a979305f33008c76a2bf45ea8aa5bc77ffe6b3e36ec11ab3239347e3`。

## 耗时

| 阶段 | 秒 |
|---|---:|
| 切片与准备 | 0.764 |
| 文档 embedding | 26.616 |
| 问题 embedding | 1.839 |
| 临时索引与检索 | 0.593 |
| 总计（不含 Python 导入和报告写入） | 29.923 |

耗时仅代表本机本次运行；未控制模型冷启动，查询 embedding 为批量处理，不能当作单请求延迟或 QPS。

## 复跑

在仓库的 `rag-service/` 目录执行；先安装 `requirements.txt`，确保本地 Ollama 已拉取所选模型。
每次完整重建临时索引，不复用旧向量。报告将写到显式指定的 `--output`。

```powershell
$env:PYTHONIOENCODING = 'utf-8'
New-Item -ItemType Directory -Force 'data/tokenizers' | Out-Null
Invoke-WebRequest -UseBasicParsing -Uri 'https://huggingface.co/BAAI/bge-m3/resolve/5617a9f61b028005a4858fdac845db406aefb181/tokenizer.json' -OutFile 'data/tokenizers/bge-m3-5617a9f61b028005a4858fdac845db406aefb181.json'
.\.venv\Scripts\python.exe -m scripts.eval_retrieval `
  --corpus '../sample-knowledge' `
  --golden-set '../docs/eval/golden-set-v1.md' `
  --tokenizer 'data/tokenizers/bge-m3-5617a9f61b028005a4858fdac845db406aefb181.json' `
  --ollama-url 'http://127.0.0.1:11434' `
  --model 'bge-m3' `
  --dimensions '1024' `
  --max-tokens '512' `
  --min-tokens '64' `
  --batch-size '16' `
  --timeout-seconds '180' `
  --output '../docs/eval/retrieval-baseline-v1.md'
```

## 逐题 Top-5

相似度 = 1 − cosine distance，仅作排序诊断，不代表置信度。位置以原始文件的 UTF-8 字节为单位。

### Q1 · HIT@1

什么是 ACID？数据库事务的四个特性分别是什么？

标注出处：`数据库/知识条目/ACID.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/ACID.md | 2 | 详细 > 概念 | 333:662 | 90 | 0.823166 |
| 2 | 数据库/知识条目/ACID.md | 1 |  | 0:333 | 89 | 0.815235 |
| 3 | 数据库/知识条目/ACID.md | 3 | 详细 > 重点 | 662:1828 | 299 | 0.717762 |
| 4 | 数据库/知识条目/Redis 事务.md | 2 | 详细 > 概念 | 327:601 | 68 | 0.613754 |
| 5 | 数据库/知识条目/Redis 事务.md | 1 |  | 0:327 | 91 | 0.583567 |

### Q2 · HIT@1

KV Cache 是什么？它解决什么问题？

标注出处：`大模型应用/知识条目/KV Cache.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/KV Cache.md | 1 |  | 0:689 | 187 | 0.695766 |
| 2 | 大模型应用/知识条目/KV Cache.md | 2 | KV Cache（键值缓存）深度知识概览 | 689:1481 | 233 | 0.651958 |
| 3 | 大模型应用/知识条目/KV Cache.md | 5 | KV Cache（键值缓存）深度知识概览 > 四、 内存开销：巨大的“吞金兽”（显存瓶颈） | 2147:2693 | 215 | 0.638092 |
| 4 | 大模型应用/知识条目/KV Cache.md | 6 | KV Cache（键值缓存）深度知识概览 > 五、 针对 KV Cache 的顶尖优化策略 | 2693:2817 | 64 | 0.634857 |
| 5 | 大模型应用/知识条目/KV Cache.md | 8 | KV Cache（键值缓存）深度知识概览 > 五、 针对 KV Cache 的顶尖优化策略 > 2. PagedAttention（分页注意力）—— vLLM 框架的核心 | 3256:3651 | 154 | 0.633377 |

### Q3 · HIT@1

Redis 的持久化方式有哪两种？各有什么特点？

标注出处：`数据库/Redis/Redis持久化.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/Redis/Redis持久化.md | 29 | 小结 | 15531:16194 | 207 | 0.705213 |
| 2 | 数据库/Redis/Redis持久化.md | 1 |  | 0:333 | 97 | 0.704504 |
| 3 | 数据库/Redis/Redis持久化.md | 3 | 1. 为什么需要持久化 > 1.1 Redis 的内存特性 | 879:1754 | 224 | 0.679790 |
| 4 | 数据库/Redis/Redis持久化.md | 2 |  | 333:879 | 190 | 0.668889 |
| 5 | 数据库/Redis/Redis持久化.md | 16 | 4. 混合持久化 > 4.1 什么是混合持久化 | 8950:9831 | 231 | 0.655764 |

### Q4 · HIT@1

事务的隔离级别有哪些？哪个并发性能最差？

标注出处：`数据库/知识条目/ACID.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/ACID.md | 3 | 详细 > 重点 | 662:1828 | 299 | 0.557580 |
| 2 | 数据库/知识条目/ACID.md | 1 |  | 0:333 | 89 | 0.543590 |
| 3 | 数据库/Redis/Redis持久化.md | 15 |  | 8355:8950 | 199 | 0.539509 |
| 4 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 4 | 详细 > 重点 | 1599:2347 | 235 | 0.530309 |
| 5 | 数据库/知识条目/缓存.md | 3 | 详细 > 重点 | 716:2266 | 423 | 0.523142 |

### Q5 · HIT@1

Docker 镜像和容器是什么关系？

标注出处：`JavaWeb/知识条目/部署/Docker/Docker 镜像.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 1 |  | 0:294 | 88 | 0.772608 |
| 2 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 2 | 详细 > 概念 | 294:651 | 90 | 0.730206 |
| 3 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 3 | 详细 > 重点 | 651:1758 | 344 | 0.644402 |
| 4 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 1 |  | 0:534 | 134 | 0.638566 |
| 5 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 2 | 详细 > 重点 | 534:1831 | 436 | 0.585898 |

### Q6 · HIT@1

LCEL 是什么？

标注出处：`大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md | 2 | 详细 > 概念 | 255:960 | 193 | 0.686714 |
| 2 | 大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md | 3 | 详细 > 重点 | 960:2513 | 452 | 0.597743 |
| 3 | 大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md | 1 |  | 0:255 | 77 | 0.539079 |
| 4 | 大模型应用/知识条目/Runnable 协议.md | 2 | 详细 > 概念 | 244:814 | 155 | 0.482474 |
| 5 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 2 | 详细 > 概念 | 404:650 | 65 | 0.442179 |

### Q7 · HIT@1

Prompt Template 是什么？为什么需要它？

标注出处：`大模型应用/知识条目/PromptTemplate 提示词模板.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/PromptTemplate 提示词模板.md | 2 | 详细 > 概念 | 280:852 | 161 | 0.691934 |
| 2 | 大模型应用/知识条目/PromptTemplate 提示词模板.md | 3 | 详细 > 重点 | 852:2360 | 417 | 0.649102 |
| 3 | 大模型应用/知识条目/PromptTemplate 提示词模板.md | 1 |  | 0:280 | 88 | 0.593429 |
| 4 | 大模型应用/知识条目/invoke、stream 与 batch.md | 3 | 详细 > 重点 | 858:2229 | 390 | 0.522371 |
| 5 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 15 | 6. 高级技巧：摘要压缩与选择性历史 > 6.1 自定义摘要触发时机 | 9850:11185 | 412 | 0.514183 |

### Q8 · HIT@1

Pinia 是什么？它管理什么？

标注出处：`JavaWeb/Web前端设计/Pinia状态管理.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/Web前端设计/Pinia状态管理.md | 29 | 小结 | 18739:19599 | 252 | 0.636442 |
| 2 | JavaWeb/Web前端设计/Pinia状态管理.md | 4 | 2. Pinia 简介 | 2283:2676 | 127 | 0.598280 |
| 3 | JavaWeb/Web前端设计/Pinia状态管理.md | 1 |  | 0:549 | 139 | 0.596241 |
| 4 | JavaWeb/Web前端设计/Pinia状态管理.md | 2 | 目录 | 549:1468 | 344 | 0.552889 |
| 5 | JavaWeb/Web前端设计/Pinia状态管理.md | 28 | 10. 常见用法总结 > 10.4 响应式追踪 | 18447:18739 | 105 | 0.528078 |

### Q9 · HIT@1

Docker 数据卷是什么？容器删了数据还在吗？

标注出处：`JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 1 |  | 0:534 | 134 | 0.808238 |
| 2 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 2 | 详细 > 重点 | 534:1831 | 436 | 0.670576 |
| 3 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 2 | 详细 > 概念 | 294:651 | 90 | 0.584446 |
| 4 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 1 |  | 0:294 | 88 | 0.575671 |
| 5 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 3 | 详细 > 重点 | 651:1758 | 344 | 0.540038 |

### Q10 · HIT@1

什么是 Bean？

标注出处：`JavaWeb/知识条目/后端/Bean.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/后端/Bean.md | 1 |  | 0:407 | 104 | 0.643155 |
| 2 | JavaWeb/知识条目/后端/Bean.md | 2 | 详细 > 重点 | 407:691 | 80 | 0.642991 |
| 3 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 3 | Interceptor 拦截器技术 > 什么是 Interceptor > 核心特点 | 1171:1560 | 128 | 0.478988 |
| 4 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 20 | Interceptor 拦截器技术 > Interceptor 与 Filter 的区别 | 13232:13963 | 277 | 0.436934 |
| 5 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 21 | Interceptor 拦截器技术 > Interceptor 与 Filter 的区别 > 选择建议 | 13963:14494 | 202 | 0.402784 |

### Q11 · HIT@1

URL 编码是什么？为什么要编码？

标注出处：`JavaWeb/知识条目/后端/URL编码.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/后端/URL编码.md | 1 |  | 0:608 | 176 | 0.795597 |
| 2 | JavaWeb/知识条目/后端/Cookie 和 Session.md | 1 |  | 0:538 | 142 | 0.523826 |
| 3 | 大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md | 2 | 详细 > 概念 | 255:960 | 193 | 0.518135 |
| 4 | JavaWeb/知识条目/后端/SQL注入.md | 1 |  | 0:569 | 156 | 0.514230 |
| 5 | 大模型应用/知识条目/Runnable 协议.md | 2 | 详细 > 概念 | 244:814 | 155 | 0.511143 |

### Q12 · HIT@1

Redis 内存不足时怎么淘汰数据？有哪些策略？

标注出处：`数据库/知识条目/Redis 内存淘汰.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/Redis 内存淘汰.md | 1 |  | 0:368 | 106 | 0.796852 |
| 2 | 数据库/知识条目/Redis 内存淘汰.md | 2 | 详细 > 概念 | 368:617 | 70 | 0.763237 |
| 3 | 数据库/Redis过期Key处理.md | 6 |  | 2603:3232 | 177 | 0.678804 |
| 4 | 数据库/Redis过期Key处理.md | 18 | 6. 过期 Key 的内存回收 > 6.2 内存池 vs OS 内存 | 8973:9506 | 173 | 0.678283 |
| 5 | 数据库/Redis/Redis持久化.md | 3 | 1. 为什么需要持久化 > 1.1 Redis 的内存特性 | 879:1754 | 224 | 0.663875 |

### Q13 · HIT@1

拦截器和过滤器有什么区别？分别执行在哪个阶段？

标注出处：`JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 20 | Interceptor 拦截器技术 > Interceptor 与 Filter 的区别 | 13232:13963 | 277 | 0.684574 |
| 2 | JavaWeb/知识条目/后端/Interceptor拦截器.md | 1 |  | 0:537 | 149 | 0.667939 |
| 3 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 2 | Interceptor 拦截器技术 > 什么是 Interceptor | 668:1171 | 150 | 0.660973 |
| 4 | JavaWeb/知识条目/后端/Interceptor拦截器.md | 2 | 详细 > 重点 | 537:812 | 85 | 0.635662 |
| 5 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 1 | Interceptor 拦截器技术 | 0:668 | 213 | 0.627936 |

### Q14 · HIT@1

Nginx 反向代理是什么？和正向代理的区别？

标注出处：`JavaWeb/Web前端设计/Nginx 反向代理服务器.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/Web前端设计/Nginx 反向代理服务器.md | 1 |  | 0:406 | 118 | 0.725610 |
| 2 | JavaWeb/Web前端设计/Nginx 反向代理服务器.md | 3 | 详细 > 重点 | 680:1816 | 325 | 0.714998 |
| 3 | JavaWeb/Web前端设计/Nginx 反向代理服务器.md | 2 | 详细 > 概念 | 406:680 | 79 | 0.673097 |
| 4 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 17 | 小结 | 14357:15110 | 233 | 0.498722 |
| 5 | 数据库/知识条目/缓存.md | 3 | 详细 > 重点 | 716:2266 | 423 | 0.489769 |

### Q15 · HIT@1

create_agent 是干什么的？它取代了旧版的哪个函数？

标注出处：`大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 17 | 小结 | 14357:15110 | 233 | 0.718172 |
| 2 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 1 |  | 0:445 | 122 | 0.699378 |
| 3 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 3 |  | 1511:2407 | 337 | 0.681377 |
| 4 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 2 | 目录 | 445:1511 | 382 | 0.652931 |
| 5 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 14 | 5. 完整实战：多工具 Agent | 8338:9407 | 295 | 0.596051 |

### Q16 · HIT@1

大模型“开卷考试”是什么意思？

标注出处：`大模型应用/知识条目/Rag.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/Rag.md | 1 |  | 0:607 | 157 | 0.537299 |
| 2 | 大模型应用/知识条目/Rag.md | 2 | RAG（检索增强生成）知识概览 > 二、 为什么需要 RAG？（价值与痛点） | 607:1331 | 241 | 0.467315 |
| 3 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 1 |  | 0:374 | 106 | 0.450537 |
| 4 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 1 |  | 0:534 | 134 | 0.442614 |
| 5 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 7 | 3. 核心挑战：上下文膨胀 > 3.1 Token 增长模型 | 4286:4572 | 109 | 0.430878 |

### N1 · 未评分

Redis 的 GEO 命令怎么用？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/Redis 事务.md | 1 |  | 0:327 | 91 | 0.611671 |
| 2 | 数据库/知识条目/Redis 事务.md | 3 | 详细 > 重点 | 601:2137 | 447 | 0.569055 |
| 3 | 数据库/知识条目/Redis 事务.md | 2 | 详细 > 概念 | 327:601 | 68 | 0.563771 |
| 4 | 数据库/Redis过期Key处理.md | 12 | 4. 定期删除详解 > 4.3 可调参数 | 5880:6391 | 156 | 0.563662 |
| 5 | 数据库/Redis/Redis持久化.md | 3 | 1. 为什么需要持久化 > 1.1 Redis 的内存特性 | 879:1754 | 224 | 0.558230 |

### N2 · 未评分

Spring Boot 的 GraalVM 原生镜像怎么配置？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 3 | 详细 > 重点 | 651:1758 | 344 | 0.488008 |
| 2 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 1 |  | 0:294 | 88 | 0.487100 |
| 3 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 2 | 详细 > 概念 | 294:651 | 90 | 0.473504 |
| 4 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 14 | Interceptor 拦截器技术 > 代码示例：token 登录拦截器 > 对应的 `WebConfig.java` | 10045:10870 | 266 | 0.457713 |
| 5 | 数据库/Redis/Redis持久化.md | 17 | 4. 混合持久化 > 4.2 配置 | 9831:10045 | 89 | 0.447657 |

### N3 · 未评分

Kubernetes 的 StatefulSet 和 Deployment 有什么区别？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/后端/Tomcat.md | 2 | 详细 > 重点 | 433:625 | 65 | 0.519417 |
| 2 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 3 | 详细 > 重点 | 650:1599 | 298 | 0.514591 |
| 3 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 4 | 详细 > 重点 | 1599:2347 | 235 | 0.508600 |
| 4 | JavaWeb/知识条目/前端/onMounted生命周期函数.md | 3 | 详细 > 重点 | 594:1593 | 322 | 0.498015 |
| 5 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 2 | 详细 > 重点 | 534:1831 | 436 | 0.487968 |

### N4 · 未评分

Vue 3 的 Teleport 特性怎么用？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/前端/onMounted生命周期函数.md | 3 | 详细 > 重点 | 594:1593 | 322 | 0.508090 |
| 2 | JavaWeb/Web前端设计/Pinia状态管理.md | 29 | 小结 | 18739:19599 | 252 | 0.497480 |
| 3 | JavaWeb/知识条目/前端/onMounted生命周期函数.md | 1 |  | 0:367 | 105 | 0.493122 |
| 4 | JavaWeb/知识条目/前端/onMounted生命周期函数.md | 2 | 详细 > 概念 | 367:594 | 74 | 0.483171 |
| 5 | JavaWeb/Web前端设计/Nginx 反向代理服务器.md | 3 | 详细 > 重点 | 680:1816 | 325 | 0.474975 |

### N5 · 未评分

MySQL 的窗口函数 ROW_NUMBER() 怎么用？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/Redis 事务.md | 3 | 详细 > 重点 | 601:2137 | 447 | 0.530133 |
| 2 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 12 | 5. LangChain Memory 组件 > 5.2 ConversationBufferWindowMemory（滑动窗口） | 7952:8409 | 152 | 0.510216 |
| 3 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 4 | 详细 > 重点 | 1599:2347 | 235 | 0.508811 |
| 4 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 17 |  | 11769:12878 | 316 | 0.506528 |
| 5 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 21 | 小结 | 14949:15618 | 196 | 0.506074 |

### N6 · 未评分

LangChain 的 Graph 模块如何构建状态图？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md | 3 | 详细 > 重点 | 960:2513 | 452 | 0.565766 |
| 2 | 大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md | 2 | 详细 > 概念 | 255:960 | 193 | 0.561871 |
| 3 | 大模型应用/知识条目/invoke、stream 与 batch.md | 2 | 详细 > 概念 | 270:858 | 156 | 0.561138 |
| 4 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 17 | 小结 | 14357:15110 | 233 | 0.554119 |
| 5 | 大模型应用/知识条目/PromptTemplate 提示词模板.md | 2 | 详细 > 概念 | 280:852 | 161 | 0.549034 |

### N7 · 未评分

我去年三月在上海买了什么？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 7 | 3. Agent 的输入：`invoke()` 接收什么 > 3.2 输入形态二：消息列表 | 5159:5612 | 148 | 0.443176 |
| 2 | JavaWeb/Web前端设计/Pinia状态管理.md | 11 | 4. 使用 Store > 4.1 导入 Store | 6936:7139 | 74 | 0.439501 |
| 3 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 15 | 5. 完整实战：多工具 Agent | 9407:10595 | 376 | 0.433616 |
| 4 | JavaWeb/Web前端设计/Pinia状态管理.md | 18 | 6. 状态持久化 > 6.2 使用 pinia-plugin-persistedstate（推荐） | 12347:13213 | 291 | 0.415140 |
| 5 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 4 | 2. `create_agent` 参数详解 > 2.1 必选参数 | 2407:3300 | 310 | 0.408254 |

### P1 · 未评分

什么是 Transformer 的多头注意力机制？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/KV Cache.md | 7 | KV Cache（键值缓存）深度知识概览 > 五、 针对 KV Cache 的顶尖优化策略 > 1. 多查询注意力（MQA）与分组查询注意力（GQA） | 2817:3256 | 182 | 0.544614 |
| 2 | 大模型应用/知识条目/KV Cache.md | 4 | KV Cache（键值缓存）深度知识概览 > 三、 工作机制（底层原理解析） > 2. 注意力计算的变化 | 1918:2147 | 113 | 0.520229 |
| 3 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 1 |  | 0:374 | 106 | 0.481036 |
| 4 | 大模型应用/知识条目/KV Cache.md | 10 | KV Cache（键值缓存）深度知识概览 > 五、 针对 KV Cache 的顶尖优化策略 > 4. FlashAttention / FlashDecoding | 3941:4131 | 92 | 0.474104 |
| 5 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 21 | 小结 | 14949:15618 | 196 | 0.465243 |

### R1 · HIT@2

大模型推理时为什么速度慢？有什么优化手段？

标注出处：`大模型应用/知识条目/KV Cache.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/Rag.md | 2 | RAG（检索增强生成）知识概览 > 二、 为什么需要 RAG？（价值与痛点） | 607:1331 | 241 | 0.606567 |
| 2 | 大模型应用/知识条目/KV Cache.md | 5 | KV Cache（键值缓存）深度知识概览 > 四、 内存开销：巨大的“吞金兽”（显存瓶颈） | 2147:2693 | 215 | 0.584081 |
| 3 | 大模型应用/知识条目/Rag.md | 5 | RAG（检索增强生成）知识概览 > 五、 关键技术难点与落地挑战 | 3660:4442 | 241 | 0.583671 |
| 4 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 8 |  | 4572:5081 | 170 | 0.576021 |
| 5 | 大模型应用/知识条目/KV Cache.md | 9 | KV Cache（键值缓存）深度知识概览 > 五、 针对 KV Cache 的顶尖优化策略 > 3. 连续批处理（Continuous Batching） | 3651:3941 | 116 | 0.555786 |

### R2 · HIT@1

服务器断电了，Redis 数据会不会丢？

标注出处：`数据库/Redis/Redis持久化.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/Redis/Redis持久化.md | 1 |  | 0:333 | 97 | 0.683895 |
| 2 | 数据库/Redis/Redis持久化.md | 22 | 6. 生产环境配置方案 > 6.2 方案二：AOF only（数据敏感场景） | 11986:12352 | 139 | 0.633204 |
| 3 | 数据库/Redis/Redis持久化.md | 3 | 1. 为什么需要持久化 > 1.1 Redis 的内存特性 | 879:1754 | 224 | 0.629197 |
| 4 | 数据库/Redis/Redis持久化.md | 26 | 7. 故障恢复与数据一致性 > 7.2 异常宕机后的数据状态 | 14062:14530 | 154 | 0.623148 |
| 5 | 数据库/Redis过期Key处理.md | 1 |  | 0:336 | 96 | 0.618940 |

### R3 · HIT@1

页面刷新后 Pinia 里的状态还在吗？

标注出处：`JavaWeb/Web前端设计/Pinia状态管理.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/Web前端设计/Pinia状态管理.md | 28 | 10. 常见用法总结 > 10.4 响应式追踪 | 18447:18739 | 105 | 0.657494 |
| 2 | JavaWeb/Web前端设计/Pinia状态管理.md | 18 | 6. 状态持久化 > 6.2 使用 pinia-plugin-persistedstate（推荐） | 12347:13213 | 291 | 0.645438 |
| 3 | JavaWeb/Web前端设计/Pinia状态管理.md | 29 | 小结 | 18739:19599 | 252 | 0.612851 |
| 4 | JavaWeb/Web前端设计/Pinia状态管理.md | 16 |  | 11035:11714 | 202 | 0.608936 |
| 5 | JavaWeb/Web前端设计/Pinia状态管理.md | 1 |  | 0:549 | 139 | 0.608667 |

### R4 · HIT@1

怎么防止数据库被 SQL 注入攻击？

标注出处：`JavaWeb/知识条目/后端/SQL注入.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/后端/SQL注入.md | 1 |  | 0:569 | 156 | 0.720631 |
| 2 | 数据库/知识条目/ACID.md | 3 | 详细 > 重点 | 662:1828 | 299 | 0.589015 |
| 3 | 数据库/Redis/Redis持久化.md | 22 | 6. 生产环境配置方案 > 6.2 方案二：AOF only（数据敏感场景） | 11986:12352 | 139 | 0.567059 |
| 4 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 4 | 详细 > 重点 | 1599:2347 | 235 | 0.547097 |
| 5 | 数据库/知识条目/缓存.md | 3 | 详细 > 重点 | 716:2266 | 423 | 0.545904 |

### R5 · HIT@1

Docker 容器一删就什么都没了吗？有办法保留数据吗？

标注出处：`JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 1 |  | 0:534 | 134 | 0.687462 |
| 2 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 2 | 详细 > 重点 | 534:1831 | 436 | 0.670580 |
| 3 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 3 | 详细 > 重点 | 651:1758 | 344 | 0.588635 |
| 4 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 1 |  | 0:294 | 88 | 0.572595 |
| 5 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 2 | 详细 > 概念 | 294:651 | 90 | 0.559430 |

### R6 · HIT@1

Redis 和数据库怎么配合？缓存不一致怎么处理？

标注出处：`数据库/知识条目/缓存.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/缓存.md | 3 | 详细 > 重点 | 716:2266 | 423 | 0.707462 |
| 2 | 数据库/Redis/Redis持久化.md | 1 |  | 0:333 | 97 | 0.691415 |
| 3 | 数据库/Redis/Redis持久化.md | 23 | 6. 生产环境配置方案 > 6.3 方案三：混合持久化（生产推荐 ⭐） | 12352:12808 | 165 | 0.683326 |
| 4 | 数据库/知识条目/Redis 事务.md | 2 | 详细 > 概念 | 327:601 | 68 | 0.675699 |
| 5 | 数据库/Redis/Redis持久化.md | 22 | 6. 生产环境配置方案 > 6.2 方案二：AOF only（数据敏感场景） | 11986:12352 | 139 | 0.672487 |

## 语料指纹

| 相对路径 | 字节数 | chunks | SHA-256 |
|---|---:|---:|---|
| JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 16437 | 24 | `87749f86445988adae93a30b76488cebb1bb68e81514ed253271ca8288685f20` |
| JavaWeb/Web前端设计/Nginx 反向代理服务器.md | 1816 | 3 | `8a33a2bfaf199b8bd90e5541d71a854cc3d48ff72559a26fa68e2fb097a225a3` |
| JavaWeb/Web前端设计/Pinia状态管理.md | 19599 | 29 | `e73b37265dbc82196434160adb091b79f2748d47d6f5b774556c76504669edb5` |
| JavaWeb/知识条目/前端/onMounted生命周期函数.md | 1593 | 3 | `0e9504f20fd3f81ddeec509c45c4dbf62037d5adf524a647e9402c83f5596c64` |
| JavaWeb/知识条目/后端/Bean.md | 691 | 2 | `e45a0f52c8b998f18bf6a6a92940cdd31c4a07747500ce71304bee513d10dcb1` |
| JavaWeb/知识条目/后端/Cookie 和 Session.md | 863 | 2 | `e5b1779896ddd0a9d80b15a0a25cde218553bd79a868e36d4ab7bb403367141f` |
| JavaWeb/知识条目/后端/Interceptor拦截器.md | 812 | 2 | `9007f3253503c96de5e3b2c2e46938371f950f4e78ec816dc0edbf3b434da7b2` |
| JavaWeb/知识条目/后端/SQL注入.md | 569 | 1 | `ff7b18b2e284ee71667cfd956b24bdbbf8ca9789de9a60399126ad0d64504d70` |
| JavaWeb/知识条目/后端/Tomcat.md | 625 | 2 | `fd371bf282c1aa098d13ee043c11b7d5dfc2696b44b800f315a57d569afacd11` |
| JavaWeb/知识条目/后端/URL编码.md | 608 | 1 | `2a6692807c8f61c31bf8ff2ac047467c9adadd1b613492f292633fa03684658f` |
| JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 1831 | 2 | `53f2ecb2a506fb56b3e4ecea0a1eabe30a78780d28012c3b4d4323bd1388ef9b` |
| JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 1758 | 3 | `d2cf76c715096dc659822e90dc69cecf6f7cad93fcf2428b834ffb78f5719618` |
| 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 15110 | 17 | `c8ac44613d81729364498645185b3a5b27c8ce472f1c8a40e0a123c0fc4c31a7` |
| 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 15618 | 21 | `d4e762742fe7ff4ecb4bc1c8ead2d0f66f7766012277cd7b8f11a8973a595baf` |
| 大模型应用/知识条目/invoke、stream 与 batch.md | 2229 | 3 | `57c42a0b334f970550b1141dd9e3847b1f3a098bbb10c8c77df93665aeecbb54` |
| 大模型应用/知识条目/KV Cache.md | 5453 | 12 | `3024164b74db364ea6b965d6ac840ec861760cd412e8269aa40238ae41cbcca7` |
| 大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md | 2513 | 3 | `c2500f0ebd6fe78787cd1250376a4c7b25f4d247df57a7d3289527227db530b1` |
| 大模型应用/知识条目/PromptTemplate 提示词模板.md | 2360 | 3 | `3798dbdc10ca4290e476bf8c5fff9172181c44d397b0ac17ac011f42a2ad5e0c` |
| 大模型应用/知识条目/Rag.md | 5975 | 8 | `8b3c597abadda7bf626e7a93c0eb1cc7f43865f0e03741c95934c3b93dbc09da` |
| 大模型应用/知识条目/Runnable 协议.md | 2121 | 3 | `00668c64bd76af2e97e9ac391a396be87c0200fe3dbcb47cd23eb64f632f169b` |
| 大模型应用/知识条目/Tools.md | 2377 | 3 | `f7cda9142d9e35e2a3f16859446f1167f2953a630c49e59d7211fc7112dfaf98` |
| 数据库/Redis/Redis持久化.md | 16194 | 29 | `46b8ea541b37391e1d8ac6f8b006a99cc7dd765fc4d6c6b981bb8e328a69f0fd` |
| 数据库/Redis过期Key处理.md | 13311 | 26 | `813de0879125a8f395fefccb7b2066bc7fd1b5307001b64d2bf5459d22b00552` |
| 数据库/知识条目/ACID.md | 1828 | 3 | `ee705778aedcc275386c0df22032fcde404cbb0c949a64807fec65151832abb8` |
| 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 2347 | 4 | `5e38d29f4a4e8d0461e01503bbb96ee6fe9bdfb2564c897637ed757c9774c911` |
| 数据库/知识条目/Redis 事务.md | 2137 | 3 | `74b8377552bc5a910e112b7281ff9846ec0f21ee26391aedd87e707409afb778` |
| 数据库/知识条目/Redis 内存淘汰.md | 2584 | 4 | `a5c677818e0e8f4254a06691b163a38e7066e486ac4b93950d4bc5786e8f17b4` |
| 数据库/知识条目/操作系统缓冲区（OS Buffer）.md | 1881 | 3 | `70749d86fc4a9af2906a539a351743b78311a597eb5fd28f6ba5174feb304f4a` |
| 数据库/知识条目/缓存.md | 2266 | 3 | `0206b502b3b4294fba74082c53b74641dc64d73104d24b8c35c1f17dddd9d6b9` |

## 环境与代码指纹

- Python: `3.14.6`
- Ollama: `0.30.10`
- chromadb: `1.5.9`
- tokenizers: `0.23.2`
- httpx: `0.28.1`

- `app/chunking.py` SHA-256: `f7bcddbb84c5ae8699f8ff92f6217db316300eddc81a0b7aa6256a9f3ccb3601`
- `scripts/eval_retrieval.py` SHA-256: `469a9a3a14141db7ff4af02ffa70e76127a0782060f00e576560a7b51f3aea0f`
- `requirements.txt` SHA-256: `c66f5ea3ff5bd6d42a821e837183a6883ee2c157c1c045fd1f69265ebe347b25`

## 本轮复核与解读

本节为运行后的复核记录；CLI 再次生成报告会覆盖本节，届时应根据新结果重新复核，而不是沿用旧结论。

- 同一语料完整执行三次，每次新建临时索引，三次均为 Q=16/16、R=6/6。最后一次包含 Unicode 栅栏解析修正，本文件保存最后一次结果与代码指纹。
- 从逐题记录另行核算：hit@1 为 Q=16/16、R=5/6，合计 **21/22（95.45%）**。R1 是唯一未首位命中的正例，标注的 `KV Cache.md` 排第 2；hit@5 没有漏召回。
- 30 题的 150 条召回记录已逐项核对：字节范围有效、可从原文件按 UTF-8 解码、片段非空、记录的 token 数不超过 512；报告内代码 SHA-256 与最终文件一致。
- 满分仅针对这个小集合：29 篇语料、22 道正例、17 个标注出处。命中文档不等于片段覆盖全部答案，也不说明所有召回片段都相关，更不能推断库外拒答或生成质量已通过。
- 六道“需改写”题用原问题也全部命中 top-5。因此现有数据不能证明应优先引入改写、Agent 或 BM25；后续收益必须用新增难例和对照实验说明，不为技术选型制造需求。

## 工程验证

- 最终 `python -m pytest -q`：**79 passed**，其中本轮新增切片 24 项、离线验收 13 项，原有 42 项保持通过。
- `python -m pip check`：`No broken requirements found`。
- 保留 Chroma 与 Starlette 的两条原有弃用警告，未改动无关依赖。
- 只读审查发现的两类 Unicode 栅栏错误均先由测试复现后修正：NBSP 不能充当关闭栅栏后的空白，Unicode 行分隔符不能制造 Markdown 新行；CR、LF、CRLF 仍保留并识别。
- 模拟 HTTP 的工程测试不作为质量分数依据；本报告召回数字来自真实本地 `bge-m3`。

## 六文件改动边界

| 文件 | 改动原因与影响范围 |
|---|---|
| `rag-service/app/chunking.py` | 新增 `split_markdown`：标题与栅栏解析、完整输入 token 预算、相邻合并、原文 UTF-8 字节定位；不增加 HTTP 接口。 |
| `rag-service/scripts/eval_retrieval.py` | 新增子 Issue B 离线验收 CLI：语料切片、本地 embedding、隔离 Chroma、严格逐题评分与报告；不接 Java/MySQL/LLM。 |
| `rag-service/tests/test_chunking.py` | 新增切片边界、Unicode、长度与原文还原回归测试。 |
| `rag-service/tests/test_eval_retrieval.py` | 新增评分分母、截断开关、向量数量/维度、索引隔离及完整 CLI 测试。 |
| `rag-service/requirements.txt` | 将已有 `tokenizers==0.23.2` 锁定为直接依赖，不升级其他包。 |
| `docs/eval/retrieval-baseline-v1.md` | 保存真实基线、逐题 Top-5、复跑命令、版本与数据指纹，以及本轮复核。 |

tokenizer 缓存与测试临时文件属于已忽略的运行产物；业务 Chroma、Java、黄金集标注及其他既有改动均未改写。未执行 commit 或 push。

## 下一步（仍属子 Issue B）

先明确分批 `/embed` 的替换/追加语义及跨语言定位契约，再接入正式切片、索引写入、按文档删除与重建接口。特别要避免“每一批都先删整篇旧索引”导致前一批刚写入的向量被删除。

这是下一轮的工作，本轮仅交付离线召回验收基线，不宣称子 Issue B 全部完成。

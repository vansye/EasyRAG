# 离线召回验收报告（子 Issue B）

> 运行时间：2026-09-22T10:48:14+08:00。由离线 CLI 生成，未调用 LLM。

## 验收边界

本报告只验证切片与索引的离线召回；资料收录、更新同步、业务问答与完整评估平台需各自的验收证据。
不实现 Agent、改写、重排、拒答或部分覆盖判断；不读取 MySQL，不打开业务持久化 Chroma。
检索策略：hybrid——向量与 BM25 各取前 20 个候选做倒数排名融合（RRF，k=60），与服务端 B-17 共用同一套函数；相似度列为向量余弦，只决定展示，不决定排序。

## 召回结果

| 类别 | hit@1 | hit@3 | hit@5 | hit@10 |
|---|---:|---:|---:|---:|
| Q | 16/16（100.00%） | 16/16（100.00%） | 16/16（100.00%） | 16/16（100.00%） |
| R | 6/6（100.00%） | 6/6（100.00%） | 6/6（100.00%） | 6/6（100.00%） |
| Q+R | 22/22（100.00%） | 22/22（100.00%） | 22/22（100.00%） | 22/22（100.00%） |

漏召回题（前 10 均未命中）：无。
命中但未进前 5：无。
N 类 7 题、P 类 1 题只展示召回，不进入上述分母，也不据此判定拒答或部分覆盖能力。

**计分口径**：Q/R 均使用原问题；hit@k = 前 k 个 chunk 中任意一个来自唯一标注文档，k ∈ {1, 3, 5, 10}。
不先按文档去重，不把同主题次要文档算正确；各 k 独立计分，排名超过该 k 不计入。
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
- `byte_start/byte_end` 为原文 UTF-8 字节偏移（左闭右开）；正文不规范化换行。HTTP 定位契约由独立的资料管理与接口测试验证。
- 索引：独立 Chroma 内存 collection，cosine 距离，检索后只删除本次 collection；无阈值过滤。
- 策略：hybrid；hybrid 候选深度：20。
- 语料：29 篇、146829 字节；222 个 chunk。
- 实际 chunk token 范围：64–490；低于软下限：0 个。
- 文档 embedding 输入合计 43894 token；全部查询合计 437 token。
- 语料指纹（按排序后的路径与原文 SHA-256 汇总）：`3d4b803355606400354a4b4f73f4fde54fc92e352f43ac80e1dd60ffda123425`。
- 黄金集原文件 SHA-256：`69b4e8f1a979305f33008c76a2bf45ea8aa5bc77ffe6b3e36ec11ab3239347e3`。

## 耗时

| 阶段 | 秒 |
|---|---:|
| 切片与准备 | 0.613 |
| 文档 embedding | 17.247 |
| 问题 embedding | 1.833 |
| 临时索引与检索 | 1.045 |
| 总计（不含 Python 导入和报告写入） | 20.820 |

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
  --strategy 'hybrid' `
  --candidates '20' `
  --output '../docs/eval/retrieval-v1-hybrid-2026-09-22.md'
```

## 逐题明细（检索深度 10，展示前 5）

相似度 = 1 − cosine distance，仅作排序诊断，不代表置信度。位置以原始文件的 UTF-8 字节为单位。

### Q1 · HIT@1

什么是 ACID？数据库事务的四个特性分别是什么？

标注出处：`数据库/知识条目/ACID.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/ACID.md | 2 | 详细 > 概念 | 345:677 | 90 | 0.823166 |
| 2 | 数据库/知识条目/ACID.md | 1 |  | 0:345 | 89 | 0.815235 |
| 3 | 数据库/知识条目/ACID.md | 3 | 详细 > 重点 | 677:1848 | 299 | 0.717762 |
| 4 | 数据库/知识条目/Redis 事务.md | 2 | 详细 > 概念 | 344:621 | 68 | 0.613754 |
| 5 | 数据库/知识条目/Redis 事务.md | 1 |  | 0:344 | 91 | 0.583567 |

### Q2 · HIT@1

KV Cache 是什么？它解决什么问题？

标注出处：`大模型应用/知识条目/KV Cache.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/KV Cache.md | 1 |  | 0:708 | 187 | 0.695766 |
| 2 | 大模型应用/知识条目/KV Cache.md | 6 | KV Cache（键值缓存）深度知识概览 > 五、 针对 KV Cache 的顶尖优化策略 | 2744:2872 | 64 | 0.634857 |
| 3 | 大模型应用/知识条目/KV Cache.md | 10 | KV Cache（键值缓存）深度知识概览 > 五、 针对 KV Cache 的顶尖优化策略 > 4. FlashAttention / FlashDecoding | 4009:4202 | 92 | 0.606967 |
| 4 | 大模型应用/知识条目/KV Cache.md | 8 | KV Cache（键值缓存）深度知识概览 > 五、 针对 KV Cache 的顶尖优化策略 > 2. PagedAttention（分页注意力）—— vLLM 框架的核心 | 3316:3715 | 154 | 0.633377 |
| 5 | 大模型应用/知识条目/KV Cache.md | 2 | KV Cache（键值缓存）深度知识概览 | 708:1513 | 233 | 0.651958 |

### Q3 · HIT@1

Redis 的持久化方式有哪两种？各有什么特点？

标注出处：`数据库/Redis/Redis持久化.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/Redis/Redis持久化.md | 1 |  | 0:344 | 97 | 0.704504 |
| 2 | 数据库/Redis/Redis持久化.md | 2 |  | 344:909 | 190 | 0.668889 |
| 3 | 数据库/Redis/Redis持久化.md | 29 | 小结 | 16005:16678 | 207 | 0.705213 |
| 4 | 数据库/Redis/Redis持久化.md | 3 | 1. 为什么需要持久化 > 1.1 Redis 的内存特性 | 909:1800 | 224 | 0.679790 |
| 5 | 数据库/Redis/Redis持久化.md | 16 | 4. 混合持久化 > 4.1 什么是混合持久化 | 9192:10088 | 231 | 0.655764 |

### Q4 · HIT@1

事务的隔离级别有哪些？哪个并发性能最差？

标注出处：`数据库/知识条目/ACID.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/ACID.md | 3 | 详细 > 重点 | 677:1848 | 299 | 0.557580 |
| 2 | 数据库/知识条目/ACID.md | 1 |  | 0:345 | 89 | 0.543590 |
| 3 | 数据库/知识条目/Redis 事务.md | 3 | 详细 > 重点 | 621:2183 | 447 | 0.507957 |
| 4 | 数据库/知识条目/Redis 事务.md | 2 | 详细 > 概念 | 344:621 | 68 | 0.494884 |
| 5 | 数据库/知识条目/Redis 事务.md | 1 |  | 0:344 | 91 | 0.497169 |

### Q5 · HIT@1

Docker 镜像和容器是什么关系？

标注出处：`JavaWeb/知识条目/部署/Docker/Docker 镜像.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 1 |  | 0:306 | 88 | 0.772608 |
| 2 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 3 | 详细 > 重点 | 666:1785 | 344 | 0.644402 |
| 3 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 2 | 详细 > 概念 | 306:666 | 90 | 0.730206 |
| 4 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 1 |  | 0:549 | 134 | 0.638566 |
| 5 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 2 | 详细 > 重点 | 549:1867 | 436 | 0.585898 |

### Q6 · HIT@1

LCEL 是什么？

标注出处：`大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md | 2 | 详细 > 概念 | 270:981 | 193 | 0.686714 |
| 2 | 大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md | 1 |  | 0:270 | 77 | 0.539079 |
| 3 | 大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md | 3 | 详细 > 重点 | 981:2544 | 452 | 0.597743 |
| 4 | 大模型应用/知识条目/Runnable 协议.md | 2 | 详细 > 概念 | 259:835 | 155 | 0.482474 |
| 5 | 大模型应用/知识条目/Tools.md | 2 | 详细 > 概念 | 260:808 | 144 | 0.439122 |

### Q7 · HIT@1

Prompt Template 是什么？为什么需要它？

标注出处：`大模型应用/知识条目/PromptTemplate 提示词模板.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/PromptTemplate 提示词模板.md | 3 | 详细 > 重点 | 873:2399 | 417 | 0.649102 |
| 2 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 13 | 5. LangChain Memory 组件 > 5.3 ConversationSummaryMemory（摘要压缩） | 8658:9634 | 303 | 0.505230 |
| 3 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 4 | 2. `create_agent` 参数详解 > 2.1 必选参数 | 2472:3393 | 310 | 0.489561 |
| 4 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 5 |  | 3393:5047 | 426 | 0.488253 |
| 5 | 大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md | 3 | 详细 > 重点 | 981:2544 | 452 | 0.477891 |

### Q8 · HIT@1

Pinia 是什么？它管理什么？

标注出处：`JavaWeb/Web前端设计/Pinia状态管理.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/Web前端设计/Pinia状态管理.md | 29 | 小结 | 19480:20358 | 252 | 0.636442 |
| 2 | JavaWeb/Web前端设计/Pinia状态管理.md | 1 |  | 0:566 | 139 | 0.596241 |
| 3 | JavaWeb/Web前端设计/Pinia状态管理.md | 4 | 2. Pinia 简介 | 2346:2749 | 127 | 0.598280 |
| 4 | JavaWeb/Web前端设计/Pinia状态管理.md | 2 | 目录 | 566:1509 | 344 | 0.552889 |
| 5 | JavaWeb/Web前端设计/Pinia状态管理.md | 5 | 2. Pinia 简介 > 2.1 Pinia vs Vuex | 2749:3214 | 202 | 0.508824 |

### Q9 · HIT@1

Docker 数据卷是什么？容器删了数据还在吗？

标注出处：`JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 1 |  | 0:549 | 134 | 0.808238 |
| 2 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 2 | 详细 > 重点 | 549:1867 | 436 | 0.670576 |
| 3 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 1 |  | 0:306 | 88 | 0.575671 |
| 4 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 3 | 详细 > 重点 | 666:1785 | 344 | 0.540038 |
| 5 | JavaWeb/知识条目/后端/Tomcat.md | 2 | 详细 > 重点 | 450:646 | 65 | 0.514677 |

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
| 1 | JavaWeb/知识条目/后端/URL编码.md | 1 |  | 0:629 | 176 | 0.795597 |
| 2 | JavaWeb/知识条目/后端/Cookie 和 Session.md | 1 |  | 0:555 | 142 | 0.523826 |
| 3 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 21 | Interceptor 拦截器技术 > Interceptor 与 Filter 的区别 > 选择建议 | 13963:14494 | 202 | 0.448251 |
| 4 | 大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md | 2 | 详细 > 概念 | 270:981 | 193 | 0.518135 |
| 5 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 22 | Interceptor 拦截器技术 | 14494:14951 | 137 | 0.435543 |

### Q12 · HIT@1

Redis 内存不足时怎么淘汰数据？有哪些策略？

标注出处：`数据库/知识条目/Redis 内存淘汰.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/Redis 内存淘汰.md | 1 |  | 0:385 | 106 | 0.796852 |
| 2 | 数据库/知识条目/Redis 内存淘汰.md | 2 | 详细 > 概念 | 385:637 | 70 | 0.763237 |
| 3 | 数据库/Redis过期Key处理.md | 6 |  | 2684:3335 | 177 | 0.678804 |
| 4 | 数据库/Redis过期Key处理.md | 2 |  | 347:921 | 190 | 0.651878 |
| 5 | 数据库/Redis/Redis持久化.md | 3 | 1. 为什么需要持久化 > 1.1 Redis 的内存特性 | 909:1800 | 224 | 0.663875 |

### Q13 · HIT@1

拦截器和过滤器有什么区别？分别执行在哪个阶段？

标注出处：`JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 20 | Interceptor 拦截器技术 > Interceptor 与 Filter 的区别 | 13232:13963 | 277 | 0.684574 |
| 2 | JavaWeb/知识条目/后端/Interceptor拦截器.md | 1 |  | 0:554 | 149 | 0.667939 |
| 3 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 2 | Interceptor 拦截器技术 > 什么是 Interceptor | 668:1171 | 150 | 0.660973 |
| 4 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 1 | Interceptor 拦截器技术 | 0:668 | 213 | 0.627936 |
| 5 | JavaWeb/知识条目/后端/Interceptor拦截器.md | 2 | 详细 > 重点 | 554:834 | 85 | 0.635662 |

### Q14 · HIT@1

Nginx 反向代理是什么？和正向代理的区别？

标注出处：`JavaWeb/Web前端设计/Nginx 反向代理服务器.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/Web前端设计/Nginx 反向代理服务器.md | 1 |  | 0:418 | 118 | 0.725610 |
| 2 | JavaWeb/Web前端设计/Nginx 反向代理服务器.md | 3 | 详细 > 重点 | 695:1854 | 325 | 0.714998 |
| 3 | JavaWeb/Web前端设计/Nginx 反向代理服务器.md | 2 | 详细 > 概念 | 418:695 | 79 | 0.673097 |
| 4 | 数据库/知识条目/缓存.md | 3 | 详细 > 重点 | 731:2293 | 423 | 0.489769 |
| 5 | JavaWeb/知识条目/后端/Interceptor拦截器.md | 2 | 详细 > 重点 | 554:834 | 85 | 0.457724 |

### Q15 · HIT@1

create_agent 是干什么的？它取代了旧版的哪个函数？

标注出处：`大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 1 |  | 0:462 | 122 | 0.699378 |
| 2 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 17 | 小结 | 14731:15494 | 233 | 0.718172 |
| 3 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 3 |  | 1548:2472 | 337 | 0.681377 |
| 4 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 2 | 目录 | 462:1548 | 382 | 0.652931 |
| 5 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 14 | 5. 完整实战：多工具 Agent | 8598:9696 | 296 | 0.598279 |

### Q16 · HIT@1

大模型“开卷考试”是什么意思？

标注出处：`大模型应用/知识条目/Rag.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/Rag.md | 1 |  | 0:626 | 157 | 0.537299 |
| 2 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 1 |  | 0:385 | 106 | 0.450537 |
| 3 | 大模型应用/知识条目/Rag.md | 2 | RAG（检索增强生成）知识概览 > 二、 为什么需要 RAG？（价值与痛点） | 626:1360 | 241 | 0.467315 |
| 4 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 1 |  | 0:462 | 122 | 0.403712 |
| 5 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 7 | 3. 核心挑战：上下文膨胀 > 3.1 Token 增长模型 | 4432:4730 | 109 | 0.430878 |

### N1 · 未评分

Redis 的 GEO 命令怎么用？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/Redis 事务.md | 1 |  | 0:344 | 91 | 0.611671 |
| 2 | 数据库/知识条目/Redis 事务.md | 2 | 详细 > 概念 | 344:621 | 68 | 0.563771 |
| 3 | 数据库/知识条目/Redis 事务.md | 3 | 详细 > 重点 | 621:2183 | 447 | 0.569055 |
| 4 | 数据库/Redis过期Key处理.md | 2 |  | 347:921 | 190 | 0.524645 |
| 5 | 数据库/Redis过期Key处理.md | 3 | 1. 过期时间设置命令 > 1.1 设置过期时间的命令 | 921:1626 | 213 | 0.524554 |

### N2 · 未评分

Spring Boot 的 GraalVM 原生镜像怎么配置？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 3 | 详细 > 重点 | 666:1785 | 344 | 0.488008 |
| 2 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 1 |  | 0:306 | 88 | 0.487100 |
| 3 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 2 | 详细 > 概念 | 306:666 | 90 | 0.473504 |
| 4 | JavaWeb/知识条目/后端/Bean.md | 1 |  | 0:407 | 104 | 0.430934 |
| 5 | JavaWeb/Web前端设计/Pinia状态管理.md | 4 | 2. Pinia 简介 | 2346:2749 | 127 | 0.430271 |

### N3 · 未评分

Kubernetes 的 StatefulSet 和 Deployment 有什么区别？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/前端/onMounted生命周期函数.md | 3 | 详细 > 重点 | 612:1620 | 322 | 0.498015 |
| 2 | JavaWeb/知识条目/后端/Tomcat.md | 2 | 详细 > 重点 | 450:646 | 65 | 0.519417 |
| 3 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 20 | Interceptor 拦截器技术 > Interceptor 与 Filter 的区别 | 13232:13963 | 277 | 0.428342 |
| 4 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 3 | 详细 > 重点 | 670:1636 | 298 | 0.514591 |
| 5 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 1 | Interceptor 拦截器技术 | 0:668 | 213 | 0.327522 |

### N4 · 未评分

Vue 3 的 Teleport 特性怎么用？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/前端/onMounted生命周期函数.md | 3 | 详细 > 重点 | 612:1620 | 322 | 0.508090 |
| 2 | JavaWeb/知识条目/前端/onMounted生命周期函数.md | 2 | 详细 > 概念 | 382:612 | 74 | 0.483171 |
| 3 | JavaWeb/知识条目/前端/onMounted生命周期函数.md | 1 |  | 0:382 | 105 | 0.493122 |
| 4 | JavaWeb/Web前端设计/Pinia状态管理.md | 10 |  | 6132:7168 | 271 | 0.463442 |
| 5 | JavaWeb/Web前端设计/Pinia状态管理.md | 29 | 小结 | 19480:20358 | 252 | 0.497480 |

### N5 · 未评分

MySQL 的窗口函数 ROW_NUMBER() 怎么用？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 12 | 5. LangChain Memory 组件 > 5.2 ConversationBufferWindowMemory（滑动窗口） | 8186:8658 | 152 | 0.510216 |
| 2 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 21 | 小结 | 15399:16078 | 196 | 0.506074 |
| 3 | 数据库/知识条目/Redis 事务.md | 3 | 详细 > 重点 | 621:2183 | 447 | 0.530133 |
| 4 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 17 |  | 12122:13270 | 316 | 0.506528 |
| 5 | 大模型应用/知识条目/Tools.md | 3 | 详细 > 重点 | 808:2425 | 441 | 0.483113 |

### N6 · 未评分

LangChain 的 Graph 模块如何构建状态图？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md | 2 | 详细 > 概念 | 270:981 | 193 | 0.561871 |
| 2 | 大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md | 3 | 详细 > 重点 | 981:2544 | 452 | 0.565766 |
| 3 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 17 | 小结 | 14731:15494 | 233 | 0.554119 |
| 4 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 1 |  | 0:462 | 122 | 0.503451 |
| 5 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 20 | 7. 与 RAG 结合的多轮对话 > 7.3 简单实现 | 14504:15399 | 297 | 0.518297 |

### N7 · 未评分

我去年三月在上海买了什么？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 7 | 3. Agent 的输入：`invoke()` 接收什么 > 3.2 输入形态二：消息列表 | 5310:5780 | 148 | 0.443176 |
| 2 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 15 | 5. 完整实战：多工具 Agent | 9696:10916 | 377 | 0.429183 |
| 3 | JavaWeb/Web前端设计/Pinia状态管理.md | 11 | 4. 使用 Store > 4.1 导入 Store | 7168:7381 | 74 | 0.439501 |
| 4 | JavaWeb/Web前端设计/Pinia状态管理.md | 18 | 6. 状态持久化 > 6.2 使用 pinia-plugin-persistedstate（推荐） | 12796:13696 | 291 | 0.415140 |
| 5 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 4 | 2. `create_agent` 参数详解 > 2.1 必选参数 | 2472:3393 | 310 | 0.408254 |

### P1 · 未评分

什么是 Transformer 的多头注意力机制？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/KV Cache.md | 7 | KV Cache（键值缓存）深度知识概览 > 五、 针对 KV Cache 的顶尖优化策略 > 1. 多查询注意力（MQA）与分组查询注意力（GQA） | 2872:3316 | 182 | 0.544614 |
| 2 | 大模型应用/知识条目/KV Cache.md | 4 | KV Cache（键值缓存）深度知识概览 > 三、 工作机制（底层原理解析） > 2. 注意力计算的变化 | 1954:2187 | 113 | 0.520229 |
| 3 | 大模型应用/知识条目/KV Cache.md | 8 | KV Cache（键值缓存）深度知识概览 > 五、 针对 KV Cache 的顶尖优化策略 > 2. PagedAttention（分页注意力）—— vLLM 框架的核心 | 3316:3715 | 154 | 0.459542 |
| 4 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 1 |  | 0:385 | 106 | 0.481036 |
| 5 | 大模型应用/知识条目/KV Cache.md | 10 | KV Cache（键值缓存）深度知识概览 > 五、 针对 KV Cache 的顶尖优化策略 > 4. FlashAttention / FlashDecoding | 4009:4202 | 92 | 0.474104 |

### R1 · HIT@1

大模型推理时为什么速度慢？有什么优化手段？

标注出处：`大模型应用/知识条目/KV Cache.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/KV Cache.md | 9 | KV Cache（键值缓存）深度知识概览 > 五、 针对 KV Cache 的顶尖优化策略 > 3. 连续批处理（Continuous Batching） | 3715:4009 | 116 | 0.555786 |
| 2 | 大模型应用/知识条目/KV Cache.md | 5 | KV Cache（键值缓存）深度知识概览 > 四、 内存开销：巨大的“吞金兽”（显存瓶颈） | 2187:2744 | 215 | 0.584081 |
| 3 | 大模型应用/知识条目/Rag.md | 1 |  | 0:626 | 157 | 0.543021 |
| 4 | 大模型应用/知识条目/KV Cache.md | 3 | KV Cache（键值缓存）深度知识概览 > 三、 工作机制（底层原理解析） > 1. 推理阶段划分 | 1513:1954 | 166 | 0.519427 |
| 5 | 数据库/Redis/Redis持久化.md | 9 |  | 4408:4914 | 168 | 0.521227 |

### R2 · HIT@1

服务器断电了，Redis 数据会不会丢？

标注出处：`数据库/Redis/Redis持久化.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/Redis/Redis持久化.md | 1 |  | 0:344 | 97 | 0.683895 |
| 2 | 数据库/Redis/Redis持久化.md | 3 | 1. 为什么需要持久化 > 1.1 Redis 的内存特性 | 909:1800 | 224 | 0.629197 |
| 3 | 数据库/Redis过期Key处理.md | 18 | 6. 过期 Key 的内存回收 > 6.2 内存池 vs OS 内存 | 9265:9815 | 173 | 0.615754 |
| 4 | 数据库/Redis/Redis持久化.md | 29 | 小结 | 16005:16678 | 207 | 0.595304 |
| 5 | 数据库/知识条目/Redis 事务.md | 2 | 详细 > 概念 | 344:621 | 68 | 0.583903 |

### R3 · HIT@1

页面刷新后 Pinia 里的状态还在吗？

标注出处：`JavaWeb/Web前端设计/Pinia状态管理.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/Web前端设计/Pinia状态管理.md | 18 | 6. 状态持久化 > 6.2 使用 pinia-plugin-persistedstate（推荐） | 12796:13696 | 291 | 0.645438 |
| 2 | JavaWeb/Web前端设计/Pinia状态管理.md | 16 |  | 11430:12139 | 202 | 0.608936 |
| 3 | JavaWeb/Web前端设计/Pinia状态管理.md | 1 |  | 0:566 | 139 | 0.608667 |
| 4 | JavaWeb/Web前端设计/Pinia状态管理.md | 29 | 小结 | 19480:20358 | 252 | 0.612851 |
| 5 | JavaWeb/Web前端设计/Pinia状态管理.md | 2 | 目录 | 566:1509 | 344 | 0.559497 |

### R4 · HIT@1

怎么防止数据库被 SQL 注入攻击？

标注出处：`JavaWeb/知识条目/后端/SQL注入.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/后端/SQL注入.md | 1 |  | 0:590 | 156 | 0.720631 |
| 2 | 数据库/知识条目/ACID.md | 3 | 详细 > 重点 | 677:1848 | 299 | 0.589015 |
| 3 | 数据库/知识条目/缓存.md | 3 | 详细 > 重点 | 731:2293 | 423 | 0.545904 |
| 4 | 数据库/知识条目/ACID.md | 1 |  | 0:345 | 89 | 0.535915 |
| 5 | JavaWeb/知识条目/后端/Tomcat.md | 2 | 详细 > 重点 | 450:646 | 65 | 0.543893 |

### R5 · HIT@1

Docker 容器一删就什么都没了吗？有办法保留数据吗？

标注出处：`JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 1 |  | 0:549 | 134 | 0.687462 |
| 2 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 2 | 详细 > 重点 | 549:1867 | 436 | 0.670580 |
| 3 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 3 | 详细 > 重点 | 666:1785 | 344 | 0.588635 |
| 4 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 1 |  | 0:306 | 88 | 0.572595 |
| 5 | 数据库/Redis过期Key处理.md | 20 | 7. 常见问题与陷阱 > 7.1 陷阱一：过期 ≠ 立即删除 | 10474:11026 | 165 | 0.530871 |

### R6 · HIT@1

Redis 和数据库怎么配合？缓存不一致怎么处理？

标注出处：`数据库/知识条目/缓存.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/缓存.md | 3 | 详细 > 重点 | 731:2293 | 423 | 0.707462 |
| 2 | 数据库/Redis/Redis持久化.md | 1 |  | 0:344 | 97 | 0.691415 |
| 3 | 数据库/知识条目/Redis 事务.md | 1 |  | 0:344 | 91 | 0.665336 |
| 4 | 数据库/知识条目/Redis 事务.md | 2 | 详细 > 概念 | 344:621 | 68 | 0.675699 |
| 5 | 数据库/Redis过期Key处理.md | 1 |  | 0:347 | 96 | 0.641977 |

## 语料指纹

| 相对路径 | 字节数 | chunks | SHA-256 |
|---|---:|---:|---|
| JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 16437 | 24 | `87749f86445988adae93a30b76488cebb1bb68e81514ed253271ca8288685f20` |
| JavaWeb/Web前端设计/Nginx 反向代理服务器.md | 1854 | 3 | `76378bba9af5494eb42d0dea8383019e86ca6e189c3654fe886d6799a4dd3491` |
| JavaWeb/Web前端设计/Pinia状态管理.md | 20358 | 29 | `08522cdc77a88b236b47da9d88fe3d3809876ba243f3ded1a6567fec30950a60` |
| JavaWeb/知识条目/前端/onMounted生命周期函数.md | 1620 | 3 | `1ef5b332c3867b637e55505dada3352e41ebed71f7a3b780a17f3ebe9d096fc9` |
| JavaWeb/知识条目/后端/Bean.md | 691 | 2 | `e45a0f52c8b998f18bf6a6a92940cdd31c4a07747500ce71304bee513d10dcb1` |
| JavaWeb/知识条目/后端/Cookie 和 Session.md | 885 | 2 | `aa5bc7570673dbbc434d258b4fb3e7137ffc41bc5545159f0a7226c5755936fb` |
| JavaWeb/知识条目/后端/Interceptor拦截器.md | 834 | 2 | `9ee79b7ba1f8f4a1e42d1a96accc40c1d6342d3bcdd989ddc071ddb0362f23c5` |
| JavaWeb/知识条目/后端/SQL注入.md | 590 | 1 | `02aaf2a6a46a8883f50920fcaf2a079f7bdf59102b0dd9e8c8bb6609eaf7a42e` |
| JavaWeb/知识条目/后端/Tomcat.md | 646 | 2 | `26cc508e10287dc27ce3cadee5ef7db79d02711308a132e4699777a68b70e5c2` |
| JavaWeb/知识条目/后端/URL编码.md | 629 | 1 | `5b233aebc09ff4d67beff3961f91203e43644c3717aec40a3547fe0049129cac` |
| JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 1867 | 2 | `4d28b48c97137b5b9edd5511ac98c0e729d81183cc676ab87f3c7ba9ebeb6a9e` |
| JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 1785 | 3 | `84283b73369a0096b89e886dfcb59583f3937546faf3a50444e9e9e17412a107` |
| 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 15494 | 17 | `084985ae282fbf306899af7edfe1d97e053ba753537ce07dddc2d47adf63cb1f` |
| 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 16078 | 21 | `8e9495f79f84a86b9c44940aafd2ea8d974b0439de4bae8d82b14a576d42aa31` |
| 大模型应用/知识条目/invoke、stream 与 batch.md | 2264 | 3 | `b27ab909085857a74d13b829d79e116c48695323ff3480a4168455e3d69cd6ab` |
| 大模型应用/知识条目/KV Cache.md | 5543 | 12 | `be601cd1032433a71bbf6e0f6d02fd79d51bfbbf9748266721b3bcde7b28ec8e` |
| 大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md | 2544 | 3 | `c5c32b240d28400013e4d8108f44f33c83d726230ec7a4d512d14f0d4e9e0548` |
| 大模型应用/知识条目/PromptTemplate 提示词模板.md | 2399 | 3 | `1b0cf1beb85b1cc5acf490a2ae13d606e4d2da5b794f84241139523b0aead513` |
| 大模型应用/知识条目/Rag.md | 6061 | 8 | `52fabc299f67191800afa02f96fb23b20219a4d72d55b9eaf1f56869e94cc70b` |
| 大模型应用/知识条目/Runnable 协议.md | 2157 | 3 | `546f414b7d625993e11f32ef6e06a85900faa4f857ae1b9ed593011cd926b1b3` |
| 大模型应用/知识条目/Tools.md | 2425 | 3 | `baa18909cb8755213397bc6b0ffa137b21c126af75171854fd21b3b62e8dd226` |
| 数据库/Redis/Redis持久化.md | 16678 | 29 | `a372a719e5925d83c83a0001f97fdcbe859b374f2d9a1d7b9575dec34ed367db` |
| 数据库/Redis过期Key处理.md | 13731 | 26 | `fc06ac5bb830ae81c0f4d092714b4aa9452250697c83ce3cc0c5ef77f48b39ab` |
| 数据库/知识条目/ACID.md | 1848 | 3 | `af053fb7be8d194ed45704711c53142a0194e8a503320c4c608981c0f29015c4` |
| 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 2393 | 4 | `a9c7874dc8fc3d88674a99755dc22c02748668afcd0d08397af4518920fdcc33` |
| 数据库/知识条目/Redis 事务.md | 2183 | 3 | `a88bcbef1516512825678168de5aa68ec87e67a2f91fbcf7c3c423b1737b4baa` |
| 数据库/知识条目/Redis 内存淘汰.md | 2637 | 4 | `71db460cf6af2b934bbdd98827e85d3721f08e3a5c4a52ae82e05e73d815e188` |
| 数据库/知识条目/操作系统缓冲区（OS Buffer）.md | 1905 | 3 | `569735092b0212193ee3483f0971b00acdb28df82e7c658020eea6792061d790` |
| 数据库/知识条目/缓存.md | 2293 | 3 | `67705fdf01800d0bdc6512e7c64bcd0da93a14af9720960e79fab4780b1d24ce` |

## 环境与代码指纹

- Python: `3.14.6`
- Ollama: `0.30.10`
- chromadb: `1.5.9`
- tokenizers: `0.23.2`
- httpx: `0.28.1`

- `retrieval.split_markdown` SHA-256: `489fe26f5016b1a107bc2a49ad756f4d7b53fe0f112cdd03b472f3e355f8e4cf`
- `scripts/eval_retrieval.py` SHA-256: `73c8519cc07deef5347c4c437e01d0fb2f986c40086ea1e04c9adf4c4cf51cfb`
- `requirements.txt` SHA-256: `b4e3d6c645b7fb8654aecadb76ce9410ed6f8fe139440bdb1182a79fcbdecf36`

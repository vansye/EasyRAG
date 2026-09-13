# 离线召回验收报告（子 Issue B）

> 运行时间：2026-09-13T22:29:31+08:00。由离线 CLI 生成，未调用 LLM。

## 验收边界

本报告只验证切片与向量索引的离线召回，不代表 Java 收录、更新同步、业务问答或模块 D 评估平台已完成。
不实现 Agent、改写、BM25、重排、拒答或部分覆盖判断；不读取 MySQL，不打开业务持久化 Chroma。

## 召回结果

| 类别 | hit@1 | hit@3 | hit@5 | hit@10 |
|---|---:|---:|---:|---:|
| Q | 5/6（83.33%） | 5/6（83.33%） | 5/6（83.33%） | 5/6（83.33%） |
| R | 2/2（100.00%） | 2/2（100.00%） | 2/2（100.00%） | 2/2（100.00%） |
| Q+R | 7/8（87.50%） | 7/8（87.50%） | 7/8（87.50%） | 7/8（87.50%） |

漏召回题（前 10 均未命中）：Q21。
命中但未进前 5：无。
N 类 3 题、P 类 1 题只展示召回，不进入上述分母，也不据此判定拒答或部分覆盖能力。

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
- `byte_start/byte_end` 为原文 UTF-8 字节偏移（左闭右开）；正文不规范化换行。Java/HTTP 跨语言契约尚未验收。
- 索引：独立 Chroma 内存 collection，cosine 距离，检索后只删除本次 collection；无阈值过滤。
- 语料：29 篇、143506 字节；222 个 chunk。
- 实际 chunk token 范围：64–490；低于软下限：0 个。
- 文档 embedding 输入合计 43892 token；全部查询合计 284 token。
- 语料指纹（按排序后的路径与原文 SHA-256 汇总）：`45f1bf18c58a1f84af9852cb50fcd61be5a589db41e096e6dd67f14449dc0b4b`。
- 黄金集原文件 SHA-256：`f35262e05a6785f03a78d2738fa64337440e5dfa424d15a40f49da123909bbfd`。

## 耗时

| 阶段 | 秒 |
|---|---:|
| 切片与准备 | 1.508 |
| 文档 embedding | 29.174 |
| 问题 embedding | 0.985 |
| 临时索引与检索 | 0.642 |
| 总计（不含 Python 导入和报告写入） | 32.508 |

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
  --golden-set '../docs/eval/golden-set-v2-heldout.md' `
  --tokenizer 'data/tokenizers/bge-m3-5617a9f61b028005a4858fdac845db406aefb181.json' `
  --ollama-url 'http://127.0.0.1:11434' `
  --model 'bge-m3' `
  --dimensions '1024' `
  --max-tokens '512' `
  --min-tokens '64' `
  --batch-size '16' `
  --timeout-seconds '180' `
  --output '../docs/eval/retrieval-heldout-v1.md'
```

## 逐题明细（检索深度 10，展示前 5）

相似度 = 1 − cosine distance，仅作排序诊断，不代表置信度。位置以原始文件的 UTF-8 字节为单位。

### Q17 · HIT@1

HTTP 是无状态协议，那登录状态是怎么保持的？Cookie 和 Session 分别存在哪边？

标注出处：`JavaWeb/知识条目/后端/Cookie 和 Session.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/后端/Cookie 和 Session.md | 1 |  | 0:538 | 142 | 0.779441 |
| 2 | JavaWeb/知识条目/后端/Cookie 和 Session.md | 2 | 详细 > 重点 | 538:863 | 98 | 0.698018 |
| 3 | JavaWeb/Web前端设计/Pinia状态管理.md | 18 | 6. 状态持久化 > 6.2 使用 pinia-plugin-persistedstate（推荐） | 12347:13213 | 291 | 0.600112 |
| 4 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 13 | Interceptor 拦截器技术 > 代码示例：token 登录拦截器 > 结合你项目的实际代码 `tokenInterceptor.java` | 9121:10045 | 287 | 0.574319 |
| 5 | JavaWeb/Web前端设计/Pinia状态管理.md | 8 | 3. Store 定义 > 3.2 state 状态定义 | 4249:4975 | 206 | 0.568497 |

### Q18 · HIT@1

Redis 的 key 设置过期时间后，到期就会被立即删除吗？实际的删除策略是什么？

标注出处：`数据库/Redis过期Key处理.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/Redis过期Key处理.md | 6 |  | 2603:3232 | 177 | 0.848523 |
| 2 | 数据库/Redis过期Key处理.md | 1 |  | 0:336 | 96 | 0.840985 |
| 3 | 数据库/Redis过期Key处理.md | 7 | 3. 惰性删除详解 > 3.1 工作原理 | 3232:3673 | 108 | 0.812166 |
| 4 | 数据库/Redis过期Key处理.md | 10 | 4. 定期删除详解 > 4.1 工作原理 | 4639:5097 | 151 | 0.807130 |
| 5 | 数据库/Redis过期Key处理.md | 26 | 小结 | 12564:13311 | 227 | 0.737443 |

### Q19 · HIT@1

Redis 事务和 MySQL 事务有什么区别？Redis 事务执行到一半出错了会回滚吗？

标注出处：`数据库/知识条目/Redis 事务.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/Redis 事务.md | 2 | 详细 > 概念 | 327:601 | 68 | 0.782411 |
| 2 | 数据库/知识条目/Redis 事务.md | 3 | 详细 > 重点 | 601:2137 | 447 | 0.764790 |
| 3 | 数据库/知识条目/Redis 事务.md | 1 |  | 0:327 | 91 | 0.749375 |
| 4 | 数据库/Redis过期Key处理.md | 18 | 6. 过期 Key 的内存回收 > 6.2 内存池 vs OS 内存 | 8973:9506 | 173 | 0.631949 |
| 5 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 4 | 详细 > 重点 | 1599:2347 | 235 | 0.619295 |

### Q20 · HIT@1

epoll 和 select/poll 相比优势在哪？为什么高并发服务器都用 epoll？

标注出处：`数据库/知识条目/epoll（Linux 高并发网络模型）.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 1 |  | 0:404 | 120 | 0.731141 |
| 2 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 2 | 详细 > 概念 | 404:650 | 65 | 0.680892 |
| 3 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 3 | 详细 > 重点 | 650:1599 | 298 | 0.656183 |
| 4 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 4 | 详细 > 重点 | 1599:2347 | 235 | 0.624643 |
| 5 | 数据库/Redis/Redis持久化.md | 15 |  | 8355:8950 | 199 | 0.541845 |

### Q21 · MISS

为什么文件写完并返回成功，断电后数据还是可能丢？怎么强制立即落盘？

标注出处：`数据库/知识条目/操作系统缓冲区（OS Buffer）.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/Redis/Redis持久化.md | 26 | 7. 故障恢复与数据一致性 > 7.2 异常宕机后的数据状态 | 14062:14530 | 154 | 0.650162 |
| 2 | 数据库/Redis/Redis持久化.md | 1 |  | 0:333 | 97 | 0.629050 |
| 3 | 数据库/Redis/Redis持久化.md | 29 | 小结 | 15531:16194 | 207 | 0.608706 |
| 4 | 数据库/Redis过期Key处理.md | 20 | 7. 常见问题与陷阱 > 7.1 陷阱一：过期 ≠ 立即删除 | 10140:10678 | 165 | 0.606988 |
| 5 | 数据库/Redis过期Key处理.md | 26 | 小结 | 12564:13311 | 227 | 0.602266 |

### Q22 · HIT@1

Vue 的 onMounted 是什么时机执行的？为什么异步请求要放在它里面而不是 setup 里？

标注出处：`JavaWeb/知识条目/前端/onMounted生命周期函数.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/前端/onMounted生命周期函数.md | 3 | 详细 > 重点 | 594:1593 | 322 | 0.768263 |
| 2 | JavaWeb/知识条目/前端/onMounted生命周期函数.md | 2 | 详细 > 概念 | 367:594 | 74 | 0.737886 |
| 3 | JavaWeb/知识条目/前端/onMounted生命周期函数.md | 1 |  | 0:367 | 105 | 0.708561 |
| 4 | JavaWeb/Web前端设计/Pinia状态管理.md | 21 | 8. Store 与路由的配合 > 8.2 页面初始化数据 | 14620:15081 | 183 | 0.598902 |
| 5 | JavaWeb/Web前端设计/Pinia状态管理.md | 3 | 1. 为什么需要状态管理 | 1468:2283 | 253 | 0.540284 |

### N8 · 未评分

Redis 的主从复制怎么配置？集群模式怎么搭建？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/Redis/Redis持久化.md | 23 | 6. 生产环境配置方案 > 6.3 方案三：混合持久化（生产推荐 ⭐） | 12352:12808 | 165 | 0.616598 |
| 2 | 数据库/知识条目/Redis 事务.md | 1 |  | 0:327 | 91 | 0.612821 |
| 3 | 数据库/Redis/Redis持久化.md | 17 | 4. 混合持久化 > 4.2 配置 | 9831:10045 | 89 | 0.603244 |
| 4 | 数据库/Redis/Redis持久化.md | 22 | 6. 生产环境配置方案 > 6.2 方案二：AOF only（数据敏感场景） | 11986:12352 | 139 | 0.598765 |
| 5 | 数据库/知识条目/Redis 事务.md | 2 | 详细 > 概念 | 327:601 | 68 | 0.597042 |

### N9 · 未评分

Docker Compose 怎么编排多个容器？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 1 |  | 0:294 | 88 | 0.620793 |
| 2 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 3 | 详细 > 重点 | 651:1758 | 344 | 0.608962 |
| 3 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 2 | 详细 > 重点 | 534:1831 | 436 | 0.603571 |
| 4 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 1 |  | 0:534 | 134 | 0.596288 |
| 5 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 4 | Interceptor 拦截器技术 | 1560:3447 | 343 | 0.555999 |

### N10 · 未评分

Spring Boot 的自动装配是怎么工作的？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/Web前端设计/Pinia状态管理.md | 28 | 10. 常见用法总结 > 10.4 响应式追踪 | 18447:18739 | 105 | 0.497163 |
| 2 | JavaWeb/知识条目/后端/Bean.md | 1 |  | 0:407 | 104 | 0.494951 |
| 3 | 数据库/Redis/Redis持久化.md | 24 |  | 12808:13656 | 310 | 0.491383 |
| 4 | JavaWeb/Web前端设计/Pinia状态管理.md | 18 | 6. 状态持久化 > 6.2 使用 pinia-plugin-persistedstate（推荐） | 12347:13213 | 291 | 0.472774 |
| 5 | JavaWeb/知识条目/前端/onMounted生命周期函数.md | 2 | 详细 > 概念 | 367:594 | 74 | 0.463535 |

### P2 · 未评分

vLLM 的 PagedAttention 是怎么工作的？借鉴了操作系统的什么机制？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/KV Cache.md | 8 | KV Cache（键值缓存）深度知识概览 > 五、 针对 KV Cache 的顶尖优化策略 > 2. PagedAttention（分页注意力）—— vLLM 框架的核心 | 3256:3651 | 154 | 0.750779 |
| 2 | 大模型应用/知识条目/KV Cache.md | 10 | KV Cache（键值缓存）深度知识概览 > 五、 针对 KV Cache 的顶尖优化策略 > 4. FlashAttention / FlashDecoding | 3941:4131 | 92 | 0.552846 |
| 3 | 大模型应用/知识条目/KV Cache.md | 7 | KV Cache（键值缓存）深度知识概览 > 五、 针对 KV Cache 的顶尖优化策略 > 1. 多查询注意力（MQA）与分组查询注意力（GQA） | 2817:3256 | 182 | 0.538931 |
| 4 | 大模型应用/知识条目/KV Cache.md | 1 |  | 0:689 | 187 | 0.529260 |
| 5 | 大模型应用/知识条目/KV Cache.md | 4 | KV Cache（键值缓存）深度知识概览 > 三、 工作机制（底层原理解析） > 2. 注意力计算的变化 | 1918:2147 | 113 | 0.512873 |

### R7 · HIT@1

Redis 里一下子设了一万个相同过期时间的 key，到期后内存占用怎么没降下来？

标注出处：`数据库/Redis过期Key处理.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/Redis过期Key处理.md | 1 |  | 0:336 | 96 | 0.749781 |
| 2 | 数据库/Redis过期Key处理.md | 6 |  | 2603:3232 | 177 | 0.736610 |
| 3 | 数据库/Redis过期Key处理.md | 10 | 4. 定期删除详解 > 4.1 工作原理 | 4639:5097 | 151 | 0.725787 |
| 4 | 数据库/Redis过期Key处理.md | 22 | 7. 常见问题与陷阱 > 7.3 陷阱三：批量过期导致性能抖动 | 10941:11494 | 180 | 0.725565 |
| 5 | 数据库/Redis过期Key处理.md | 7 | 3. 惰性删除详解 > 3.1 工作原理 | 3232:3673 | 108 | 0.707355 |

### R8 · HIT@1

LangChain 里怎么让模型边生成边出结果？有一批输入想一次跑完用什么方法？

标注出处：`大模型应用/知识条目/invoke、stream 与 batch.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/invoke、stream 与 batch.md | 2 | 详细 > 概念 | 270:858 | 156 | 0.637461 |
| 2 | 大模型应用/知识条目/invoke、stream 与 batch.md | 3 | 详细 > 重点 | 858:2229 | 390 | 0.633453 |
| 3 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 1 |  | 0:374 | 106 | 0.628749 |
| 4 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 17 | 小结 | 14357:15110 | 233 | 0.620524 |
| 5 | 大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md | 2 | 详细 > 概念 | 255:960 | 193 | 0.602627 |

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

- `app/chunking.py` SHA-256: `9cf8832f5038c62527a9b2445734679713079c9125e840497cee1afd00a312bb`
- `scripts/eval_retrieval.py` SHA-256: `832542634b89cd0c65a5366fa6d82068c484884f97b81e3c8fcc8dcaa47fe876`
- `requirements.txt` SHA-256: `6951653d68a0e07bd1a4a8830a4c7d3d26ab14d0c94481e680938e8c854be7cc`

---

## 复核与解读（手工追加段，重跑会被覆盖，届时须按新结果重写）

本报告由黄金集 v2（留出难例集，`golden-set-v2-heldout.md`）生成——题在系统建成之后写、出处全部来自 v1 从未考过的 12 篇干扰项中的 7 篇。**这是第一份非满分的召回报告**：v1 基线 hit@5 = 22/22 = 100%，本集 7/8 = 87.5%。度量衡恢复了分辨力。

- **Q21（OS Buffer）是唯一漏召回，也是本集最有价值的发现**：正确出处是一张短概念卡，恰好完整回答"写回模式断电易丢 + fsync 强制落盘"，却**未进前 10**；Top-5 全被 Redis 专篇占据——第 1 名是 `Redis持久化.md` 的"异常宕机后的数据状态"（讲 RDB/AOF 宕机恢复，主题相邻但非本题答案）。这是**同主题密集区专篇压制短概念卡**的检索失衡：Redis 四篇笔记把"数据丢失/宕机"的语义空间占满，短卡的语义密度被稀释。它给 M4 提供了第一个带数字的改进靶子。
- **R7/R8 改写题直查全部命中（HIT@1）**：口语问法与原文术语的语义鸿沟，bge-m3 直接跨过去了。与 C-2 裁决（改写重查在此数据上无增益，M2 不做）相互印证——留出集继续支持这条裁决。
- 其余 5 题直答全部 HIT@1，包括设计过干扰的 Q17（原理篇压过实现篇）、Q18（过期删除与内存淘汰的机制辨析）、Q19（Redis 事务 vs ACID 对比表）。
- **N8/N9/N10 与 P2 不参与召回计分**（解析器对 N/P 类 expected_source 置空）。N8 是"检索可能词面命中、判定必须拒答"的模糊边界题，等三进程在线时随拒答首测（`POST /api/questions`）验证三态判定，离线脚本不冒充判定。
- 对比口径：v1（回归集，测"考过的还能不能找到"）hit@5 = 22/22；v2（留出集，测"没考过的能不能找到、会不会找错"）hit@5 = 7/8。两集数字分开报告，不合并趋势线。

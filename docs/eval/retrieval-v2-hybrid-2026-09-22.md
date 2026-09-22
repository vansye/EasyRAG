# 离线召回验收报告（子 Issue B）

> 运行时间：2026-09-22T10:48:57+08:00。由离线 CLI 生成，未调用 LLM。

## 验收边界

本报告只验证切片与索引的离线召回；资料收录、更新同步、业务问答与完整评估平台需各自的验收证据。
不实现 Agent、改写、重排、拒答或部分覆盖判断；不读取 MySQL，不打开业务持久化 Chroma。
检索策略：hybrid——向量与 BM25 各取前 20 个候选做倒数排名融合（RRF，k=60），与服务端 B-17 共用同一套函数；相似度列为向量余弦，只决定展示，不决定排序。

## 召回结果

| 类别 | hit@1 | hit@3 | hit@5 | hit@10 |
|---|---:|---:|---:|---:|
| Q | 5/6（83.33%） | 5/6（83.33%） | 6/6（100.00%） | 6/6（100.00%） |
| R | 2/2（100.00%） | 2/2（100.00%） | 2/2（100.00%） | 2/2（100.00%） |
| Q+R | 7/8（87.50%） | 7/8（87.50%） | 8/8（100.00%） | 8/8（100.00%） |

漏召回题（前 10 均未命中）：无。
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
- `byte_start/byte_end` 为原文 UTF-8 字节偏移（左闭右开）；正文不规范化换行。HTTP 定位契约由独立的资料管理与接口测试验证。
- 索引：独立 Chroma 内存 collection，cosine 距离，检索后只删除本次 collection；无阈值过滤。
- 策略：hybrid；hybrid 候选深度：20。
- 语料：29 篇、146829 字节；222 个 chunk。
- 实际 chunk token 范围：64–490；低于软下限：0 个。
- 文档 embedding 输入合计 43894 token；全部查询合计 284 token。
- 语料指纹（按排序后的路径与原文 SHA-256 汇总）：`3d4b803355606400354a4b4f73f4fde54fc92e352f43ac80e1dd60ffda123425`。
- 黄金集原文件 SHA-256：`f35262e05a6785f03a78d2738fa64337440e5dfa424d15a40f49da123909bbfd`。

## 耗时

| 阶段 | 秒 |
|---|---:|
| 切片与准备 | 0.604 |
| 文档 embedding | 18.003 |
| 问题 embedding | 0.896 |
| 临时索引与检索 | 0.953 |
| 总计（不含 Python 导入和报告写入） | 20.558 |

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
  --strategy 'hybrid' `
  --candidates '20' `
  --output '../docs/eval/retrieval-v2-hybrid-2026-09-22.md'
```

## 逐题明细（检索深度 10，展示前 5）

相似度 = 1 − cosine distance，仅作排序诊断，不代表置信度。位置以原始文件的 UTF-8 字节为单位。

### Q17 · HIT@1

HTTP 是无状态协议，那登录状态是怎么保持的？Cookie 和 Session 分别存在哪边？

标注出处：`JavaWeb/知识条目/后端/Cookie 和 Session.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/后端/Cookie 和 Session.md | 1 |  | 0:555 | 142 | 0.779400 |
| 2 | JavaWeb/知识条目/后端/Cookie 和 Session.md | 2 | 详细 > 重点 | 555:885 | 98 | 0.698180 |
| 3 | JavaWeb/Web前端设计/Pinia状态管理.md | 18 | 6. 状态持久化 > 6.2 使用 pinia-plugin-persistedstate（推荐） | 12796:13696 | 291 | 0.599747 |
| 4 | JavaWeb/Web前端设计/Pinia状态管理.md | 1 |  | 0:566 | 139 | 0.541961 |
| 5 | 数据库/知识条目/ACID.md | 3 | 详细 > 重点 | 677:1848 | 299 | 0.532849 |

### Q18 · HIT@1

Redis 的 key 设置过期时间后，到期就会被立即删除吗？实际的删除策略是什么？

标注出处：`数据库/Redis过期Key处理.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/Redis过期Key处理.md | 1 |  | 0:347 | 96 | 0.841352 |
| 2 | 数据库/Redis过期Key处理.md | 6 |  | 2684:3335 | 177 | 0.848635 |
| 3 | 数据库/Redis过期Key处理.md | 2 |  | 347:921 | 190 | 0.733412 |
| 4 | 数据库/Redis过期Key处理.md | 7 | 3. 惰性删除详解 > 3.1 工作原理 | 3335:3794 | 108 | 0.812310 |
| 5 | 数据库/Redis过期Key处理.md | 26 | 小结 | 12974:13731 | 227 | 0.737383 |

### Q19 · HIT@1

Redis 事务和 MySQL 事务有什么区别？Redis 事务执行到一半出错了会回滚吗？

标注出处：`数据库/知识条目/Redis 事务.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/Redis 事务.md | 2 | 详细 > 概念 | 344:621 | 68 | 0.782298 |
| 2 | 数据库/知识条目/Redis 事务.md | 3 | 详细 > 重点 | 621:2183 | 447 | 0.764834 |
| 3 | 数据库/知识条目/Redis 事务.md | 1 |  | 0:344 | 91 | 0.749129 |
| 4 | 数据库/Redis过期Key处理.md | 12 | 4. 定期删除详解 > 4.3 可调参数 | 6074:6603 | 156 | 0.610027 |
| 5 | 数据库/Redis过期Key处理.md | 1 |  | 0:347 | 96 | 0.612386 |

### Q20 · HIT@1

epoll 和 select/poll 相比优势在哪？为什么高并发服务器都用 epoll？

标注出处：`数据库/知识条目/epoll（Linux 高并发网络模型）.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 1 |  | 0:421 | 120 | 0.730894 |
| 2 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 4 | 详细 > 重点 | 1636:2393 | 235 | 0.624497 |
| 3 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 3 | 详细 > 重点 | 670:1636 | 298 | 0.655917 |
| 4 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 2 | 详细 > 概念 | 421:670 | 65 | 0.680698 |
| 5 | JavaWeb/Web前端设计/Nginx 反向代理服务器.md | 2 | 详细 > 概念 | 418:695 | 79 | 0.486822 |

### Q21 · HIT@4

为什么文件写完并返回成功，断电后数据还是可能丢？怎么强制立即落盘？

标注出处：`数据库/知识条目/操作系统缓冲区（OS Buffer）.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/Redis/Redis持久化.md | 26 | 7. 故障恢复与数据一致性 > 7.2 异常宕机后的数据状态 | 14481:14965 | 154 | 0.650091 |
| 2 | 数据库/Redis过期Key处理.md | 20 | 7. 常见问题与陷阱 > 7.1 陷阱一：过期 ≠ 立即删除 | 10474:11026 | 165 | 0.607189 |
| 3 | 数据库/Redis/Redis持久化.md | 12 | 3. AOF（追加日志持久化） > 3.3 三种刷盘策略详解 | 5504:6530 | 321 | 0.589809 |
| 4 | 数据库/知识条目/操作系统缓冲区（OS Buffer）.md | 3 | 详细 > 重点 | 757:1905 | 356 | 0.572244 |
| 5 | 数据库/Redis/Redis持久化.md | 29 | 小结 | 16005:16678 | 207 | 0.608760 |

### Q22 · HIT@1

Vue 的 onMounted 是什么时机执行的？为什么异步请求要放在它里面而不是 setup 里？

标注出处：`JavaWeb/知识条目/前端/onMounted生命周期函数.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/前端/onMounted生命周期函数.md | 3 | 详细 > 重点 | 612:1620 | 322 | 0.769410 |
| 2 | JavaWeb/知识条目/前端/onMounted生命周期函数.md | 1 |  | 0:382 | 105 | 0.709261 |
| 3 | JavaWeb/知识条目/前端/onMounted生命周期函数.md | 2 | 详细 > 概念 | 382:612 | 74 | 0.738476 |
| 4 | JavaWeb/Web前端设计/Pinia状态管理.md | 21 | 8. Store 与路由的配合 > 8.2 页面初始化数据 | 15165:15649 | 183 | 0.599017 |
| 5 | JavaWeb/Web前端设计/Pinia状态管理.md | 29 | 小结 | 19480:20358 | 252 | 0.514605 |

### N8 · 未评分

Redis 的主从复制怎么配置？集群模式怎么搭建？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/Redis/Redis持久化.md | 23 | 6. 生产环境配置方案 > 6.3 方案三：混合持久化（生产推荐 ⭐） | 12696:13171 | 165 | 0.616696 |
| 2 | 数据库/Redis/Redis持久化.md | 17 | 4. 混合持久化 > 4.2 配置 | 10088:10315 | 89 | 0.603504 |
| 3 | 数据库/Redis/Redis持久化.md | 24 |  | 13171:14059 | 310 | 0.580583 |
| 4 | 数据库/Redis过期Key处理.md | 12 | 4. 定期删除详解 > 4.3 可调参数 | 6074:6603 | 156 | 0.558811 |
| 5 | 数据库/Redis/Redis持久化.md | 1 |  | 0:344 | 97 | 0.580180 |

### N9 · 未评分

Docker Compose 怎么编排多个容器？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 1 |  | 0:306 | 88 | 0.621005 |
| 2 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 1 |  | 0:549 | 134 | 0.596856 |
| 3 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 2 | 详细 > 重点 | 549:1867 | 436 | 0.604151 |
| 4 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 3 | 详细 > 重点 | 666:1785 | 344 | 0.609425 |
| 5 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 4 | Interceptor 拦截器技术 | 1560:3447 | 343 | 0.555867 |

### N10 · 未评分

Spring Boot 的自动装配是怎么工作的？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/后端/Bean.md | 1 |  | 0:407 | 104 | 0.494709 |
| 2 | JavaWeb/Web前端设计/Pinia状态管理.md | 28 | 10. 常见用法总结 > 10.4 响应式追踪 | 19175:19480 | 105 | 0.497242 |
| 3 | 数据库/Redis过期Key处理.md | 10 | 4. 定期删除详解 > 4.1 工作原理 | 4791:5262 | 151 | 0.438460 |
| 4 | 数据库/Redis/Redis持久化.md | 5 | 2. RDB（快照持久化） > 2.1 工作原理 | 2169:3004 | 191 | 0.445177 |
| 5 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 3 | Interceptor 拦截器技术 > 什么是 Interceptor > 核心特点 | 1171:1560 | 128 | 0.429329 |

### P2 · 未评分

vLLM 的 PagedAttention 是怎么工作的？借鉴了操作系统的什么机制？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/KV Cache.md | 8 | KV Cache（键值缓存）深度知识概览 > 五、 针对 KV Cache 的顶尖优化策略 > 2. PagedAttention（分页注意力）—— vLLM 框架的核心 | 3316:3715 | 154 | 0.750845 |
| 2 | 大模型应用/知识条目/KV Cache.md | 12 | KV Cache（键值缓存）深度知识概览 > 七、 总结：关键时间节点速记 | 4839:5543 | 221 | 0.502568 |
| 3 | 大模型应用/知识条目/KV Cache.md | 4 | KV Cache（键值缓存）深度知识概览 > 三、 工作机制（底层原理解析） > 2. 注意力计算的变化 | 1954:2187 | 113 | 0.512580 |
| 4 | 数据库/知识条目/操作系统缓冲区（OS Buffer）.md | 3 | 详细 > 重点 | 757:1905 | 356 | 0.472466 |
| 5 | 大模型应用/知识条目/KV Cache.md | 11 | KV Cache（键值缓存）深度知识概览 > 六、 RAG 与 KV Cache 的密切关系 | 4202:4839 | 225 | 0.468199 |

### R7 · HIT@1

Redis 里一下子设了一万个相同过期时间的 key，到期后内存占用怎么没降下来？

标注出处：`数据库/Redis过期Key处理.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/Redis过期Key处理.md | 1 |  | 0:347 | 96 | 0.749794 |
| 2 | 数据库/Redis过期Key处理.md | 22 | 7. 常见问题与陷阱 > 7.3 陷阱三：批量过期导致性能抖动 | 11297:11867 | 180 | 0.725724 |
| 3 | 数据库/Redis过期Key处理.md | 6 |  | 2684:3335 | 177 | 0.737045 |
| 4 | 数据库/Redis过期Key处理.md | 2 |  | 347:921 | 190 | 0.690747 |
| 5 | 数据库/Redis过期Key处理.md | 16 |  | 8407:8939 | 157 | 0.673866 |

### R8 · HIT@1

LangChain 里怎么让模型边生成边出结果？有一批输入想一次跑完用什么方法？

标注出处：`大模型应用/知识条目/invoke、stream 与 batch.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/invoke、stream 与 batch.md | 3 | 详细 > 重点 | 885:2264 | 390 | 0.633183 |
| 2 | 大模型应用/知识条目/invoke、stream 与 batch.md | 2 | 详细 > 概念 | 291:885 | 156 | 0.636892 |
| 3 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 1 |  | 0:462 | 122 | 0.601506 |
| 4 | 大模型应用/知识条目/Runnable 协议.md | 3 | 详细 > 重点 | 835:2157 | 353 | 0.599184 |
| 5 | 大模型应用/知识条目/KV Cache.md | 9 | KV Cache（键值缓存）深度知识概览 > 五、 针对 KV Cache 的顶尖优化策略 > 3. 连续批处理（Continuous Batching） | 3715:4009 | 116 | 0.562723 |

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

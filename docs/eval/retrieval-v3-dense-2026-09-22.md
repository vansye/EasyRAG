# 离线召回验收报告（子 Issue B）

> 运行时间：2026-09-22T22:27:54+08:00。由离线 CLI 生成，未调用 LLM。

## 验收边界

本报告只验证切片与索引的离线召回；资料收录、更新同步、业务问答与完整评估平台需各自的验收证据。
不实现 Agent、改写、重排、拒答或部分覆盖判断；不读取 MySQL，不打开业务持久化 Chroma。
检索策略：dense——只用向量排序。

## 召回结果

| 类别 | hit@1 | hit@3 | hit@5 | hit@10 |
|---|---:|---:|---:|---:|
| Q | 10/12（83.33%） | 12/12（100.00%） | 12/12（100.00%） | 12/12（100.00%） |
| R | 3/3（100.00%） | 3/3（100.00%） | 3/3（100.00%） | 3/3（100.00%） |
| Q+R | 13/15（86.67%） | 15/15（100.00%） | 15/15（100.00%） | 15/15（100.00%） |

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
- 策略：dense；hybrid 候选深度：20。
- 语料：29 篇、146829 字节；222 个 chunk。
- 实际 chunk token 范围：64–490；低于软下限：0 个。
- 文档 embedding 输入合计 43894 token；全部查询合计 364 token。
- 语料指纹（按排序后的路径与原文 SHA-256 汇总）：`3d4b803355606400354a4b4f73f4fde54fc92e352f43ac80e1dd60ffda123425`。
- 黄金集原文件 SHA-256：`4cba06545eac696e56db8c9b5d7fc956d4f04e3f45a9915106479a54d91e3f0b`。

## 耗时

| 阶段 | 秒 |
|---|---:|
| 切片与准备 | 0.871 |
| 文档 embedding | 28.230 |
| 问题 embedding | 1.801 |
| 临时索引与检索 | 1.217 |
| 总计（不含 Python 导入和报告写入） | 32.241 |

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
  --golden-set '../docs/eval/golden-set-v3-hard.md' `
  --tokenizer 'data/tokenizers/bge-m3-5617a9f61b028005a4858fdac845db406aefb181.json' `
  --ollama-url 'http://127.0.0.1:11434' `
  --model 'bge-m3' `
  --dimensions '1024' `
  --max-tokens '512' `
  --min-tokens '64' `
  --batch-size '16' `
  --timeout-seconds '180' `
  --strategy 'dense' `
  --candidates '20' `
  --output '../docs/eval/retrieval-v3-dense-2026-09-22.md'
```

## 逐题明细（检索深度 10，展示前 5）

相似度 = 1 − cosine distance，仅作排序诊断，不代表置信度。位置以原始文件的 UTF-8 字节为单位。

### Q23 · HIT@1

redis-check-aof --fix 是干什么用的？什么时候会用到它？

标注出处：`数据库/Redis/Redis持久化.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/Redis/Redis持久化.md | 28 | 7. 故障恢复与数据一致性 > 7.4 AOF 文件损坏处理 | 15606:16005 | 149 | 0.656744 |
| 2 | 数据库/Redis/Redis持久化.md | 16 | 4. 混合持久化 > 4.1 什么是混合持久化 | 9192:10088 | 231 | 0.620280 |
| 3 | 数据库/Redis/Redis持久化.md | 29 | 小结 | 16005:16678 | 207 | 0.612650 |
| 4 | 数据库/Redis/Redis持久化.md | 22 | 6. 生产环境配置方案 > 6.2 方案二：AOF only（数据敏感场景） | 12317:12696 | 139 | 0.612085 |
| 5 | 数据库/Redis/Redis持久化.md | 13 | 3. AOF（追加日志持久化） > 3.4 AOF 重写（Compaction） | 6530:7346 | 248 | 0.605724 |

### Q24 · HIT@2

Linux 里的 pdflush 线程负责什么？

标注出处：`数据库/知识条目/操作系统缓冲区（OS Buffer）.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/Redis/Redis持久化.md | 24 |  | 13171:14059 | 310 | 0.499454 |
| 2 | 数据库/知识条目/操作系统缓冲区（OS Buffer）.md | 3 | 详细 > 重点 | 757:1905 | 356 | 0.494750 |
| 3 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 2 | 详细 > 概念 | 421:670 | 65 | 0.483138 |
| 4 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 1 |  | 0:421 | 120 | 0.477618 |
| 5 | 数据库/知识条目/Redis 内存淘汰.md | 1 |  | 0:385 | 106 | 0.476512 |

### Q25 · HIT@1

activeExpireCycle 每秒跑几次？由哪个参数控制？

标注出处：`数据库/Redis过期Key处理.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/Redis过期Key处理.md | 11 | 4. 定期删除详解 > 4.2 内部实现细节 | 5262:6074 | 246 | 0.628991 |
| 2 | 数据库/Redis过期Key处理.md | 12 | 4. 定期删除详解 > 4.3 可调参数 | 6074:6603 | 156 | 0.540530 |
| 3 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 5 |  | 3393:5047 | 426 | 0.528952 |
| 4 | 数据库/Redis过期Key处理.md | 26 | 小结 | 12974:13731 | 227 | 0.508027 |
| 5 | 数据库/Redis/Redis持久化.md | 7 | 2. RDB（快照持久化） > 2.3 触发方式 | 3265:4067 | 251 | 0.504361 |

### Q26 · HIT@1

MQA 和 GQA 有什么区别？各自的代价是什么？

标注出处：`大模型应用/知识条目/KV Cache.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/KV Cache.md | 7 | KV Cache（键值缓存）深度知识概览 > 五、 针对 KV Cache 的顶尖优化策略 > 1. 多查询注意力（MQA）与分组查询注意力（GQA） | 2872:3316 | 182 | 0.561858 |
| 2 | 大模型应用/知识条目/KV Cache.md | 12 | KV Cache（键值缓存）深度知识概览 > 七、 总结：关键时间节点速记 | 4839:5543 | 221 | 0.428821 |
| 3 | 大模型应用/知识条目/KV Cache.md | 5 | KV Cache（键值缓存）深度知识概览 > 四、 内存开销：巨大的“吞金兽”（显存瓶颈） | 2187:2744 | 215 | 0.421045 |
| 4 | 大模型应用/知识条目/Rag.md | 2 | RAG（检索增强生成）知识概览 > 二、 为什么需要 RAG？（价值与痛点） | 626:1360 | 241 | 0.415439 |
| 5 | 大模型应用/知识条目/KV Cache.md | 2 | KV Cache（键值缓存）深度知识概览 | 708:1513 | 233 | 0.391477 |

### Q27 · HIT@1

epoll 的 ET 和 LT 有什么区别？默认是哪个？

标注出处：`数据库/知识条目/epoll（Linux 高并发网络模型）.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 3 | 详细 > 重点 | 670:1636 | 298 | 0.584547 |
| 2 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 2 | 详细 > 概念 | 421:670 | 65 | 0.549855 |
| 3 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 1 |  | 0:421 | 120 | 0.507402 |
| 4 | 大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md | 2 | 详细 > 概念 | 270:981 | 193 | 0.483890 |
| 5 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 4 | 详细 > 重点 | 1636:2393 | 235 | 0.462945 |

### Q28 · HIT@1

Redis 的 WATCH 是干嘛的？和 MULTI/EXEC 怎么配合？

标注出处：`数据库/知识条目/Redis 事务.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/Redis 事务.md | 3 | 详细 > 重点 | 621:2183 | 447 | 0.746621 |
| 2 | 数据库/知识条目/Redis 事务.md | 1 |  | 0:344 | 91 | 0.663866 |
| 3 | 数据库/Redis过期Key处理.md | 12 | 4. 定期删除详解 > 4.3 可调参数 | 6074:6603 | 156 | 0.619649 |
| 4 | 数据库/知识条目/Redis 事务.md | 2 | 详细 > 概念 | 344:621 | 68 | 0.599410 |
| 5 | 数据库/知识条目/缓存.md | 3 | 详细 > 重点 | 731:2293 | 423 | 0.597044 |

### Q29 · HIT@1

docker 的 volumn 删了容器还在不在？

标注出处：`JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 1 |  | 0:549 | 134 | 0.637967 |
| 2 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 2 | 详细 > 重点 | 549:1867 | 436 | 0.621177 |
| 3 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 1 |  | 0:306 | 88 | 0.528514 |
| 4 | JavaWeb/知识条目/后端/Bean.md | 2 | 详细 > 重点 | 407:691 | 80 | 0.515196 |
| 5 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 3 | 详细 > 重点 | 666:1785 | 344 | 0.499866 |

### Q30 · HIT@1

cookie 和 seesion 有啥区别，各存在哪一边？

标注出处：`JavaWeb/知识条目/后端/Cookie 和 Session.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/后端/Cookie 和 Session.md | 2 | 详细 > 重点 | 555:885 | 98 | 0.687256 |
| 2 | JavaWeb/知识条目/后端/Cookie 和 Session.md | 1 |  | 0:555 | 142 | 0.593785 |
| 3 | 数据库/知识条目/缓存.md | 2 | 详细 > 概念 | 365:731 | 97 | 0.460928 |
| 4 | 数据库/Redis过期Key处理.md | 15 | 5. 两种策略的配合机制 > 5.2 工作机制对比 | 7941:8407 | 180 | 0.448875 |
| 5 | 数据库/知识条目/操作系统缓冲区（OS Buffer）.md | 2 | 详细 > 概念 | 456:757 | 85 | 0.442383 |

### Q31 · HIT@3

网络数据在真正发出去之前先暂存在哪里？

标注出处：`数据库/知识条目/操作系统缓冲区（OS Buffer）.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/缓存.md | 2 | 详细 > 概念 | 365:731 | 97 | 0.606966 |
| 2 | 数据库/知识条目/缓存.md | 1 |  | 0:365 | 97 | 0.561220 |
| 3 | 数据库/知识条目/操作系统缓冲区（OS Buffer）.md | 2 | 详细 > 概念 | 456:757 | 85 | 0.555779 |
| 4 | 数据库/知识条目/缓存.md | 3 | 详细 > 重点 | 731:2293 | 423 | 0.545704 |
| 5 | JavaWeb/知识条目/后端/Tomcat.md | 2 | 详细 > 重点 | 450:646 | 65 | 0.534090 |

### Q32 · HIT@1

Tomcat 是数据库吗？它和 Servlet 是什么关系？

标注出处：`JavaWeb/知识条目/后端/Tomcat.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/后端/Tomcat.md | 2 | 详细 > 重点 | 450:646 | 65 | 0.698940 |
| 2 | JavaWeb/知识条目/后端/Tomcat.md | 1 |  | 0:450 | 121 | 0.679238 |
| 3 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 4 | Interceptor 拦截器技术 | 1560:3447 | 343 | 0.577947 |
| 4 | 数据库/知识条目/ACID.md | 1 |  | 0:345 | 89 | 0.514443 |
| 5 | 数据库/知识条目/ACID.md | 3 | 详细 > 重点 | 677:1848 | 299 | 0.494747 |

### Q33 · HIT@1

Pinia 的 state 刷新页面就没了，用哪个 plugin 能存到 localStorage？

标注出处：`JavaWeb/Web前端设计/Pinia状态管理.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/Web前端设计/Pinia状态管理.md | 18 | 6. 状态持久化 > 6.2 使用 pinia-plugin-persistedstate（推荐） | 12796:13696 | 291 | 0.726505 |
| 2 | JavaWeb/Web前端设计/Pinia状态管理.md | 28 | 10. 常见用法总结 > 10.4 响应式追踪 | 19175:19480 | 105 | 0.620184 |
| 3 | JavaWeb/Web前端设计/Pinia状态管理.md | 29 | 小结 | 19480:20358 | 252 | 0.612527 |
| 4 | JavaWeb/Web前端设计/Pinia状态管理.md | 4 | 2. Pinia 简介 | 2346:2749 | 127 | 0.606127 |
| 5 | JavaWeb/Web前端设计/Pinia状态管理.md | 16 |  | 11430:12139 | 202 | 0.597387 |

### Q34 · HIT@1

Nginx 反向代理的配置里 proxy_pass 后面写什么？

标注出处：`JavaWeb/Web前端设计/Nginx 反向代理服务器.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/Web前端设计/Nginx 反向代理服务器.md | 3 | 详细 > 重点 | 695:1854 | 325 | 0.694042 |
| 2 | JavaWeb/Web前端设计/Nginx 反向代理服务器.md | 1 |  | 0:418 | 118 | 0.653014 |
| 3 | JavaWeb/Web前端设计/Nginx 反向代理服务器.md | 2 | 详细 > 概念 | 418:695 | 79 | 0.604501 |
| 4 | JavaWeb/JavaWeb后端开发/会话管理与登陆校验/Interceptor拦截器技术.md | 16 | Interceptor 拦截器技术 > 拦截路径的配置 > `addPathPatterns()` 配置要拦截的路径 | 11397:11765 | 161 | 0.520720 |
| 5 | JavaWeb/知识条目/后端/Interceptor拦截器.md | 2 | 详细 > 重点 | 554:834 | 85 | 0.519908 |

### N11 · 未评分

pdflush 在哪个 Linux 内核版本被 flusher 线程取代了？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/操作系统缓冲区（OS Buffer）.md | 3 | 详细 > 重点 | 757:1905 | 356 | 0.487631 |
| 2 | 数据库/知识条目/Redis 内存淘汰.md | 4 | 详细 > 重点 | 1585:2637 | 337 | 0.461669 |
| 3 | 数据库/知识条目/Redis 内存淘汰.md | 1 |  | 0:385 | 106 | 0.460511 |
| 4 | 数据库/知识条目/操作系统缓冲区（OS Buffer）.md | 1 |  | 0:456 | 130 | 0.457793 |
| 5 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 2 | 详细 > 重点 | 549:1867 | 436 | 0.453536 |

### N12 · 未评分

pinia-plugin-persistedstate 能把状态存到 IndexedDB 吗？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/Web前端设计/Pinia状态管理.md | 18 | 6. 状态持久化 > 6.2 使用 pinia-plugin-persistedstate（推荐） | 12796:13696 | 291 | 0.683029 |
| 2 | JavaWeb/Web前端设计/Pinia状态管理.md | 28 | 10. 常见用法总结 > 10.4 响应式追踪 | 19175:19480 | 105 | 0.570616 |
| 3 | JavaWeb/Web前端设计/Pinia状态管理.md | 29 | 小结 | 19480:20358 | 252 | 0.555399 |
| 4 | JavaWeb/Web前端设计/Pinia状态管理.md | 1 |  | 0:566 | 139 | 0.551378 |
| 5 | JavaWeb/Web前端设计/Pinia状态管理.md | 8 | 3. Store 定义 > 3.2 state 状态定义 | 4374:5125 | 206 | 0.548450 |

### N13 · 未评分

epoll 在 Windows 上怎么用？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 2 | 详细 > 概念 | 421:670 | 65 | 0.705564 |
| 2 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 1 |  | 0:421 | 120 | 0.641406 |
| 3 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 3 | 详细 > 重点 | 670:1636 | 298 | 0.622164 |
| 4 | 数据库/知识条目/epoll（Linux 高并发网络模型）.md | 4 | 详细 > 重点 | 1636:2393 | 235 | 0.559978 |
| 5 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 15 | 5. 完整实战：多工具 Agent | 9696:10916 | 377 | 0.493250 |

### P3 · 未评分

Docker 数据卷怎么备份？备份时要注意什么？

不参与召回命中率计分。

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 1 |  | 0:549 | 134 | 0.737631 |
| 2 | JavaWeb/知识条目/部署/Docker/Docker 数据卷（Volume）.md | 2 | 详细 > 重点 | 549:1867 | 436 | 0.703895 |
| 3 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 3 | 详细 > 重点 | 666:1785 | 344 | 0.617896 |
| 4 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 2 | 详细 > 概念 | 306:666 | 90 | 0.576784 |
| 5 | JavaWeb/知识条目/部署/Docker/Docker 镜像.md | 1 |  | 0:306 | 88 | 0.572661 |

### R9 · HIT@1

redis 把 aof 文件搞坏了还能救回来吗？

标注出处：`数据库/Redis/Redis持久化.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 数据库/Redis/Redis持久化.md | 28 | 7. 故障恢复与数据一致性 > 7.4 AOF 文件损坏处理 | 15606:16005 | 149 | 0.757850 |
| 2 | 数据库/Redis/Redis持久化.md | 27 | 7. 故障恢复与数据一致性 > 7.3 手动恢复流程 | 14965:15606 | 240 | 0.711894 |
| 3 | 数据库/Redis/Redis持久化.md | 29 | 小结 | 16005:16678 | 207 | 0.681045 |
| 4 | 数据库/Redis/Redis持久化.md | 25 | 7. 故障恢复与数据一致性 > 7.1 正常重启恢复顺序 | 14059:14481 | 142 | 0.678273 |
| 5 | 数据库/Redis/Redis持久化.md | 22 | 6. 生产环境配置方案 > 6.2 方案二：AOF only（数据敏感场景） | 12317:12696 | 139 | 0.665619 |

### R10 · HIT@1

LangChain 里想让模型只记住最近几轮对话、别记太多，用什么？

标注出处：`大模型应用/LangChain 1.0/核心组件/message/多轮对话.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 12 | 5. LangChain Memory 组件 > 5.2 ConversationBufferWindowMemory（滑动窗口） | 8186:8658 | 152 | 0.706162 |
| 2 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 20 | 7. 与 RAG 结合的多轮对话 > 7.3 简单实现 | 14504:15399 | 297 | 0.649147 |
| 3 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 11 | 5. LangChain Memory 组件 > 5.1 BufferMemory（全量保留） | 7440:8186 | 245 | 0.646680 |
| 4 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 10 |  | 6700:7440 | 241 | 0.642755 |
| 5 | 大模型应用/LangChain 1.0/核心组件/message/多轮对话.md | 13 | 5. LangChain Memory 组件 > 5.3 ConversationSummaryMemory（摘要压缩） | 8658:9634 | 303 | 0.635195 |

### R11 · HIT@1

LangChain 里怎么把一个普通 Python 函数变成模型能调用的工具？

标注出处：`大模型应用/知识条目/Tools.md`

| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |
|---:|---|---:|---|---|---:|---:|
| 1 | 大模型应用/知识条目/Tools.md | 2 | 详细 > 概念 | 260:808 | 144 | 0.704858 |
| 2 | 大模型应用/知识条目/Tools.md | 3 | 详细 > 重点 | 808:2425 | 441 | 0.625657 |
| 3 | 大模型应用/知识条目/invoke、stream 与 batch.md | 2 | 详细 > 概念 | 291:885 | 156 | 0.610833 |
| 4 | 大模型应用/LangChain 1.0/核心组件/Agent/create_agent 与 Agent 输入输出.md | 14 | 5. 完整实战：多工具 Agent | 8598:9696 | 296 | 0.608929 |
| 5 | 大模型应用/知识条目/LangChain 1.0 LCEL 表达式语言.md | 2 | 详细 > 概念 | 270:981 | 193 | 0.604824 |

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

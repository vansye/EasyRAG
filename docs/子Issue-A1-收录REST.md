# 子 Issue A-1：收录 REST 与异步索引接入

> 父 Issue：#2 子 Issue A（资料管理模块）
> 位置：Spring Boot（`server/`）
> 目标用户故事：**U1 —— 我把一篇资料丢进系统，它出现在列表里**

## 一、为什么需要这个增量

索引管线已经建好：切片、embedding、并发闸门、五阶段编排、失败清理全部实现并测试通过。但它只消费 `index_status = 'PENDING'` 的文档——**而目前没有任何代码能创建 PENDING 文档**。

Java 侧至今只暴露 `GET /health` 一个端点，`DocumentIndexingService.index()` 没有调用方。所以三条兜底功能一条都跑不起来。

本增量补上写入侧入口，让 U1 真正跑通。

## 二、范围

**做**

| 能力 | 说明 |
|---|---|
| `POST /api/documents` | 上传 md / txt，落库为 PENDING，立即返回 |
| `GET /api/documents` | 分页列表 |
| 异步索引触发 | 收录后后台推进 PENDING → INDEXED |
| 就绪与恢复入口 | 闸门初态是 RECOVERY_REQUIRED，不做这个收录会被直接拒绝 |

**不做（留给 A-2）**

URL 抓取、更新（PUT）、删除（DELETE）、手动重索引、`GET /api/documents/{id}` 详情、`/chunks` 透明度接口。

理由：U1 只需要"丢进去 + 看见它"。更新与删除属于 U2 / U3，和更新同步一起做才能验收完整。

## 三、内部逻辑拆分（用接口表示）

现有组件（已实现，本增量不改）：

```
DocumentIndexingService.index(documentId) → IndexingResult
    五阶段编排，消费 PENDING 文档
RagOperationGate.tryAcquire(MUTATION|QUERY|RECOVERY) → Admission
    五态闸门，MUTATION 仅在 READY 态放行
DocumentIndexRepository / ChunkRepository
    状态流转与 chunk 落库
```

本增量新增四个组件：

```
DocumentController                        # REST 入口
  POST /api/documents  (multipart file)   → 201 DocumentCreated
  GET  /api/documents  (status,page,size) → 200 DocumentPage
  POST /api/admin/ready                   → 200 ReadinessResult

DocumentIntakeService                     # 收录：解析 → 校验 → 落库
  intake(filename, bytes) → DocumentCreated
    1. 校验大小（≤1MB）与类型（md/txt）
    2. 解码 UTF-8（严格，失败即 400）
    3. 解析 frontmatter → title / tags
    4. 规范化正文 → content_hash
    5. INSERT document (status=PENDING)
    6. 提交后触发异步索引

DocumentQueryRepository                   # 读侧
  insertPending(NewDocument) → long
  findPage(status, page, size) → DocumentPage
    chunk_count 用 COUNT(*) 实时算（A1-3），不读冗余列
  findPendingIds() → List<Long>           # 供恢复扫描（A1-2）

IndexingTrigger                           # 异步推进
  @Async submit(documentId)
    调 DocumentIndexingService.index()
    单线程执行器（A-1 已裁决：不引入 MQ）
```

以及就绪入口：

```
ReadinessService
  ready() → ReadinessResult
    1. tryAcquire(RECOVERY)，拿不到租约 → 409（有未结束的变更）
    2. 闸门转 READY
    3. 扫描全部 PENDING 文档，逐篇 IndexingTrigger.submit()（A1-2）
    4. 返回 { state, recovered: 提交篇数 }
```

## 四、用示例把接口串起来

**上传一篇 `KV Cache.md`（U1 正常路径）**

```
① POST /api/documents   multipart: file=KV Cache.md (5453 字节)
   │
   ├─ DocumentIntakeService.intake()
   │    大小 5453 ≤ 1MB              ✓
   │    UTF-8 严格解码                ✓
   │    frontmatter 无 → title 取正文首个 # 标题 → "KV Cache"
   │    tags → []
   │    content_hash = SHA-256(规范化正文)
   │    INSERT document → id=42, status=PENDING
   │
   ├─ 事务提交后 → IndexingTrigger.submit(42)
   │
   └─ 201 { id:42, title:"KV Cache", source_type:"UPLOAD",
            index_status:"PENDING" }        ← 不等索引，立即返回

② 后台线程：DocumentIndexingService.index(42)
   闸门 tryAcquire(MUTATION) → READY，取得租约
   LOAD_DOCUMENT → CHUNK(12片) → SAVE_CHUNKS → EMBED → MARK_INDEXED
   闸门 confirmCompletion() → READY

③ GET /api/documents
   200 { total:1, items:[{ id:42, title:"KV Cache",
                           index_status:"INDEXED", chunk_count:12 }] }
```

**闸门未就绪时（必须能解释的路径）**

```
① POST /api/documents → 201 PENDING（收录本身不受闸门约束，正常入库）
② IndexingTrigger → tryAcquire(MUTATION) 返回空租约（态为 RECOVERY_REQUIRED）
   → 不改状态，文档停在 PENDING，记录原因
③ GET /api/documents → index_status 显示 PENDING，不是 FAILED
④ POST /api/admin/ready
   → 闸门转 READY，扫描到这篇 PENDING，重新提交
   → 200 { state:"READY", recovered:1 }
⑤ 后台推进 → GET /api/documents 显示 INDEXED
```

**为什么收录本身不过闸门**：见 §九。

## 五、数据原型

复用子 Issue A §二 的 `document` / `chunk` 表，**不加新表、不改列**。

本增量只写入这些字段：

```
id, source_type='UPLOAD', source_uri=原始文件名, title, content,
content_hash, tags, index_status='PENDING', chunk_count=0, created_at, updated_at
```

`title` 降级顺序（子 Issue A §一.1 已定）：

```
frontmatter.title → 正文首个 # 一级标题 → 文件名去扩展名
```

## 六、模块边界

**本增量负责**：HTTP 入口、正文解析与校验、落库为 PENDING、触发异步索引、就绪恢复入口。

**不负责**：怎么切片（模块 B）、检索与生成（模块 C）。索引编排本身已由 `DocumentIndexingService` 实现，本增量只调用它，不改它。

## 七、验收

- [ ] 上传一篇 md → 201 返回 PENDING，列表立即可见（不等索引）
- [ ] 稍后查列表 → 状态变为 INDEXED，`chunk_count` 与实际切片数一致
- [ ] **一篇完全没有 frontmatter 的 md 能正常收录并索引**（不写 frontmatter 的用户不受影响）
- [ ] 有 frontmatter 的 md → title 与 tags 正确提取
- [ ] 超过 1 MB → 400，提示含实际大小与上限
- [ ] 非 UTF-8 字节 → 400，不入库
- [ ] 不支持的扩展名 → 400，不入库
- [ ] 闸门处于 RECOVERY_REQUIRED 时上传 → 文档入库为 PENDING，不是 FAILED
- [ ] `POST /api/admin/ready` → 闸门转 READY，返回 `recovered` 等于被重新提交的 PENDING 篇数
- [ ] 恢复后这些 PENDING 文档能被推进到 INDEXED
- [ ] 有未结束的索引变更时调 `/api/admin/ready` → 409，不强行夺取闸门
- [ ] 未调用 `/api/admin/ready` 时上传 → 文档停在 PENDING，且不被判为 FAILED
- [ ] 并发上传两篇 → 两篇最终都 INDEXED（闸门串行化，不互相踩）
- [ ] 列表的 `chunk_count` 与该文档 `chunk` 表实际行数一致（实时 COUNT，不读冗余列）

## 八、已裁决

| # | 问题 | 结论 | 理由 |
|---|---|---|---|
| A1-1 | 闸门初态如何转到 READY | **手动端点 `POST /api/admin/ready`** | 保持子 Issue A 的 A-2 裁决无例外：仅重启 Java 不会终止旧 Python 请求，自动就绪等于在没确认旧执行者退出的情况下放行。显式端点让"谁确认过"这件事可追溯，代价只是 demo 多一步 |
| A1-2 | 停在 PENDING 的文档如何重新推进 | **恢复时扫描全部 PENDING 逐篇提交** | 否则 A-1 阶段存在"上传了但永远不索引"的死角——重索引端点要到 A-2 才有。恢复本来就是"把系统拉回一致状态"，顺带把积压推进去符合这个语义 |
| A1-3 | 列表的 `chunk_count` 从哪来 | **实时 `COUNT(*)`** | 与 A-6 裁决的语义（MySQL 现存 chunk 行数）天然一致，不会出现冗余字段与事实不符；几百篇规模下性能无差别。维护计数列则需要改动已测试通过的落库代码，收益不抵风险 |

### A1-1 的接口

```
POST /api/admin/ready
  → 200 { state: "READY", recovered: int }    # recovered = 重新提交的 PENDING 篇数
  → 409 { state: "MUTATING"|"RECOVERING" }    # 有未结束的变更，不能恢复
```

`recovered` 是本次扫描并提交的 PENDING 篇数（A1-2），不是索引成功数——索引在后台异步进行，结果看各文档的 `index_status`。

## 九、一条自行裁决的边界

**收录本身不过闸门，只有索引过闸门。**

闸门保护的是索引一致性，不是入库。用户上传的正文进 MySQL 是无条件安全的（MySQL 是真相源），停在 PENDING 只表示"还没索引"，事后恢复即可。若收录也被闸门拒掉，用户会丢失刚写完的内容——两边代价不对等。

所以闸门未就绪时的行为是：文档正常入库为 `PENDING`，**不是 `FAILED`**。`FAILED` 表示"试过且失败了"，而这里根本没试过，用错状态会让后续恢复逻辑无法区分这两种情况。

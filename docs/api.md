# EasyRAG 公共 API

供前端开发与接口验收使用。统一后端默认地址为 `http://127.0.0.1:8080`，Vue 通过同源 `/api`、`/health` 访问。在线 [Swagger](http://127.0.0.1:8080/docs) 可查看路由和请求参数；当前响应未全部声明为 OpenAPI schema，字段、错误状态和示例以本文为准。启动与配置见 [README](../README.md)，恢复操作见 [切换与回退](fastapi-cutover.md)。

## 路由

除文件上传外，请求体使用 `application/json`。成功响应为 JSON；删除资料成功时没有响应体。

| 方法与路径 | 参数 / 请求体 | 成功状态与含义 |
|---|---|---|
| `GET /health` | 无 | `200`，进程与依赖的分层健康状态 |
| `GET /api/runtime` | 无 | `200`，运行门禁状态和模型公开信息 |
| `POST /api/admin/ready` | 无 | `200`，显式检查一致性并重新提交待处理资料 |
| `POST /api/documents` | `multipart/form-data`，必填文件字段 `file` | `201`，资料已入库，返回 `id/title/source_type/index_status` |
| `GET /api/documents` | `page=0`、`size=20`、可选 `status`、`q` | `200`，`{total, items}` |
| `GET /api/documents/{document_id}` | 路径 ID | `200`，资料详情 |
| `GET /api/documents/{document_id}/chunks` | 路径 ID | `200`，`{items}`，按 `seq` 排序 |
| `PUT /api/documents/{document_id}` | `{"content":"完整正文"}` | `200`，`{id, index_status, reindexed}` |
| `POST /api/documents/{document_id}/reindex` | 路径 ID，无请求体 | `202`，`{id, index_status, reindexed}`，重处理已提交 |
| `DELETE /api/documents/{document_id}` | 路径 ID | `204`，撤下向量、软删除资料并删除切片 |
| `POST /api/questions` | `{"question":"问题"}` | `200`，回答、出处与检索过程 |
| `GET /api/model-config` | 无 | `200`，当前生效的配置，固定六个公开字段 |
| `PUT /api/model-config` | `provider/model/base_url`，可选 `api_key` | `200`，保存后的六字段配置 |
| `DELETE /api/model-config` | 无 | `200`，移除本机覆盖后重新读取启动配置 |

`document_id` 按有符号 64 位整数解析，格式错误或溢出返回 `400`；不存在或已删除的资料返回 `404`。

| 列表参数 | 规则 |
|---|---|
| `page` | 从 `0` 开始，非负有符号 32 位整数；缺省或 `page=` 均为 `0` |
| `size` | `1..100` 的整数；缺省或 `size=` 均为 `20` |
| `status` | `PENDING / INDEXING / INDEXED / FAILED`，忽略大小写；缺省或全空白不筛选；非空值不要带首尾空格 |
| `q` | 去掉首尾空白后，在标题中作字面包含搜索；`%`、`_` 不是通配符；缺省或空白不筛选 |

列表按 `updated_at` 降序、`id` 降序排列，不包含已删除资料。`page=1.0` 等小数写法不会转换为整数。

## 资料与切片

单次上传一份 `.md` 或 `.txt`，后缀忽略大小写，内容必须是非空 UTF-8 文本；开头的 UTF-8 BOM 会移除。文件产品上限为 **1 MiB（1,048,576 字节）**，上传请求整体另有 **4 MiB** 硬上限，包含 multipart 开销且对没有 `Content-Length` 的请求同样计数。两种超限都返回 `400`。更新正文也受 1 MiB 上限约束。

标题优先取 frontmatter 的 `title`，再取正文第一个一级标题，最后取文件名；`tags` 来自支持的 frontmatter 标签数组。公开上传只产生 `source_type: "UPLOAD"`，没有 URL 抓取接口。

以下 JSON 是字段示例，ID、时间、分数和 token 数以实际响应为准。上传成功只说明已保存为 `PENDING`，不表示索引完成；待恢复或门禁忙时资料可以保留在 `PENDING`。

```json
{"id":7,"title":"Note","source_type":"UPLOAD","index_status":"PENDING"}
```

列表响应：

```json
{
  "total": 1,
  "items": [{
    "id": 7,
    "title": "Note",
    "source_type": "UPLOAD",
    "tags": [],
    "index_status": "INDEXED",
    "chunk_count": 1,
    "updated_at": "2026-09-15T12:30:00"
  }]
}
```

详情响应：

```json
{
  "id": 7,
  "source_type": "UPLOAD",
  "source_uri": "note.md",
  "title": "Note",
  "content": "# Note\n中文",
  "tags": [],
  "index_status": "INDEXED",
  "index_error": null,
  "chunk_count": 1,
  "created_at": "2026-09-15T12:30:00",
  "updated_at": "2026-09-15T12:30:00"
}
```

`index_status` 为 `PENDING / INDEXING / INDEXED / FAILED`；失败诊断在 `index_error`。`chunk_count` 来自实际保存的切片数。详情不暴露内部 `content_hash`、`indexed_at`、`deleted_at`。

切片响应：

```json
{
  "items": [{
    "id": 11,
    "seq": 0,
    "text": "# Note\n中文",
    "byte_start": 0,
    "byte_end": 13,
    "heading_path": "Note",
    "token_count": 8
  }]
}
```

`seq` 从 `0` 开始；`byte_start/byte_end` 是原文 UTF-8 字节区间 `[start, end)`，不能直接作为 JavaScript 字符串下标。`heading_path` 可为空字符串或 `null`；切片列表不重复返回 `document_id`。

更新或重处理提交后的示例：

```json
{"id":7,"index_status":"PENDING","reindexed":true}
```

更新先统一换行并去除正文首尾空白后比较哈希；哈希未变时仅更新修改时间，保留原正文和切片，返回当前状态及 `reindexed: false`。正文改变或显式重处理会撤下旧向量并异步建立新索引，最终状态要从资料接口读取。

## 问答

`question` 必须是非空字符串，最多 2,000 个 Unicode 码点，保留原始问题文本。当前每次问题检索一次，默认 `top_k=5`；HTTP 不开放 `top_k` 或检索轮数参数。每个问题固定一个回答模型会话，配置变更从下一次问题生效。

```json
{
  "answer": "资料中记录了中文内容。[1]",
  "status": "ANSWERED",
  "sources": [{
    "chunk_id": 11,
    "document_id": 7,
    "title": "Note",
    "text": "# Note\n中文",
    "byte_start": 0,
    "byte_end": 13,
    "heading_path": "Note"
  }],
  "trace": [{
    "round_index": 1,
    "query": "资料记录了什么？",
    "retrieved": [{"chunk_id":11,"document_id":7,"score":0.88,"rank":1}],
    "decision": "SUFFICIENT"
  }]
}
```

`status` 为 `ANSWERED / PARTIAL / REFUSED`，对应判定 `SUFFICIENT / PARTIAL / NONE`。依据不足仍是正常 `200`，`answer` 为拒答说明、`sources` 为空，但 `trace` 可以保留检索结果。检索、模型调用、判定输出或出处查询发生技术失败时返回 `502`，不会伪装成拒答。

`sources` 按最终检索排名排列，以 `chunk_id` 关联 `trace.retrieved`；引用编号对应从 `1` 开始的 `rank`。来源列表表示提供给回答的依据，正文实际使用了哪些引用需另行统计。若出处无法与切片 ID 对齐，服务要求恢复并返回 `503`。

## 模型配置

保存请求示例使用本地 Ollama 的占位 key，不是真实凭据：

```json
{
  "provider": "openai",
  "model": "qwen2.5:7b-instruct-q4_K_M",
  "base_url": "http://localhost:11434/v1",
  "api_key": "ollama"
}
```

`provider` 只接受 `openai / deepseek`，Ollama 使用 `openai` 兼容协议。`model` 去首尾空白后须为 `1..200` 字符；`base_url` 最多 2,048 字符且必须是 HTTP(S)，不能包含用户信息、查询参数或片段；`api_key` 最多 4,096 字符。请求不接受额外字段。

`api_key` 可省略、为 `null` 或留空，但只有服务类型和规范化后的接口地址与当前配置一致时才能沿用密钥；首次配置或更换目标地址必须提供密钥。保存只校验字段与持久化，能否回答需实际提问验证。

GET、PUT、DELETE 成功时都只返回以下六个字段：

```json
{
  "configured": true,
  "provider": "openai",
  "model": "qwen2.5:7b-instruct-q4_K_M",
  "base_url": "http://localhost:11434/v1",
  "api_key_configured": true,
  "source": "local"
}
```

`source` 为 `local` 或 `environment`。没有可用启动配置时返回 `configured: false`、`api_key_configured: false`、空 `model`、默认 `openai` 地址及 `source: "environment"`。覆盖文件损坏或不可读则返回 `503`。

F 模块负责原子保存和移除本机覆盖文件。所有模型配置响应（包括错误与尾斜杠重定向）都带 `Cache-Control: no-store`。API 不回传密钥，错误不回显提交的凭据或上游响应正文；浏览器不应缓存密钥。切换回答模型不重建向量。

## 健康与运行门禁

`GET /health` 的顶层 `status: "UP"` 只代表进程能响应。`db.status` 单独报告 MySQL/schema 状态；`retrieval` 包含整体 `status` 和 `chroma`、`embedding`、`tokenizer` 的分层状态。依赖 DOWN 时健康接口仍返回 `200`，不能只看 HTTP 状态。

`GET /api/runtime` 示例：

```json
{
  "state": "RECOVERY_REQUIRED",
  "rag_available": true,
  "llm": {"configured":true,"provider":"openai","model":"qwen2.5:7b-instruct-q4_K_M"},
  "embedding": {"provider":"ollama","model":"bge-m3","dim":1024}
}
```

`rag_available` 保留前端兼容语义，表示统一服务可达；检索依赖失败时也为 `true`。此接口只读，不探测模型网络、不加载 tokenizer、不确认恢复。回答模型未配置或无法读取时，`llm` 为 `{"configured":false,"provider":null,"model":null}`。

| `state` | 含义 |
|---|---|
| `RECOVERY_REQUIRED` | 启动或变更未确认一致；需人工检查并确认恢复 |
| `READY` | 门禁允许开始操作 |
| `QUERYING` | 问答进行中，暂停资料变更 |
| `MUTATING` | 资料变更或索引进行中，暂停问答与其他变更 |
| `RECOVERING` | 正在核对资料、切片和向量 |

`POST /api/admin/ready` 检查依赖，以及所有已索引资料的切片 ID、内容和元数据是否与向量一致；遗留 `INDEXING`、缺失或多余向量等会拒绝就绪。通过后开放门禁并重新提交 `PENDING`，示例为 `{"state":"READY","recovered":0}`。`recovered` 是重新提交数，不是已完成数；后台处理已开始时响应状态也可能是 `MUTATING`。页面必须由用户点击「确认就绪」，不会自动放行。

## 错误

普通错误结构为 `{"error":"可展示的说明"}`；门禁与恢复错误额外携带 `state`。参数校验统一返回 **400，不返回默认 422**；包括 JSON 格式错误、字段类型错误、缺少必填项与整数溢出。

| 状态码 | 触发条件 |
|---|---|
| `400` | 请求不合法、文件类型/大小/编码不支持、模型配置被拒绝 |
| `404` | 资料不存在或已删除；路由不存在 |
| `405` | 路由不支持该请求方法 |
| `409` | 更新、重处理、删除或确认就绪遇到运行门禁忙 |
| `502` | 问答技术失败，包括未配置回答模型或上游调用失败 |
| `503` | 数据库/模型配置不可用、需恢复、变更未确认完成；问答遇到门禁忙也为 `503` |
| `500` | 未预期的服务异常，返回脱敏通用说明 |

例如未就绪时提问：

```json
{"error":"当前操作暂不可用","state":"RECOVERY_REQUIRED"}
```

不存在公开的 `/chunk`、`/embed`、`/query`、`/reset`、`/index/reset` 等内部操作路由；索引重建只通过停服维护命令执行。

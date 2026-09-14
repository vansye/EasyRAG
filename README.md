# EasyRAG

个人知识库问答系统。收录笔记、文档与链接；基于知识库内容问答，答案附带可点开的出处；资料更新或删除后，问答结果随之更新。知识库中没有的内容，系统明确拒答，不编造。

> 回声实验室招新项目 · 题目一（个人知识库管理）。当前已提供连接实际后端的「资料库 / 知识问答」本地工作台，按里程碑继续迭代。

## 技术栈

| 层 | 选型 | 职责 |
|---|---|---|
| 前端 | Vue 3 + TypeScript + Vite | 问答界面、资料管理、检索过程展示 |
| 知识管理服务 | Spring Boot 4.1.0 + MySQL 8 | 对外全部 REST API、资料管理、评估数据存储（唯一真相源） |
| RAG 引擎 | Python + FastAPI | 切片、embedding、检索、agent 循环、生成 |
| 模型 | 可配置 | LLM 与 embedding 按配置切换厂商（provider + model + dim 三元组） |

## 架构

Spring Boot 与 Python 按职责划分：Spring Boot 拥有数据与入口，全部业务数据落 MySQL；Python 只做 RAG 计算，不持有业务数据，其向量与词法索引为派生副本，可随时从 MySQL 全量重建。

```
写入流（收录 / 更新 / 删除资料）：

收录入口 ──REST──► Spring Boot ──原文+内容哈希──► MySQL document
                        │
                        └──原文──► Python 切片 ──chunks──► Spring Boot 落库（chunk 文本+元数据）
                                          Spring Boot ──chunks──► Python 建索引（派生）
                                                                    ├─ Chroma 向量索引
                                                                    └─ BM25 词法索引
变更：内容哈希比对 → 旧 chunk 失效 → 重索引 → 问答结果同步更新

问答流（提问）：

Vue ──REST──► Spring Boot ──问题──► Python RAG 引擎
                                        agent 循环：检索 → 不足则改写重查 → 仍不足则拒答
                  ┌──答案 + chunk_id[] + 检索 trace────────────────────┘
                  ▼
            Spring Boot 用 chunk_id 查 MySQL，补全出处定位（文档、标题、位置）
                  ▼
Vue 展示：答案 + 出处 + 检索过程（轮次、检索片段、正文实际引用）
```

## 目录结构

```
EasyRAG/
├── frontend/      # Vue 3 资料库与知识问答工作台
├── server/        # Spring Boot 知识管理服务（M1 已落地）
├── rag-service/   # Python RAG 引擎（M1 已落地）
├── docs/          # 设计文档、Issue 底稿、黄金问答集
└── sample-knowledge/  # 样例语料（29 篇），供评估使用
```

前端启动、交互范围与验证方式见 [frontend/README.md](frontend/README.md)。

## 依赖

| 组件 | 版本 | 说明 |
|---|---|---|
| JDK | 17 | 本机实测 17；Spring Boot 4.1 要求 17+ |
| Maven | 不需要 | 用仓库自带的 `./mvnw` |
| Python | 3.14 | 实测 3.14.6；Chroma 1.5.9 有 cp314 wheel |
| MySQL | 8.0 | 库会自动创建（`createDatabaseIfNotExist`），表由 Flyway 迁移建 |
| Ollama | 任意 | 仅在用本地 embedding 时需要 |

## 本地启动

### 1. 知识管理服务（端口 8080）

数据库凭据从环境变量读，不写进代码：

```bash
cd server
export MYSQL_USER=<用户名>          # Windows PowerShell: $env:MYSQL_USER="<用户名>"
export MYSQL_PASSWORD=<密码>
./mvnw spring-boot:run
```

启动时 Flyway 自动建 `document` 与 `chunk` 表。验证：

```bash
curl http://localhost:8080/health
# {"status":"UP","service":"easyrag-server","db":{"database":"mysql","status":"UP"}}
```

`db.status` 为 `DOWN` 表示连不上库（凭据或服务问题）——此时进程仍会正常起，健康检查如实报告。

### 2. RAG 引擎（端口 8000）

```bash
cd rag-service
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Linux/macOS: .venv/bin/python
cp .env.example .env                                      # 然后填入 LLM 配置（见下方"LLM 配置"）
```

默认配置用 Ollama 的 `bge-m3`（1024 维）。除拉模型外还需下载 tokenizer（约 16 MB，被 .gitignore 挡在仓库外，不下载则 `/chunk` 返回 503 `TOKENIZER_UNAVAILABLE`）：

```bash
ollama pull bge-m3
mkdir -p data/tokenizers   # Windows PowerShell: New-Item -ItemType Directory -Force data/tokenizers
curl -L -o data/tokenizers/bge-m3-5617a9f61b028005a4858fdac845db406aefb181.json \
  https://huggingface.co/BAAI/bge-m3/resolve/5617a9f61b028005a4858fdac845db406aefb181/tokenizer.json
.venv/Scripts/python -m app
```

验证：

```bash
curl http://localhost:8000/health
```

`embedding.status` 为 `DOWN` 且 `error` 为 `MODEL_NOT_FOUND` 表示配置的模型没拉下来，响应里会列出实际可用的模型。换模型需同时改 `EMBEDDING_MODEL` 与 `EMBEDDING_DIM`——两者不一致时服务拒绝启动并提示重建（维度错配若不拦住，报错会推迟到检索时才爆，且表现为距离计算异常）。

**LLM 配置**：可在网页「知识问答 → 模型 → 配置回答模型」填写，也可使用 `.env` 中的 `LLM_PROVIDER`、`LLM_MODEL`、`LLM_BASE_URL`、`LLM_API_KEY`。模板里的占位符需替换为自己的配置；模型名称与密钥缺失时，问答返回 503 `LLM_NOT_CONFIGURED`。DeepSeek / OpenAI / 本地 Ollama 示例见 `.env.example`；Ollama 使用 OpenAI 兼容端点 `http://localhost:11434/v1`，占位 key 填 `ollama`。模板将单次模型超时设为 180 秒，按本地 7b 的较慢响应预留。

网页配置优先于启动配置，保存到已被 Git 忽略的 `rag-service/config/llm.json`（可用 `LLM_CONFIG_FILE` 指定路径），下一次提问生效；服务重启后保留。API Key 不回传，留空仅在服务类型和接口地址不变时沿用。「恢复启动配置」移除这个文件，不改 `.env`。嵌入模型仍通过后端配置，网页展示其实际名称与维度。

### 3. 前端工作台（端口 5173）

在 Python 与 Java 服务启动后，另开一个终端：

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5173
```

打开 http://127.0.0.1:5173/ 。开发代理把 `/api` 和 `/health` 转发到 Java 8080。服务重启后，页面会显示「确认就绪」按钮，需明确点击后再使用问答。模型入口展示实际回答与检索模型，并提供回答模型配置面板。

### 4. 测试

```bash
cd server && ./mvnw test        # 单元测试，不需要数据库
cd server && ./mvnw verify      # 追加集成测试，需要 MySQL 与本地凭据
cd rag-service && .venv/Scripts/python -m pytest    # 不需要 Ollama 在线
cd frontend && npm test
cd frontend && npm run build
```

## 进度

| 里程碑 | 状态 |
|---|---|
| M0 样例语料 + 黄金问答集 | 完成（29 篇 / 30 题） |
| M1 双后端骨架与健康检查 | 完成 |
| 前端工作台 | 资料管理、知识问答、引用核验、检索过程与回答模型配置（[#33](https://github.com/vansye/EasyRAG/issues/33)） |
| 收录、索引与问答 | 上传/查询/更新/删除/重建索引、三态问答与手动就绪恢复已实现 |

测试分为 Java 单元及 MySQL 集成、Python 回归、前端逻辑与浏览器交互。最新数量以命令输出与变更记录为准。

## 开发方式

- Issue 驱动：总功能文档 Issue（[#1](https://github.com/vansye/EasyRAG/issues/1)）定义产品边界与模块划分，每个模块一个子 Issue（[#2 资料管理](https://github.com/vansye/EasyRAG/issues/2)、[#3 索引管线](https://github.com/vansye/EasyRAG/issues/3)、[#33 前端工作台](https://github.com/vansye/EasyRAG/issues/33)）；底稿见 [docs/总功能文档-Issue.md](docs/总功能文档-Issue.md)。
- 一个功能一个 PR，关联对应 Issue，描述说明做了什么、为什么。检索类改进的 PR 标题附评估数字变化（如 `混合检索：hit@5 60% → 80%`）。
- 评估口径与黄金问答集见 [docs/eval/golden-set-v1.md](docs/eval/golden-set-v1.md)。

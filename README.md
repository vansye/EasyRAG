# EasyRAG

个人知识库问答系统。收录笔记、文档与链接；基于知识库内容问答，答案附带可点开的出处；资料更新或删除后，问答结果随之更新。知识库中没有的内容，系统明确拒答，不编造。

> 回声实验室招新项目 · 题目一（个人知识库管理）。当前处于文档设计与 Issue 驱动阶段，按里程碑逐 PR 落地。

## 技术栈

| 层 | 选型 | 职责 |
|---|---|---|
| 前端 | Vue 3 + TypeScript + Vite | 问答界面、资料管理、检索过程展示 |
| 知识管理服务 | Spring Boot 4.1.0 + MySQL 8 | 对外全部 REST API、资料管理、评估数据存储（唯一真相源） |
| RAG 引擎 | Python + FastAPI + LangChain 1.x | 切片、embedding、检索、agent 循环、生成 |
| 模型 | 可配置 | LLM 与 embedding 按配置切换厂商 |

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
Vue 展示：答案 + 出处 + 检索过程（重查次数、采用/丢弃的片段）
```

## 目录结构

```
EasyRAG/
├── frontend/      # Vue 3 前端
├── server/        # Spring Boot 知识管理服务
├── rag-service/   # Python RAG 引擎
└── docs/          # 设计文档、Issue 底稿、API 文档
```

## 本地启动（骨架落地后更新为实测命令）

```bash
# 0. MySQL
mysql -u root -e "CREATE DATABASE easyrag CHARACTER SET utf8mb4"

# 1. 知识管理服务（端口 8080）
cd server && mvn spring-boot:run

# 2. RAG 引擎（端口 8000，模型配置见 rag-service）
cd rag-service && pip install -r requirements.txt && python -m app

# 3. 前端（端口 5173）
cd frontend && npm install && npm run dev
```

## 开发方式

- Issue 驱动：总功能文档 Issue（[#1](https://github.com/vansye/EasyRAG/issues/1)）定义产品边界与模块划分，每个模块一个子 Issue；底稿见 [docs/总功能文档-Issue.md](docs/总功能文档-Issue.md)。
- 一个功能一个 PR，关联对应 Issue，描述说明做了什么、为什么。检索类改进的 PR 标题附评估数字变化（如 `混合检索：hit@5 60% → 80%`）。
- 评估口径与黄金问答集随 M0 阶段入库。

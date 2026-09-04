# EasyRAG

个人知识库问答系统——把散落的笔记、文档、链接随手丢进去，用一句话问出答案，且每条结论带可点开的出处。

> 回声实验室招新项目 · 题目一（个人知识库管理）。当前处于**文档设计与 Issue 驱动阶段**，按里程碑逐 PR 落地。

## 核心行为准则

**答不出来就承认。** 拒答优于编造——知识库里没有的内容，系统会明说"库里没有"，而不是拿检索到的碎片拼一个答案。

## 技术栈

| 层 | 选型 | 职责 |
|---|---|---|
| 前端 | Vue 3 + TypeScript + Vite | 问答界面、资料管理、检索过程展示 |
| 知识管理服务 | Spring Boot 3 + MySQL 8 | 全部对外 REST API、资料 CRUD、评估数据存储（**唯一真相源**） |
| RAG 引擎 | Python + FastAPI + LangChain 1.x | 切片、embedding、混合检索、agent 循环、生成（**无状态**） |
| 模型 | 可配置 | LLM 与 embedding 按配置切换厂商，不绑定单一供应商 |

## 为什么是两个后端

Spring Boot 与 Python 各司其职，边界是**职责**而非语言偏好：

- **Spring Boot 拥有数据与入口**：前端只见这一层；资料、切片元数据、评估结果全部落 MySQL。
- **Python 只做计算**：切片、向量、检索、agent 循环、生成。不持有业务数据，随时可重启、可替换。

贯穿全局的一条不变量：**MySQL 是唯一真相源，向量索引是派生物**。向量索引按切片 id 关联 MySQL 中的切片记录，任何时刻可以从 MySQL 全量重建。由此：

- 「资料更新后问答结果同步更新」在结构上成立——文档变更使旧切片失效，重索引后旧答案自然过期；
- 关系型数据库与向量检索各得其所，前者是权威存储，后者是可丢弃的加速结构。

## 数据流

```
写入流（收录 / 更新 / 删除资料）：

Vue ──REST──► Spring Boot ──写──► MySQL（文档 + 切片元数据，内容哈希检测变更）
                    │
                    └──HTTP(文档id+原文)──► Python RAG 引擎
                                              ├─ 切片 → embedding
                                              └─ 按 chunk_id upsert 向量索引（派生物）

问答流（提问）：

Vue ──REST──► Spring Boot ──HTTP(问题)──► Python RAG 引擎
                                            agent 循环：
                                            检索 → 不够则改写重查 → 仍不够则拒答
                    ┌─────────────────────────┘
                    │  返回：答案 + 引用的 chunk_id[] + 检索 trace
                    ▼
              Spring Boot 用 chunk_id 查 MySQL 补全出处定位（文档、标题、原文位置）
                    │
                    ▼
Vue 展示：答案 + 可点开的出处 + 本次检索过程（查了几轮、丢弃了哪些片段）
```

## 目录结构

```
EasyRAG/
├── frontend/      # Vue 3 前端
├── server/        # Spring Boot 知识管理服务
├── rag-service/   # Python RAG 引擎
├── docs/          # 设计文档、总功能 Issue 底稿、API 文档
└── README.md
```

## 本地启动（骨架落地后更新为实测命令）

```bash
# 0. 准备 MySQL
mysql -u root -e "CREATE DATABASE easyrag CHARACTER SET utf8mb4"

# 1. 知识管理服务（端口 8080）
cd server && mvn spring-boot:run

# 2. RAG 引擎（端口 8000，模型厂商见 rag-service/config）
cd rag-service && pip install -r requirements.txt && python -m app

# 3. 前端（端口 5173）
cd frontend && npm install && npm run dev
```

## 开发方式

- **Issue 驱动**：总功能文档 Issue 概述产品边界与模块划分，每个模块一个子 Issue（含数据原型与对外接口的伪代码），见 [docs/总功能文档-Issue.md](docs/总功能文档-Issue.md) 底稿。
- **一个功能一个 PR**，PR 描述关联对应 Issue，讲清做了什么、为什么。检索类改进的 PR 标题带评估数字变化（如 `混合检索：hit@5 60% → 80%`）。
- 评估口径与黄金问答集随 M0 阶段入库，检索质量的每个结论都有可复现的数字支撑。

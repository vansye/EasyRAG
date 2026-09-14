# 回答模型配置与前端交付计划

> 执行方式：当前会话逐步实施、检查；保留已确认的两页 UI。用户已授权创建前端子 Issue 和 PR。

**目标：** 从问答框的模型入口配置回答模型，保存后下一次问答使用新配置；交付关联总 Issue #1 的前端子 Issue 和 PR。

**架构：** 浏览器只请求 Java。Java 提供模型配置接口并转发 Python；Python 保存独立的本机运行配置，每次创建问答模型时读取快照。配置文件不入库，原有环境变量作为启动配置和恢复目标。

**技术栈：** Vue 3 / TypeScript / Pinia、Spring Boot、FastAPI / Pydantic / LangChain。

## 设计与边界

- 复用问答框现有模型信息入口，打开原生 dialog 配置面板；暖灰、白色、深绿视觉不变。
- 支持 OpenAI 兼容接口和 DeepSeek。Ollama 使用 OpenAI 兼容的 `/v1` 地址。
- 表单包含 provider、base_url、model、api_key。已保存密钥不回传；留空仅在服务类型和接口地址不变时沿用，切换接口必须填写新密钥。
- `GET /api/model-config` 返回公开配置和 `api_key_configured`；`PUT` 保存并使用；`DELETE` 恢复启动配置。错误响应不含请求值或上游响应正文。
- Python 对应 `/model-config`；运行配置写入已忽略的 `rag-service/config/llm.json`，原子替换文件。环境变量和 `.env` 不修改。
- 每次问答只构造一个模型实例，保存配置不会改变已经开始的回答。保存不宣称上游连接验证成功。
- 不增加模型目录、多配置档案、自动检测、嵌入模型切换、流式回答或额外轮询。

## 实施步骤

- [x] **1. 整理 PR 基线和子 Issue。** 保存当前工作，基于最新 `origin/main` 建立 `feat/knowledge-workspace`；处理已合入的读接口、问答修复与本地变更重叠，排除尚未进入 main 的黄金集 v2。完善 `docs/子Issue-E-前端.md` 的数据原型、API、模块边界和验收，创建 GitHub 子 Issue #33 并关联 #1。
  - 验证：PR 差异只覆盖前端及配套接口；每条文档路由只有一个 Controller；原有用户文档不变。
- [x] **2. Python 配置读写与问答接入。** 新建 `app/model_config.py`、`tests/test_model_config.py`；修改 `app/runtime.py` 和 `app/llm.py`，注册配置路由并从统一配置入口构造模型。
  - 先验证未实现接口返回 404，再实现。
  - 契约例：`PUT /model-config {provider: "openai", base_url: "http://localhost:11434/v1", model: "qwen2.5:7b", api_key: "ollama"}`。
  - 验证：`python -m pytest tests/test_model_config.py tests/test_runtime.py tests/test_llm.py -q`；覆盖保存、重读、恢复、密钥不回传、跨接口不得沿用旧密钥、非法输入、写失败保留原配置，以及真实模型构造读取新设置。
- [x] **3. Java 配置接口。** 新建 `runtime/ModelConfigController.java` 及对应测试，复用现有 RestClient、HTTP/1.1 和错误契约。只返回固定公开字段；失败文案由 Java 决定。
  - 验证：`mvnw test -Dtest=ModelConfigControllerTest,RuntimeControllerTest`；覆盖三个 HTTP 方法、400/502/503、未知字段过滤和密钥脱敏。
- [x] **4. 前端配置面板。** 新建 `features/qa/components/ModelSettings.vue`，在 `QuestionBox.vue` 模型卡片中接入入口；配置 API 和类型放在独立 `features/qa/model-config.ts`。
  - 验证：浏览器检查读取、保存、刷新后保留、失败可修改重试、留空密钥、恢复启动配置、关闭恢复焦点、390 px 无横向溢出；密钥不进入 localStorage/sessionStorage。
- [x] **5. 联调、审查与 PR 材料。** 更新 README、前端设计记录和验证结果；补充前端 CI；运行三端测试、构建、浏览器回归；PR 材料使用 `Closes #33` 和 `Refs #1`。发布状态与 CI 结果以 GitHub 关联记录为准。
  - 验证：提交不含凭据、运行数据、个人知识库内容或本地验证产物；PR 目标为 main；CI 结果如实记录。

## 验证记录

基于 main 完成前端 15 项、Java 569 项、Python 276 项及 102 项隔离 MySQL 集成测试；两个模型 Controller 的 HTTP/1.1 修复后追加 28 项回归。前端生产构建、两套隔离浏览器回归和 21:56 的真实完整流程均通过，详细结果见 [前端实施记录](../../子Issue-E-前端.md)。

独立审查提出的旧 URL 凭据、SDK 地址优先级和状态刷新竞态均已修复并复审。真实联调额外发现并修复了 JDK h2c 升级导致 Uvicorn 收不到请求正文的问题；保留对应传输回归。

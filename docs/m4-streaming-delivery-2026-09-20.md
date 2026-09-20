# M4 流式输出：实施范围与持续交付计划

日期：2026-09-20。状态：23 文件实施范围已获确认，前后端本地验收通过，前端准备发布。总 Issue #1 是范围裁判；本次只交付 M4 优先级 3，不新增召回算法、查询改写或多轮会话。

## 当前验证与审查

- 后端默认套件：666 passed，两条既有 Chroma/Starlette 弃用警告。
- 前端：73 passed，Vue 类型检查与 Vite 生产构建通过。
- 浏览器：23 项回归通过，包含生成结束前可见增量、EOF 未完成提示、历史切换后迟到输出隔离及后台失败归属。
- F 新增 9 项先失败再通过，后补两种 provider 的真实 SDK 提前关闭；C 12 项、G 8 项、HTTP 4 项、前端 7 项分别记录了缺失实现的预期失败。专项包含在全量内，不相加。
- 独立审查 F/C 与 G/HTTP，补齐逆序 chunk ID、真实 SDK 关闭、完整 HTTP 断连/原生取消/生命周期关闭，以及历史写入失败测试。资源关闭发生在线程实际退出之后，进程锁不提前释放。
- MySQL 集成验收：82 passed。第一次执行因 Windows stdin 中文路径编码未加载连接配置，报无密码认证失败；改用环境变量传递配置路径，明确校验已加载后重新执行通过。没有因此修改业务配置或数据库。
- 已建立总 #1 的真实子 Issue [#66](https://github.com/vansye/EasyRAG/issues/66)，并回读父子关系。运行中的版本仍为此前的 4efdfb4；开发测试不代表运行版本更新。

| 切片 | PR | 功能提交 | CI |
|---|---|---|---|
| F 模型流 | [#67](https://github.com/vansye/EasyRAG/pull/67) | d90c8c1 | [35503861291](https://github.com/vansye/EasyRAG/actions/runs/35503861291) 两项成功 |
| C 问答流 | [#68](https://github.com/vansye/EasyRAG/pull/68) | bede172 | [35503864859](https://github.com/vansye/EasyRAG/actions/runs/35503864859) 两项成功 |
| G/HTTP | [#69](https://github.com/vansye/EasyRAG/pull/69) | fab8a3f | [35503868200](https://github.com/vansye/EasyRAG/actions/runs/35503868200) 两项成功 |
| 前端与文档 | 未发布 | 未提交 | 本地通过；冒烟、模型配置、13 组生产布局通过；真实联调脚本已适配，实际运行待部署验收 |

三个后端 PR 已按 #67 → #68 → #69 合入，每次核对 head SHA、retarget 后继到 main。合并提交分别为 e49720d、43acd5c、9084d8a；最终 [main CI 35504296305](https://github.com/vansye/EasyRAG/actions/runs/35504296305) 成功，前两个 main CI 35504287308、35504291899 同样成功。#66 保持 OPEN，F/C/G 完成项已更新并保留前端、整项发布及运行验收待办。

继续开发工作区为原项目已忽略目录 `rag-service/data/worktrees/m4-streaming`，分支 feat/m4-streaming 当前功能 HEAD fab8a3f；远端 main 已到 9084d8a。未提交前端与四份文档保留。用户已确认额外 3 脚本范围，browser-smoke.mjs、browser-question-layout.mjs、browser-live.mjs 已适配；主流程冒烟、模型配置和 13 组生产布局已通过，真实模型联调尚未执行；下一步发布前端 PR；不得把现有运行服务视为流式已生效。

预览文本按纯文本展示，最终 done 后才展示现有 Markdown/引用组件。sources 使用 trace rank，不使用 chunk ID 数值排序；同一份来源顺序用于 done 与历史。前端不自动重连/重试。提交开始后断连可能留下已保存但客户端未收到的记录，失败提示引导先查历史。

## 基线与 Issue

- 本地统一工作区：`S:/个人项目/EasyRAG-worktrees/unified-fastapi`，HEAD `4efdfb4`。
- 已联网核实总计划相关模块 Issue：F #35、C #27、G #36 均 OPEN；暂无开放 PR。
- main CI `35111878188` 为 success，head 为 `4efdfb4d20bc34fceb9e0149ad424ca86b82ea1a`。这是已有版本的证据，不是本轮功能验收。
- 统一工作区已有三份未跟踪评估文档：`golden-set-v2-heldout.md`、`retrieval-heldout-lf.md`、`retrieval-heldout-v2run.md`，保留，不混入流式 PR。
- 初次端口检查未发现 8080/5173/8000 监听。运行验收须单独记录，不能由 HEAD 或 CI 推断。

## 目标与选择

沿用已批准的 M4 优先级 3：生成过程中可看到答案，完整校验与历史提交后才成为完成结果。检索、判定、提示词及来源排序不因流式而改变。

选择新增 `POST /api/questions/stream`，使用 fetch 读取 SSE；原 JSON 问答接口保留。相较 WebSocket，它与现有单请求问答相符，无需双向会话协议；相较只完成模型层，它能产生用户可见的首字反馈。

事件顺序：`sources`（生成证据按固定 rank 排列）→ 零个或多个 `delta`（尚未校验的文本）→ 一个 `done`（完整结果及已提交 history_id）或 `error`（安全错误信息）。拒答可直接 done，不调用生成流。EOF 不代表成功；缺少 done 必须显示未完成。已发响应头后的错误使用 error 事件，不能宣称更改 HTTP 状态码。

## 模块契约

- F：同一冻结会话新增 `stream(prompt)`；SDK 分段转纯文本，保留文本空白，跳过空元信息段；非文本和上游失败转安全错误。正常结束、失败及消费者关闭时关闭 SDK 迭代器。原 complete 继续用于判定和旧路径。
- C：定义自己的流式端口和事件。检索、判定后固定证据编号；生成增量仅作预览；聚合结束后执行现有引用校验。空证据零模型调用，NONE 不生成，失败不冒充拒答。C 不依赖 F/G/HTTP。
- G：整个工作线程持有 QUERY 许可及 request_work；来源先核对完整性再发出。仅最终有效结果保存历史；断连或失败不保存部分结果。断连与历史提交同时发生时，以提交开始为边界：提交前取消则不保存，已进入提交则允许一次原子完成，不伪称能撤销事务。
- HTTP：有界事件队列连接同步 worker 与异步响应，取消标记解除队列背压。断连后不再开始下一阶段；不能中止的同步调用结束/超时前仍持有许可，防止用户改资料与尚在读取的工作并发。线程实际退出后再释放资源；不遗留后台模型工作。
- 前端：临时文本与已保存结果分开；生成中显示进行状态，不把未校验 Markdown 当成已完成引用。只有 done 替换成正式回答、刷新历史；中途 error/EOF 明确显示未完成，不自动重试模型。保留历史选择 revision 隔离。
- 耗时：服务端单独记录从请求开始到首段非空文本的时间和完整请求时间；浏览器时间另行测量，不把生成 SDK 首段时间冒充用户看到首字的时间。

## 文件范围（23 个，已授权）

以下路径相对实施工作区；按依赖顺序拆成可验收 PR，不一次混合提交。

| 切片 | 文件 | 影响边界 |
|---|---|---|
| F 模型流 | `rag-service/app/modules/answer_models/public.py` | ChatSession / _Session.stream |
| F 模型流 | `rag-service/tests/test_answer_models_stream.py`（新增） | 冻结配置、两种 provider、逐段返回、失败与关闭 |
| C 问答流 | `rag-service/app/modules/qa/public.py` | 自有流式端口/事件、与现有答案规则共用逻辑 |
| C 问答流 | `rag-service/tests/test_qa_stream.py`（新增） | 三态、固定编号、增量与最终校验 |
| G 编排 | `rag-service/app/application/questions.py` | 流式许可、来源、历史及耗时 |
| G 编排 | `rag-service/tests/test_question_stream.py`（新增） | 中断/失败/缺出处不保存，许可持有与释放 |
| HTTP | `rag-service/app/http.py` | 注册流式路由与参数/错误适配 |
| HTTP | `rag-service/app/question_stream.py`（新增） | SSE、队列、连接生命周期 |
| HTTP | `rag-service/tests/test_http_stream.py`（新增） | 真流式分段、终止事件、断连、背压 |
| 生命周期 | `rag-service/tests/test_lifecycle.py` | 关闭服务等待流式工作实际退出 |
| 数据集成 | `rag-service/tests/mysql_http_integration.py` | 流式提交/失败与真实隔离历史存储 |
| 前端 | `frontend/src/features/qa/api.ts` | fetch/SSE 解码、终止与协议校验 |
| 前端 | `frontend/src/features/qa/store.ts` | 临时文本、完成结果与历史选择隔离 |
| 前端 | `frontend/src/features/qa/AskView.vue` | 生成中反馈及未完成展示 |
| 前端 | `frontend/src/features/qa/stream.test.ts`（新增） | 跨字节/跨行分包、error/EOF、请求状态 |
| 浏览器 | `frontend/tests/browser-regressions.mjs` | 慢分段、中断、历史选择与完成刷新 |
| 浏览器 | `frontend/tests/browser-smoke.mjs` | 原主流程夹具使用 SSE，保留原交互断言 |
| 浏览器 | `frontend/tests/browser-question-layout.mjs` | 生产布局 CI 的问答夹具使用 SSE |
| 浏览器 | `frontend/tests/browser-live.mjs` | 真实联调要求最终 done，不能以 HTTP 200 代替成功 |
| 文档 | `docs/api.md` | SSE 事件与错误/取消/历史边界 |
| 文档 | `docs/fastapi-modules.md` | F/C/G 新公开能力与资源职责 |
| 文档 | `README.md` | 流式使用、验证方式与限制 |
| 文档 | `docs/m4-streaming-delivery-2026-09-20.md`（新增） | 最终设计、计划进度、文件影响、PR/CI/运行证据 |

## 执行与验收门槛

1. F：先添加测试，确认缺少 stream 导致失败；实现后运行 F 全套与模块边界测试。测试使用替身 HTTP，不发送真实付费模型请求。
2. C：先验证第一段在模型结束前可消费、跨段引用最终校验、无效引用不可 done；通过后运行 C 旧回归。
3. G/HTTP：验证有界队列、真实断连、上游阻塞期间许可不提前释放、结束后可以再次查询、历史只提交一次；运行生命周期及隔离 MySQL 验收。
4. 前端：验证 UTF-8 与 SSE 拆包、无 done 的 EOF、部分输出报错、历史切换期间迟到 delta 不覆盖历史；运行前端测试、构建及浏览器回归。
5. 集成：运行后端默认套件、五个隔离 MySQL 文件、前端测试/构建/浏览器布局；每个 PR 关联总 #1 及对应模块 Issue。
6. 发布：本地证据写入交付文档，推送后回读 PR diff 与 head，CI 通过才标为可合入。合并后再次检查 main CI；启动验收独立记录，不把“CI 通过”等同于“已运行”。

PR 拆分：F（#35）→ C（#27）→ G/HTTP（#36）→ 前端及端到端交付（#1/#36，具体前端任务另设 Issue）。父模块 Issue 不因一个切片完成而关闭。每个完成项保存命令、结果、head 与链接；被阻塞项写原因，禁止提前打勾。

## 本轮授权边界

用户确认本次多文件修改并要求审查、参考总 Issue 和既有要求。实现按此范围推进；冒烟、布局 CI、真实联调三个旧脚本固定使用 JSON 接口，用户已确认扩大至 23 文件，适配已完成。业务数据迁移、索引重建、删除旧评估文件和清理 stash 不在范围内。

## 实现前运行基线（2026-09-20 16:24 +08:00）

- [x] 联网读取 Issue #35/#27/#36、开放 PR 和 main CI；上述基线属本轮实查。
- [x] 从统一工作区启动现有 `4efdfb4` 后端及前端。后端 worker PID 5980，venv 启动器 13008；前端启动 PID 25272。PID 仅作当次记录，操作前仍须重新核实。
- [x] `/health` HTTP 200：db/retrieval/embedding/chroma/tokenizer 均 UP；bge-m3 / 1024，当前 Chroma 向量数 630。旧记录的 623 不作为本轮预期数量。
- [x] `/api/runtime` HTTP 200，`rag_available=true`，`state=RECOVERY_REQUIRED`；前端 5173 HTTP 200。后端日志确认 Application startup complete。
- [ ] 就绪一致性检查尚未执行；按现有产品规则保留“确认就绪”，不能把健康检查通过称为问答可用。
- 当时尚未开始流式实现；当前开发验证见本文顶部，仍不复用旧测试数量证明新功能。

本轮运行日志在原工作区已忽略目录 `rag-service/data/m4-20260920/`。只启动现有服务，没有迁移、重建索引、修改模型配置或调用生成模型。

实现前仅新增方案稿；当前文件/函数影响按上表逐项交付，最终以 PR diff 为准。既有未跟踪评估文件保留在原统一工作区，不进入本次 PR。

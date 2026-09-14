# 统一 FastAPI 的切换与回退

供维护者将 Java 8080 + Python 8000 切换为一个 FastAPI 8080 进程。目标是保留同一套资料、切片、向量和回答模型配置，并留下可操作的回退入口。首次新建空库请按 [README](../README.md) 启动；公共接口见 [API 文档](api.md)。

本机已于 2026-09-15 完成切换与验收，实际结果见文末。以下保留完整执行和回退流程；再次执行时使用当次核验的进程、路径和备份。任何命令失败、检查不符或资料无法核验，都保持暂停写入并停止后续步骤。

## 1. 记录配置，冻结写入并停旧服务

先关闭资料上传、编辑、删除、重处理和模型配置写入入口，等待现有问答与索引结束。停止期间也不要从另一个终端运行评估、索引维护或直接改数据库。

记录旧提交、分支、启动方式、MySQL 目标、Chroma 目录、模型覆盖文件、`.env`、tokenizer 和切片/embedding 参数。路径以实际配置解析后的绝对路径为准；保存外部环境变量或 Java 本机配置时使用受保护文件，不将密钥写进日志、报告或 Git。

本次已保留的回退代码入口：旧提交 `01103d8`，原分支 `feat/knowledge-workspace`，原工作区 `S:\个人项目\个人知识库管理系统`。迁移代码位于独立工作区 `S:\个人项目\EasyRAG-worktrees\unified-fastapi` 的 `refactor/unified-fastapi` 分支。前端 PR #34 保持独立，未作为本次迁移动作合并；不要清理原工作区。

在 PowerShell 查看实际监听进程：

```powershell
$cutoverListeners = @(Get-NetTCPConnection -State Listen -LocalPort 8080,8000 -ErrorAction SilentlyContinue)
$cutoverPids = @($cutoverListeners.OwningProcess | Sort-Object -Unique)
Get-CimInstance Win32_Process |
  Where-Object { $_.ProcessId -in $cutoverPids } |
  Select-Object ProcessId, ParentProcessId, ExecutablePath
```

对照原启动终端或服务管理器确认这些是旧 EasyRAG 进程，将已记录但不再监听的旧工作进程 PID 也补入 `$cutoverPids`。在各自原终端用 Ctrl+C，或停止对应服务；等待 Java/Python 实际工作进程退出。关闭启动窗口或父进程不等于工作进程已退出，不要按 `java`、`python` 名称批量终止。

```powershell
if ($cutoverPids.Count -gt 0 -and @(Get-Process -Id $cutoverPids -ErrorAction SilentlyContinue).Count -gt 0) {
  throw '旧工作进程仍在运行，禁止备份或启动新后端'
}
if (@(Get-NetTCPConnection -State Listen -LocalPort 8080,8000 -ErrorAction SilentlyContinue).Count -gt 0) {
  throw '8080 或 8000 仍有监听进程，请核对实际 PID'
}
```

验证：已记录的旧工作 PID 均消失，8080/8000 均无旧服务监听，没有其他资料写入者。进程独占锁只约束新后端与新维护 CLI，不能代替这一步。

## 2. 成套备份

在停服且无写入期间，备份 MySQL 全库、**整个 Chroma 目录**、生效的模型覆盖文件和 `.env`，作为同一个恢复点。MySQL 要包含 `document`、`chunk`、`flyway_schema_history`；不能只导出未删除资料。Chroma 要包含 SQLite 与所有分段文件，不能只拷贝单个 collection。模型覆盖文件原本不存在时，明确记录“无覆盖文件”。同时保留 tokenizer 文件或可核验的固定版本与哈希，以及外部配置的恢复方式；本机使用的 `server/config/application-local.yaml` 也须复制进同一批备份，记录原路径并纳入哈希清单。

下面的路径是默认布局示例，执行前按第 1 步记录修改；数据库目标也必须与旧服务一致。备份目录位于业务工作区之外。MySQL 密码由客户端交互读取，命令行不填写真实密码。

```powershell
$cutoverOldService = 'S:\个人项目\个人知识库管理系统\rag-service'
$cutoverChroma = Join-Path $cutoverOldService 'data\chroma'
$cutoverModel = Join-Path $cutoverOldService 'config\llm.json'
$cutoverEnv = Join-Path $cutoverOldService '.env'
$cutoverBackup = Join-Path 'S:\EasyRAG-backups' (Get-Date -Format 'yyyyMMdd-HHmmss')
$cutoverDbHost = '127.0.0.1'
$cutoverDbPort = 3306
$cutoverDatabase = 'easyrag'
$cutoverDbUser = Read-Host 'MySQL 备份用户'
New-Item -ItemType Directory -Path $cutoverBackup -ErrorAction Stop | Out-Null
$cutoverSql = Join-Path $cutoverBackup 'mysql.sql'

mysqldump.exe "--host=$cutoverDbHost" "--port=$cutoverDbPort" "--user=$cutoverDbUser" --password `
  --single-transaction --routines --triggers --events --default-character-set=utf8mb4 `
  --set-gtid-purged=OFF "--result-file=$cutoverSql" $cutoverDatabase
if ($LASTEXITCODE -ne 0) { throw 'MySQL 备份失败' }
if ((Get-Item -LiteralPath $cutoverSql).Length -eq 0) { throw 'MySQL 备份为空' }

Copy-Item -LiteralPath $cutoverChroma -Destination (Join-Path $cutoverBackup 'chroma') -Recurse -ErrorAction Stop
Copy-Item -LiteralPath $cutoverEnv -Destination (Join-Path $cutoverBackup '.env') -ErrorAction Stop
if (Test-Path -LiteralPath $cutoverModel) {
  Copy-Item -LiteralPath $cutoverModel -Destination (Join-Path $cutoverBackup 'llm.json') -ErrorAction Stop
} else {
  Set-Content -LiteralPath (Join-Path $cutoverBackup 'llm-override-absent.txt') -Value 'absent' -Encoding UTF8 -ErrorAction Stop
}
$cutoverBackupFiles = @(Get-ChildItem -LiteralPath $cutoverBackup -File -Recurse -Force -ErrorAction Stop)
$cutoverBackupFiles | Get-FileHash -Algorithm SHA256 -ErrorAction Stop |
  Export-Csv -LiteralPath (Join-Path $cutoverBackup 'sha256.csv') -NoTypeInformation -Encoding UTF8 -ErrorAction Stop
```

Windows 必须让 `mysqldump --result-file` 直接写文件，避免 PowerShell `>` 或文本管道改变 SQL 转储编码。该示例不使用 `--databases`，便于回退时向新建空库导入完整快照。

在备份记录中补上实际路径映射、文档/切片数量与 ID、正文/切片哈希、文件存在性和命令退出码。先核对备份文件完整、哈希可重算，并在隔离空库验证 SQL 可以恢复，再继续接管；不能仅凭生成了文件名就宣称备份成功。

验证：数据库、索引和配置来自同一停服窗口，备份完整且可以恢复；原资料与原工作区保留。

## 3. 显式接管旧库

在新工作区准备 Python 环境，参见 [README](../README.md)。新后端和所有维护命令均从新工作区的 `rag-service` 运行，使用同一 MySQL、Chroma、tokenizer、模型配置和 `RUNTIME_LOCK_FILE`。跨工作区时显式填写绝对路径，避免相对路径落到空索引或另一份配置；`LLM_CONFIG_FILE` 由进程环境变量指定时，服务与维护会话保持一致。

配置 `BACKEND_HOST=127.0.0.1`、`BACKEND_PORT=8080`。同一套数据只允许一个后端进程、一个 worker；不要用不同锁路径绕过独占，也不要删除锁文件来强行启动。旧 Java/Python 此时必须仍然停止。

```powershell
cd 'S:\个人项目\EasyRAG-worktrees\unified-fastapi\rag-service'
.\.venv\Scripts\python.exe -m app.maintenance adopt-legacy-db
if ($LASTEXITCODE -ne 0) { throw '旧库接管失败，保持停服并核对备份与 schema' }
```

A 模块只支持核验通过的 Flyway V1/V2：历史脚本、校验和和成功标记必须一致，字段、类型、默认值、索引、外键、InnoDB 与字符集也须匹配。通过后仅登记 Alembic 基线 `0001_legacy_v2`，保留 Flyway 历史；不改正文、切片 ID 或向量。再次接管已登记的相同旧库仍需通过校验。

`init-db` 用于预先创建好的空 MySQL 数据库，不会替你创建数据库，也不能用于迁移旧库；已初始化为当前 Alembic 版本时可重复验证。不要删除表、修改 Flyway 校验和或手工 stamp 来绕过接管失败。

CLI 成功时退出码为 `0` 并输出 `{"command":"adopt-legacy-db","status":"OK"}`。失败退出码为 `1`，中断为 `130`；错误输出包含 `error/cause`，不应继续执行启动或放行步骤。

验证：接管退出码为 0，Alembic 基线正确，原资料/切片数量、ID、正文和切片哈希与备份记录相符。

## 4. 启动、手动就绪与必要的索引恢复

在新终端以前台方式启动统一服务：

```powershell
cd 'S:\个人项目\EasyRAG-worktrees\unified-fastapi\rag-service'
.\.venv\Scripts\python.exe -m app
```

另一终端检查；此时继续冻结普通用户写入：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8080/health'
Invoke-RestMethod 'http://127.0.0.1:8080/api/runtime'
Invoke-RestMethod -Method Post 'http://127.0.0.1:8080/api/admin/ready'
```

先确认 8080 的实际 PID 属于新 Python 服务，旧 8000 没有监听。`/health` 顶层 UP 只是进程可达，还需看 MySQL、Chroma、embedding、tokenizer。新进程总从 `RECOVERY_REQUIRED` 开始，`rag_available: true` 不代表问答已经就绪。

手动 POST 或网页「确认就绪」通过后才开放门禁，并重新提交 `PENDING`。检查依赖失败、遗留 `INDEXING`、切片失效、向量内容/元数据不一致时返回 `503`；不得把报错、超时或重新启动当成恢复成功。`recovered` 是提交数，后台任务仍需达到终态。

若就绪检查发现索引不一致，停止新后端并核对其实际 PID 已退出，然后在同一配置下离线执行：

```powershell
.\.venv\Scripts\python.exe -m app.maintenance rebuild-index
if ($LASTEXITCODE -ne 0) { throw '索引重建失败，保持停服并检查原因或回退' }
```

重建从 MySQL 读取未删除资料，以当前 tokenizer/切片配置生成完整向量集合，清除旧派生向量。只有现存切片的顺序、文本、字节范围、标题路径和 token 数与当前切片结果完全相同时才复用 ID，否则重新生成切片 ID。成功输出包含 `documents/chunks/reused_chunks`；失败可能留下部分重建结果，需修复后重跑或恢复整套备份，不能只重启放行。

重建成功后重新启动，并再次手动确认就绪。核对原资料与正文，检查切片字节区间，验证问答引用能打开对应原文。用独立临时资料完成上传→索引→问答→修改→重处理→删除，再验证原有资料哈希未改变；只清理本次临时资料，不删除个人知识库。真实模型超时或失败如实记录为失败。

验证：一致性通过，资料任务终态可解释，原数据与模型配置保留，临时资料闭环和引用核验成功。满足后才解除写入冻结。

## 5. 回退到双后端

继续或重新冻结写入，先停止统一进程、旧 Java/Python，以及使用该索引的维护或评估进程，确认实际 PID 消失且 8080/8000 已释放。记录新系统启动后是否产生了用户写入；恢复旧快照不会包含这些写入，需要先单独保全和核对。

使用第 2 步同一批次备份恢复 **MySQL + 整个 Chroma + 模型覆盖文件状态 + `.env`/外部配置**。不能只退代码、只恢复数据库，或将备份目录叠加复制到带有残留文件的 Chroma。

建议将 MySQL 快照导入新建空库，保留迁移后的库供核验，避免删除个人资料。以下在 MySQL 客户端中执行，库名和转储路径替换为本次记录；若备份由其他方式产生，应先确认其中没有指向旧库的 `USE` 或 `CREATE DATABASE`：

```sql
CREATE DATABASE easyrag_rollback_20260915 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE easyrag_rollback_20260915;
SOURCE S:/EasyRAG-backups/本次备份目录/mysql.sql;
```

旧提交 `01103d8` 的 `Settings.chroma_dir` 固定返回所在工作区的 `rag-service/data/chroma`，不读取 `CHROMA_DIR`。回退到原工作区时，先核对该实际目录的绝对路径仍在已记录的旧工作区内；停服确认后，将现有目录在同一 `data` 目录下重命名保留，例如 `chroma.before-rollback-<时间戳>`，再把备份的**整个** Chroma 目录复制回固定的 `rag-service/data/chroma`。检查新目录中的 SQLite、分段文件与备份哈希一致，不向原目录叠加复制。

也可在独立的旧代码工作区恢复其固定 `rag-service/data/chroma`，并从该工作区启动旧 Python；仅设置环境变量不会改变旧版索引位置。两种方式都保留迁移后的目录，不做递归删除。

恢复同批备份的 `.env`、tokenizer、模型覆盖文件及旧 Java 本机配置。原本无模型覆盖文件时，恢复后的实际配置路径也必须保持无覆盖文件，不能沿用迁移后保存的 `llm.json`。核验文件哈希，记录原位置与恢复位置；如选用独立旧代码工作区，下列启动目录也须对应替换。

返回保留的原工作区，确认 `git rev-parse HEAD` 为 `01103d8` 对应完整提交，分支仍为 `feat/knowledge-workspace`；不要在迁移工作区执行强制 reset。按旧版本启动方式分别运行 Python 8000 和 Java 8080：

```powershell
# 旧 Python 终端：已恢复此工作区固定的 data/chroma 及同批配置
cd 'S:\个人项目\个人知识库管理系统\rag-service'
.\.venv\Scripts\python.exe -m app
```

原工作区的 `server/config/application-local.yaml` 包含字面 JDBC URL 和数据源凭据，不能假定修改 `MYSQL_DATABASE` 会覆盖它。连接新建恢复库时，在专用终端用优先级更高的 `SPRING_DATASOURCE_URL/USERNAME/PASSWORD` 明确覆盖全部连接信息；主机、端口和库名替换为已验证的恢复目标，凭据通过交互输入，不写入命令行。

```powershell
# 旧 Java 终端：显式连接已验证的恢复库
cd 'S:\个人项目\个人知识库管理系统\server'
$rollbackCredential = Get-Credential -Message '恢复库的 MySQL 凭据'
$env:SPRING_DATASOURCE_URL = 'jdbc:mysql://127.0.0.1:3306/easyrag_rollback_20260915?useUnicode=true&characterEncoding=UTF-8&serverTimezone=Asia/Shanghai&useSSL=false&allowPublicKeyRetrieval=true'
$env:SPRING_DATASOURCE_USERNAME = $rollbackCredential.UserName
$env:SPRING_DATASOURCE_PASSWORD = $rollbackCredential.GetNetworkCredential().Password
.\mvnw.cmd spring-boot:run "-Dspring-boot.run.jvmArguments=-Dspring.profiles.active=local"
```

这里使用原工作区已验证的 JVM 参数激活 `local`；本机历史记录中，`SPRING_PROFILES_ACTIVE` 和 `-Dspring-boot.run.profiles` 未可靠传入派生进程，不替换为这两种写法。若选择完整恢复原数据库，则恢复全部原配置后沿用原连接信息，不再指向新建恢复库。

检查两个端口的实际进程与健康状态，确认 Java 激活了 `local`，并通过受控数据库检查核验实际连接的是选定恢复库，避免只凭进程 UP 判断。按旧前端流程手动确认就绪，核对旧 Flyway 历史、文档/切片数量、ID、正文哈希、模型配置和引用问答；验证通过后解除写入冻结。PR #34 与原工作区仍保留，回退不隐含合并或清理分支。

## 本机执行记录

| 项目 | 当前记录 |
|---|---|
| 旧代码入口 | `01103d8` / `feat/knowledge-workspace`，原工作区保留 |
| 旧 Java/Python 实际 PID 已退出 | 2026-09-15 05:30（UTC+8）：Java 34068、Python 22712 及对应启动器均退出，8080/8000 释放；停止前 READY、13 份有效资料均 INDEXED |
| 成套备份位置、哈希与恢复验证 | `S:\EasyRAG-backups\20260915-052954`：16 行 document（含历史/软删除）、623 行 chunk、Flyway 历史、完整 Chroma、旧 `.env`、Java 本机配置、tokenizer；原模型覆盖文件不存在。SQL 恢复到随机隔离空库后逐行哈希一致，验证库已清理；文件 SHA-256 见本地 `manifest.json` |
| `adopt-legacy-db` 结果与数据核对 | 退出码 0、status OK，登记 `0001_legacy_v2`；接管前后所有原文档、切片和 Flyway 行的完整哈希一致 |
| 首次就绪检查 | 发现旧 Chroma 仅有 106 条向量，较 623 个有效切片缺少 517 条；返回 503 并保持 RECOVERY_REQUIRED，没有在不一致时开放问答 |
| 离线索引恢复 | `rebuild-index` 退出码 0：13 份资料、623 个切片，623 个 ID 全部复用。与备份恢复库逐字段比较，只有有效资料的 `indexed_at` 更新，正文、元数据、updated_at、切片及 Flyway 历史未变；证据见本地 `post-rebuild-data.json` |
| 统一 8080、手动就绪 | FastAPI 实际 worker PID 43556（启动器 35544），代码提交 `b1deacc`；只监听 127.0.0.1:8080，8000 无监听。MySQL、Chroma、embedding、tokenizer 均 UP；手动 ready 为 READY / recovered 0，Chroma 623 条向量，Vue 5173 代理通过 |
| 原资料/引用核验与临时资料闭环 | `browser-live.mjs` 的 10 个步骤通过：配置保存/重读/恢复，上传、索引、引用原文、编辑、重处理、删除、删除后拒答。3 次真实问答均 HTTP 200（ANSWERED、ANSWERED、REFUSED），无页面异常；临时资料 17 已软删除，切片/向量清除，原资料与切片完整哈希未再改变 |
| 模型与配置 | `agnes-3.0-flash` / openai，来源 environment；检索 `bge-m3` / 1024 维。网页验证后已恢复原配置，新旧工作区均无 `config/llm.json` 覆盖文件；新后端 `.env` 仍被 Git 忽略 |
| 是否开放写入 / 是否回退 | 2026-09-15 05:51（UTC+8）最终核验 READY，开放使用；未回退。备份哈希再次验证通过，旧工作区和全部 PR 保留，未执行合并 |

验证与交付：本次 [PR #56](https://github.com/vansye/EasyRAG/pull/56) 的 [Linux CI](https://github.com/vansye/EasyRAG/actions/runs/34898370390) 通过默认 Python 471 项、真实 MySQL 61 项、Vue 15 项及生产构建。浏览器验收连接真实本地 embedding 与当前回答模型；3 次提问耗时分别为 7.978、2.032、1.558 秒，仅记录该次场景，不作为性能基准。

本地证据：备份目录中的 `manifest.json`、`post-rebuild-data.json`、`final-check.json`；新工作区 `rag-service/data/cutover/` 的进程、配置、就绪与审计记录；`frontend/.verification/live-report.json` 与截图。备份、密钥、用户资料和这些运行产物均未提交到 Git。

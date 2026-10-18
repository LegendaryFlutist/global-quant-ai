# 项目进度与会话交接

更新时间：2026-10-19（Asia/Shanghai）。每个经过验证的开发单元都更新本文件、TASKS.md并创建本地提交。本文件不依赖聊天历史。

## 当前阶段

**Phase1及GitHub恢复机制已交付。Phase2已实现三年历史K线本地归档、多周期技术结构API/图表和当前选中标的5秒刷新（道氏123、趋势线破坏、波浪候选、RSI背离、因果收盘确认）；PR #11 后端、前端、研究、发布检查全通过。本机API镜像仍待包下载恢复后重建。下一步为总体情绪/周期汇总和隔离模拟账户。行情只覆盖注册表中精确核验的标的，按需采集，不推测未核验符号。**

目标仓库：https://github.com/LegendaryFlutist/global-quant-ai

Git分支：`feature/phase2-market-structure`，从PR #10的功能分支`refactor/domain-packages-and-function-docs`派生。准确提交以`git log -5 --oneline`为准。Draft PR #11：https://github.com/LegendaryFlutist/global-quant-ai/pull/11（基于PR #10）；提交`32f1a9e`四项GitHub CI成功。Phase2其他计划项目仍未完成，不得整体标为完成。

## 已完成功能

- Web：React/TypeScript/Vite/ECharts；总览、资产、多周期K线、任务、能力状态、设置、登录；Python3.11 FastAPI按领域拆分。
- 数据：PostgreSQL/Alembic 0001、Redis/RQ、不可变Decimal Parquet/SHA256；公开Binance精确代码核验、分页采集、断点恢复、异常重试；不伪造小周期或缺失行情。
- 身份：Argon2id、服务端过期会话、CSRF/Origin检查、登录限流、密码变更撤销会话；本机loopback部署。无实盘执行能力。
- 资产：80项登记、13个真实精确合约代码的公开元数据快照；四个优先标的KORUUSDT、CLUSDT、NVDAUSDT、BTCUSDT。本机保留34,544根真实15m行情，最近90天大周期严格聚合。
- 离线研究：既有12类反转信号、多周期、经典道氏与1-2-3、波浪候选、GRU等模型、杠杆合约回测、持仓统计、分层报告、模拟邮件。风险研究复用冻结GRU与已使用验证窗，按GRU方向支持/大周期趋势/道氏/波浪/反转形态生成质量分，结合波动率、止损、回撤预算限制杠杆和仓位，并仅减仓。策略配置`config/swing.json`、原模型权重与冻结数据计划保持原状。
- 容器：保留NVIDIA NGC PyTorch 25.11 GPU lab；独立Python3.11 API、worker、Web、PostgreSQL、Redis。镜像摘要与依赖锁固定。
- 分享准备：匿名配置/示例邮箱、凭据和私人数据排除、发布扫描、贡献指南、安全说明、Issue/PR模板、固定官方Actions摘要的CI、Dependabot。
- 新克隆：没有私人历史目录时改为公开采集任务；不把不存在的本机数据标为导入成功。
- 恢复规范：AGENTS.md架构/恢复约定；本进度文件；TASKS.md；只读`resume.sh`、显式文件提交`checkpoint.sh`；持久状态备份/恢复说明。

## 实际验证记录

| 日期 | 开发单元 / 命令 | 真实结果 | 证据 |
|---|---|---|---|
| 2026-10-18 | Phase1 Docker + 浏览器 | 4个标的×6周期共24组图表通过，80项资产、34,544根真实K线；无浏览器未捕获异常 | 本机`runs/platform_phase1/browser-check.json`、截图、runtime-check.json |
| 2026-10-18 | `bash scripts/platform.sh test`（原Phase1） | 20 passed | 本机`runs/platform_phase1/backend-tests.log` |
| 2026-10-18 | `cd frontend && npm test && npm run build` | 2 passed；TypeScript与Vite生产构建成功 | 本机`runs/platform_phase1/frontend-tests.log`、frontend-build.log |
| 2026-10-18 | `npm audit --json` | 当时0项已知问题；不是未来安全保证 | 本机`runs/platform_phase1/frontend-dependency-audit.json` |
| 2026-10-18 | 分享准备：`bash scripts/platform.sh build`、`test` | 构建成功；21 passed（含新克隆采集路径）；1项Starlette TestClient弃用提示 | `/tmp/quant_github_build.log`、`/tmp/quant_github_backend_tests.log`，已归档本机`runs/maintenance/20261018/` |
| 2026-10-18 | 匿名邮箱：`docker exec quant-lab-lab-1 python -m unittest tests.test_notifications tests.test_futures -q` | 14 passed，SMTP均为测试mock，无真实投递 | `/tmp/quant_github_mail_tests.log`，已归档本机`runs/maintenance/20261018/` |
| 2026-10-18 | `python3 scripts/check_publish.py --staged` | 首个提交141文件扫描通过；每次新增提交均重新扫描 | 见本阶段交接记录 |
| 2026-10-18 | 全新远程克隆 + `bash scripts/resume.sh` + 发布扫描 | PASS，提交05af517；私人状态不存在，工作区干净 | 本机`runs/maintenance/20261018/remote-clone-recovery.json` |
| 2026-10-18 | `python3 -m unittest discover -s tests -p test_checkpoint.py -v` | 5 passed：显式文件、无操作、只读恢复、拒绝其他暂存工作、拒绝.env | 本机`runs/maintenance/20261018/checkpoint-tests.log` |
| 2026-10-18 | `python3 scripts/check_docs.py` | PASS：404个具名Python函数/嵌套函数均有docstring | 控制台结果；`scripts/check_docs.py`纳入CI |
| 2026-10-18 | `docker compose ... exec -T lab env PYTHONPATH=tests python -m unittest test_risk -v` | 10 passed；覆盖评分门控、波动杠杆、资金预算、时间对齐和减仓账本 | 本机命令结果；合成行情，不修改冻结报告 |
| 2026-10-18 | `docker compose ... exec -T lab python -m quant_lab.services.risk_research --timeframe 1h` | 四标的均完成；对照固定5倍、评分/波动仓位、评分/波动+减仓；未触碰保留留出集、未重训 | 私人只读产物`runs/risk_latest/report.json`及时间戳归档；不得提交Git |
| 2026-10-18 | 风险回放（同一已使用验证区间） | 带减仓方案压力回撤：KORU 0.72%、CL 0.72%、NVDA 0.20%、BTC 0.59%；固定5倍分别7.49%、3.47%、1.28%、2.80%。带减仓净收益分别-0.09%、-0.64%、-0.01%、0.18%，低于部分固定方案；四模型仍为`NO_CANDIDATE` | `runs/risk_latest/report.json`；探索性验证，不代表独立样本外或未来表现 |
| 2026-10-18 | `bash scripts/platform.sh test` | 24 passed；仅使用隔离`quant_test`数据库和Redis测试服务；1项既有Starlette弃用提示 | 控制台结果；测试服务已停止 |
| 2026-10-18 | `bash scripts/container.sh test` | 154 passed，GPU容器研究/模型/回测/报告及恢复回归 | 控制台结果 |
| 2026-10-18 | `cd frontend && npm test && npm run build` | 2 passed；TypeScript和Vite生产构建成功 | 控制台结果 |
| 2026-10-18 | `bash scripts/platform.sh up`及健康检查 | API/Web重建；`/api/health`为200，风险研究未认证访问为401；现有lab与PostgreSQL/Redis保留 | 控制台结果 |
| 2026-10-18 | `node scripts/platform_browser_check.mjs 9223` | 未通过：旧bootstrap密码已失效，登录页拒绝认证；没有继续页面断言，不修改数据库凭据 | 本机控制台；脚本在失败前写入`runs/platform_phase1/login.png`，可能覆盖原登录截图；后续E2E使用隔离账号和新输出目录 |
| 2026-10-18 | GitHub Draft PR #10 / commit `c9a099e` | 分支已推送；backend、frontend、publish-hygiene、risk-engine全部通过 | https://github.com/LegendaryFlutist/global-quant-ai/pull/10 ，Actions run 39130693978 |
| 2026-10-18 | GitHub Draft PR #10 / architecture handoff commit `2aefb3f` | 文档变更的backend、frontend、publish-hygiene、risk-engine CI全部通过 | Actions run 39130868450 |
| 2026-10-18 | Phase2历史归档：`bash scripts/platform.sh build`、`bash scripts/platform.sh test` | 重建测试镜像后30 passed；1项既有Starlette TestClient/httpx弃用提示 | 本机控制台；mock Binance、临时目录、quant_test数据库；真实网络未请求 |
| 2026-10-18 | `cd frontend && npm test && npm run build` | 2 passed；TypeScript与Vite生产构建成功 | 本机控制台 |
| 2026-10-18 | `python3 scripts/check_docs.py`、`git diff --check` | 428个具名Python函数有docstring；差异空白检查通过 | 本机控制台 |
| 2026-10-18 | `docker compose -p quant-lab -f compose.yaml exec -T lab env PYTHONPATH=tests python -m unittest test_market_structure -v` | 7 passed：新增多周期报告前缀不变性、确认后价格-RSI背离；原道氏/波浪/报告测试通过 | 本机控制台；合成OHLCV，不改既有结果 |
| 2026-10-18 | `python3 scripts/check_docs.py`（新增结构事件函数后） | PASS：432个具名Python函数均有docstring | 本机控制台 |
| 2026-10-18 | `bash scripts/container.sh test` | 156 passed；包含结构/背离、研究报告、因果和恢复测试 | 现有NVIDIA研究容器；未改模型权重或冻结回测产物 |
| 2026-10-19 | `bash scripts/platform.sh test`（提交51d82f9之后） | PostgreSQL/Redis隔离服务中30 passed；GitHub Actions失败定位为测试字符串断言未兼容PostgreSQL `NUMERIC(28,8)` 八位规范化；改为Decimal数值比较。本地测试通过，修复提交`e9cbc3c`的GitHub backend、frontend、risk-engine、publish-hygiene均success；1项既有Starlette/httpx弃用提示 | 本机容器控制台；Actions run 39144358043首次失败、修复后检查见`e9cbc3c` |
| 2026-10-19 | `python3 scripts/check_docs.py`、`python3 scripts/check_publish.py`、`git diff --check` | PASS：473个Python函数有docstring；222文件发布扫描通过；差异空白检查通过 | 本机控制台 |
| 2026-10-19 | `bash scripts/platform.sh build`、`bash scripts/platform.sh test`（挂载工作区后端源码） | ARM64 Python3.11 API镜像构建成功；隔离PostgreSQL/Redis 44 passed，1项既有弃用提示。构建没有重启运行中的API/Web | 本机Docker控制台；源代码挂载使测试对象对应当前工作区 |
| 2026-10-19 | `cd frontend && npm test && npm run build`、文档/发布/py_compile检查 | 2 passed；TS类型检查及Vite构建成功；498个Python函数有docstring；222文件发布扫描通过；本次涉及模块语法通过 | 本机控制台 |

未把本地测试声称为远程CI结果。`/tmp`日志不耐重启，正式阶段结束应归档至`runs/maintenance/`；公开文档仅保留无敏感信息的结果摘要。

## 当前问题与边界

1. 容器对fapi.binance.com DNS解析失败；宿主先前也曾返回HTTP451。自动更新不可用，已有真实历史快照可研究；不切换来源绕过限制、不伪造行情。
2. 三年标记价/费率采集代码已本地验证，但GitHub CI待跑且当前运行容器尚未重启；合约回测成交/权益账本、对比收益曲线、模型注册/训练、SMTP/私有API凭据、2FA审批与实盘执行均未完成。禁止虚构数据或擅自启用。
3. 原模型结果未达晋升门槛；不能把最高样本收益称为稳定盈利模型，不能因为恢复会话而重新使用冻结留出集。
4. 本机账号、历史数据、权重和数据库不在GitHub；重启后应保留原持久卷/目录。磁盘丢失必须依赖另行备份，不能靠Git恢复。
5. 单人本机环境，不等于公网生产系统；测试提示未来需迁移Starlette TestClient到httpx2；项目暂未选择开源许可证。

## 进行中与下一步

- 已完成：24个研究模块迁移到`quant_lab`领域包，保留根兼容入口；404个具名Python函数/嵌套函数有文档；前端页面按职责拆分。分包提交`b868d1f`已本地保存。
- 已完成并推送：版本化风险配置、真实GRU质量评分、波动率感知预算、保守只减仓回测、认证只读风险API及Web风险页面。风险单元提交`c9a099e`远程4项CI全通过；当前Draft PR #10。
- 架构及交接说明已更新并提交；commit `2aefb3f` 的四项CI通过。PR仍因浏览器验证未完成而保持Draft。
- 浏览器验证需解决：本机保存的初始bootstrap凭据与现存密码不一致。使用独立临时测试账户/隔离测试数据库和新的输出目录完成页面端到端检查；不得重置当前用户密码或更改持久数据库。
- 已完成Phase2首个开发单元：长期行情独立归档服务、`FETCH_ARCHIVE`后台任务、认证API和图表采集/读取入口；点击后按页写不可变Parquet与SHA256清单，可续传且不影响90天快照。测试运行于mock与隔离库，尚未真实访问Binance，也未浏览器验收。
- 已实现并提交多周期结构归档单元：复用确认摆动点和Dow123，输出各周期趋势线/破位、波浪候选、确认123、RSI顶/底背离，并关联数据SHA256；将事件汇入已有离线HTML/JSON结构报告。目标模块7项测试通过，完整研究套件156项通过；见最新Git记录。
- 多周期结构Web单元已通过PR #11提交`32f1a9e`上的GitHub CI：Python3.11 backend、前端、risk-engine、发布扫描全success；本机npm测试2 passed/生产构建成功、py_compile与437函数文档检查通过。新增API运行容器未在本机重建：Docker BuildKit两次停在ARM64 NumPy wheel下载，第二次约90秒无进度后手动取消（退出码130）；现有服务仍用旧镜像，不声称本机已部署。恢复命令：处理可达的Python包索引/代理后运行`bash scripts/platform.sh up`，随后`bash scripts/platform.sh test`及认证后页面验收。CI结果与限制记录于`docs/platform/phase1.md`。
- 实时刷新单元提交`cf678fd`及SHA256展示修正提交`dbb3602`已通过PR #11四项GitHub CI（Python3.11后端隔离PostgreSQL/Redis、前端、研究、发布扫描全success）；新服务/API测试包含闭合K线只追加Parquet、未闭合快照独立SHA256、会话/CSRF、无外部网络mock。前端2 passed/构建成功；本机py_compile、448个函数docstring检查通过。实例只在三年缓存视图刷新当前周期，错误退避至60秒。Docker本机镜像仍未重建：BuildKit ARM64 NumPy下载无进度后手动取消，当前运行API/Web还是旧镜像；恢复需在包索引可达后`bash scripts/platform.sh up`、`bash scripts/platform.sh test`和隔离账号浏览器验收。单进程锁适用于当前单机服务；若横向多API副本，需要Redis级共享节流。
- 总体结构周期摘要提交`9225ecc`的PR #11四项GitHub CI全部success：Python3.11 backend (mock结构、本地隔离测试库)、frontend、risk-engine和publish-hygiene；研究容器`bash scripts/container.sh test`为156 passed，前端2 passed/生产构建，本机`py_compile`、文档检查457函数、发布扫描全过。方向分明确为趋势70%+道氏30%并叠加最近八根周期内确认RSI背离（±0.2）；20根收盘对数波动只作背景；4h/1d冲突或破位为TRANSITIONING，缺档UNKNOWN。来源hash和支撑/压力透明显示；无公网行情读取。
- 已完成（待本次远程CI）：标记价格K线与资金费率按需采集service/API任务/账号UI、本地不可变Parquet/SHA256、分段校验和续传；用户可以点击按账号所选资产/周期排队最近三年行情。`bash scripts/platform.sh build`成功；挂载当前源码的隔离`bash scripts/platform.sh test` 44 passed，前端2 passed/构建成功；GitHub尚未运行本单元CI。构建未重启现有服务，当前API容器仍可能运行旧镜像。
- 下一步：提交并推送上述数据采集单元、确认PR CI；部署到现有服务并验收只读API/任务状态（真实币安连通性需在用户点击后确认）；随后构建合约历史回测账本/净值、费用/资金费、逐笔、回撤、分标的和持仓时长比较。报告需绑定合约标记价/资金费manifest SHA256、策略配置版本和时间窗，不能把普通K线收益冒充合约净收益或保证未来盈利。

## 发布交接记录

- GitHub已通过官方CLI登录；不在此文件记录任何授权码或Token。
- 公开仓库已创建并核验PUBLIC/main；首个提交`b7e3330`已推送且远程HEAD一致。
- 基线CI：https://github.com/LegendaryFlutist/global-quant-ai/actions/runs/39120035942 。**已完成，success**（backend、frontend、publish-hygiene均通过）。
- 恢复/检查点5项回归已通过；脚本拒绝混入其他已暂存文件，恢复过程保持用户工作区不变。
- 检查点保护提交`05af517`已保存并推送；新增5项恢复回归。其远程CI也已success：https://github.com/LegendaryFlutist/global-quant-ai/actions/runs/39120227472 （backend、frontend、publish-hygiene均通过）。独立临时目录从GitHub克隆该提交、执行resume和发布扫描均通过，私人状态未被克隆，工作区保持干净。最新提交以`git log -1`为准。私人完整备份/隔离恢复演练仍未执行，保留未完成项。

## 新会话首先运行

```bash
cd <你的项目路径>
bash scripts/resume.sh
# 核对当前分支、未提交改动、最近提交、TASKS.md，按需只读查看服务
bash scripts/platform.sh status
```

需要启动已停止服务时：`bash scripts/platform.sh up`。需要测试时：`bash scripts/platform.sh test`，前端进入frontend运行`npm test && npm run build`。禁止为了恢复而删除卷、覆盖.env或重建冻结数据计划。

### 下一次继续开发的具体入口

先阅读三个根目录交接文件和Git状态；确认远程CI后创建`feature/phase2-analysis-contract`分支。复用现有technical.py和market_structure.py，定义可序列化的确认时间分析契约，并补充未来追加数据不改变过去确认结果的测试。先不接入训练、账户或实盘模块。

## 本轮用户任务：领域分包、函数说明与评分/风险页面

- 已验证单元：24个研究模块迁移到quant_lab的10个领域包；根入口/导入和旧可信pickle兼容；后端catalog/ingestion/jobs拆分；前端页面/layout/hooks分离。
- 全部379个当时具名Python函数有docstring；新增文档检查脚本纳入CI。具名TS函数/组件已有JSDoc。
- 2026-10-18实际结果：研究引擎144 passed、后端21 passed、前端2 passed、生产构建成功；24模块函数/类AST行为与旧版相同，迁移DDL不变。日志归档runs/maintenance/20261018-refactor。
- 接着开发：独立风险策略版本、实际GRU入场质量分、波动率杠杆/仓位预算、保守分级减仓、同条件回放对比与清晰Web页面。只限研究/模拟，不承诺更大收益，不修改原冻结留出或实际账户。
- 当前分支：refactor/domain-packages-and-function-docs。保存本单元检查点后继续，未验证风险功能不能勾选完成。

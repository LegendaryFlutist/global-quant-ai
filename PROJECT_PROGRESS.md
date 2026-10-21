# 项目进度与会话交接

更新时间：2026-10-21（Asia/Shanghai）。每个经过验证的开发单元都更新本文件、TASKS.md并创建本地提交。本文件不依赖聊天历史。

## 当前阶段

**截至2026-10-21，Phase2已实现本地市场结构、多模拟策略回测、整套离线模型任务及分模型/标的报告。本轮新增中国六个指数、指数/商品/外汇等资产分类，以及新浪主源/腾讯备用源的免key日线适配；87项隔离后端测试、3项前端测试/构建和711个Python函数文档检查通过，Docker镜像已重建，API数据库与Redis健康。实测新浪和腾讯端点均返回ConnectError，六个指数的启动补档任务各重试3次后FAILED；没有写入行情数据，两个来源在系统内标为UNAVAILABLE。Binance REST也仍受先前网络限制；真实三年数据收益尚无结论，未实现模型注册/自动择优或持续纸面执行。**

目标仓库：https://github.com/LegendaryFlutist/global-quant-ai

Git分支：`feature/phase2-market-structure`，从PR #10的功能分支`refactor/domain-packages-and-function-docs`派生。准确提交以`git log -5 --oneline`为准。Draft PR #11：https://github.com/LegendaryFlutist/global-quant-ai/pull/11（基于PR #10）；提交`32f1a9e`四项GitHub CI成功。Phase2其他计划项目仍未完成，不得整体标为完成。

## 已完成功能

- Web：React/TypeScript/Vite/ECharts；总览、资产、多周期K线、任务、能力状态、设置、登录；Python3.11 FastAPI按领域拆分。
- 数据：PostgreSQL/Alembic 0001、Redis/RQ、不可变Decimal Parquet/SHA256；公开Binance精确代码核验、分页采集、断点恢复、异常重试；不伪造小周期或缺失行情。
- 身份：Argon2id、服务端过期会话、CSRF/Origin检查、登录限流、密码变更撤销会话；本机loopback部署。无实盘执行能力。
- 资产：80项登记、13个真实精确合约代码的公开元数据快照；四个优先标的KORUUSDT、CLUSDT、NVDAUSDT、BTCUSDT。2026-10-19公开连接器回填后，13个精确USDT永续各有8,639根真实15m行情（共112,307根），大周期严格聚合。
- 离线研究：既有12类反转信号、多周期、经典道氏与1-2-3、波浪候选、GRU等模型、杠杆合约回测、持仓统计、分层报告、模拟邮件。风险研究复用冻结GRU与已使用验证窗，按GRU方向支持/大周期趋势/道氏/波浪/反转形态生成质量分，结合波动率、止损、回撤预算限制杠杆和仓位，并仅减仓。策略配置`config/swing.json`、原模型权重与冻结数据计划保持原状。
- 容器：保留NVIDIA NGC PyTorch 25.11 GPU lab；独立Python3.11 API、worker、Web、PostgreSQL、Redis。镜像摘要与依赖锁固定。
- 分享准备：匿名配置/示例邮箱、凭据和私人数据排除、发布扫描、贡献指南、安全说明、Issue/PR模板、固定官方Actions摘要的CI、Dependabot。
- 新克隆：没有私人历史目录时改为公开采集任务；不把不存在的本机数据标为导入成功。
- 恢复规范：AGENTS.md架构/恢复约定；本进度文件；TASKS.md；只读`resume.sh`、显式文件提交`checkpoint.sh`；持久状态备份/恢复说明。
- 系统页面完整模型回测：模拟/回测页可启动四标的×八模型及15m–4h周期的白名单组合；NVIDIA PyTorch 25.11 runner运行同一离线引擎，逐个验证折和退出配置报告进度，页面显示模型/标的/周期排名、权益曲线和逐笔操作。已有`runs/*/batch_summary.json`历史批次可只读浏览，新报告写入`.runtime/research-runs/<UUID>`并按用户隔离；不会发真实邮件或订单。

## 实际验证记录

| 日期 | 开发单元 / 命令 | 真实结果 | 证据 |
|---|---|---|---|
| 2026-10-19 | 全模型页面/API/runner：`bash scripts/platform.sh test` | 51 passed，1项既有Starlette TestClient弃用提示；含任务隔离、曲线和逐笔、旧批次只读及路径穿越回归 | 本机隔离`quant_test`控制台结果；`backend/tests/test_research_backtests.py` |
| 2026-10-19 | 前端：`cd frontend && npm test && npm run build` | 2 passed；TypeScript和Vite生产构建成功 | 本机控制台结果 |
| 2026-10-19 | `python3 scripts/check_docs.py`、语法检查、`bash scripts/platform.sh up`、健康与GPU检查 | 559个Python具名函数文档通过；语法检查通过；API健康/数据库/Redis正常；NVIDIA runner已启动，Torch 2.10.0a0+b558c986e8.nv25.11且CUDA可用 | 本机控制台结果；容器`quant-lab-research-runner-1` |
| 2026-10-19 | 完整GPU任务端到端 | 尚未通过登录页面触发；旧批次接口由隔离API测试覆盖。下一步可在页面点“开始整套回测”验证GPU进度和报告 | 未验证；不能标记为端到端验收 |
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
| 2026-10-19 | `bash scripts/platform.sh build`、`bash scripts/platform.sh test`（挂载工作区后端源码） | ARM64 Python3.11 API镜像构建成功；隔离PostgreSQL/Redis 44 passed，1项既有弃用提示 | 本机Docker控制台；测试容器挂载当前工作区源码 |
| 2026-10-19 | `cd frontend && npm test && npm run build`、文档/发布/py_compile检查 | 2 passed；TS类型检查及Vite构建成功；498个Python函数有docstring；222文件发布扫描通过；本次涉及模块语法通过 | 本机控制台 |
| 2026-10-19 | GitHub PR #11 commit `5d2a028` | backend、frontend、risk-engine、publish-hygiene四项CI均success | Actions run 39145561898；https://github.com/LegendaryFlutist/global-quant-ai/actions/runs/39145561898 |
| 2026-10-19 | `bash scripts/platform.sh up` + `/api/health`、首页、OpenAPI路由检查 | 新API/Web镜像已启动且healthy；健康端点和首页HTTP 200；模拟账号数据归档route已注册；worker可导入新采集服务 | 本机Docker控制台；没有真实Binance网络请求 |
| 2026-10-19 | 多理论回测：`bash scripts/platform.sh test` | 47 passed，使用隔离PostgreSQL/Redis和合成归档；覆盖多周期时间因果、账号修订绑定、用户隔离、数据SHA及不可变报告。1项既有Starlette/httpx弃用提示 | 本机Docker测试控制台；没有外部行情请求 |
| 2026-10-19 | `cd frontend && npm test && npm run build` | 2 passed，TypeScript检查及Vite生产构建成功；净值/回撤图复用项目已有ECharts依赖 | 本机控制台 |
| 2026-10-19 | `python3 scripts/check_docs.py`、`python3 scripts/check_publish.py`、`python3 -m py_compile ...`、`git diff --check` | 函数文档、公开内容扫描、涉及模块语法与差异格式检查通过；224文件、1,919,449字节无禁止路径/凭据模式 | 本机控制台；最终提交前会再运行staged扫描 |
| 2026-10-19 | GitHub PR #11 commit `1eefbb5` | backend、frontend、risk-engine、publish-hygiene四项CI全部success | [Actions run 39146765039](https://github.com/LegendaryFlutist/global-quant-ai/actions/runs/39146765039) |
| 2026-10-19 | `bash scripts/platform.sh up` + `/api/health`、首页与OpenAPI | API/Web镜像重建；健康接口和首页HTTP 200；三个回测路由已注册；worker及API均可导入回测服务 | 本机Docker控制台；未执行真实回测或Binance请求 |

未把本地测试声称为远程CI结果。`/tmp`日志不耐重启，正式阶段结束应归档至`runs/maintenance/`；公开文档仅保留无敏感信息的结果摘要。

## 当前问题与边界

1. 容器对fapi.binance.com DNS解析失败；宿主先前也曾返回HTTP451。自动更新不可用，已有真实历史快照可研究；不切换来源绕过限制、不伪造行情。
2. 三年标记价/费率采集已通过本地与GitHub CI并部署；尚未由用户点击请求真实行情，因此真实上游连通性/数据可用范围未知。本地回测报告已实现，但还没有真实回填数据的收益结果；深度模型注册/加载、跨账号曲线并列比较、滚动/样本外评估、持续纸面账户、SMTP/私有API凭据、2FA审批与实盘执行均未完成。禁止虚构数据或擅自启用。
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
- 已完成并部署：提交`5d2a028`的标记价格K线与资金费率按需采集service/API任务/账号UI、本地不可变Parquet/SHA256、跨worker文件锁、分段校验和重叠时间窗续传；用户可按账号内核验资产/周期点击排队最近三年公开行情。Python3.11隔离44 passed；前端2 passed/构建成功；文档/发布扫描通过；GitHub Actions run 39145561898四项success；`platform.sh up`健康检查及新路由注册通过。没有发起真实Binance请求，尚无采集成功样本。
- 本轮新增本地合约回测服务/API/UI：多理论加权信号、最高周期趋势门控、账号持仓窗口、标记价格执行及资金费/费率假设、跳过缺档标的、逐笔和组合曲线/回撤、每资产统计。报告在运行时`DATA_ROOT/simulation_runs/<task_uuid>/`保存不可变Parquet，并绑定账号revision/config hash、算法SHA和来源数据SHA；账号API按所有者隔离。隔离后端47 passed、前端2 passed/构建成功、文档/发布扫描通过。commit `1eefbb5` 已推送PR #11，GitHub四项CI通过；容器已重建且接口、首页与worker检查通过。没有真实行情回填和收益结论。
- 下一步：用隔离测试身份做新页面浏览器全流程验证；用户显式点击采集后检查真实覆盖并查看报告。后续做账号间曲线可视化对照、验证/消融及深度模型注册。

## 本轮增量：可调持仓下限、多周期评分与期限评估（2026-10-19）

- 已完成工作区改动：模拟账号`min_holding_hours` API下限和页面输入下限改为1小时，默认值1；止损/风控退出仍可提前。最大上限改为90天（2160小时），便于30天评估策略继续持仓。
- 新增账号策略字段：各原生K线周期相对权重、`trend_following`/`countertrend_rebound`/`balanced`三种方向规则、3/7/30天决策期限及其它期限共识权重。所选周期至少一个权重为正；逆势反弹要求基础周期反转形态与趋势、道氏或背离中至少一项确认。多周期特征按已闭合K线收盘时间因果对齐。
- 入场记录三个期限的方向评分、以滚动历史波动率生成的价格中心与情景带；另用目标时间之后真实已归档K线收盘价生成逐笔实际收益与方向准确率。尾部无足够未来样本时记空值。未来数据仅作事后评估，不参与信号。情景未校准，UI及报告均标记`UNCALIBRATED_SCENARIO`。
- 模型调研已写入`docs/platform/phase1.md`：TimesFM 3.0适合作通用预测参照但公开权重非商业/非生产许可；公开金融基准未证明普适最佳模型，VSN+LSTM等结论限其研究样本。后续用独立滚动样本外、真实手续费/资金费数据比较简单基线、现有GRU与候选深度架构，不依据宣传排名决定。当前未安装新模型、未自动下载权重。
- 系统容量初检：`free -h`可用约105 GiB内存、空闲swap约15 GiB，`df -h /home`可用约1.0 TiB，`.runtime/market-data`约1.2 MiB；Compose现有PostgreSQL、Redis AOF、RQ worker均running。暂不加MQ；需随三年采集增长监控。
- 验证（实际运行，2026-10-19）：`bash scripts/platform.sh test`：48 passed，1项既有Starlette TestClient/httpx弃用提示；包括4小时配置合法、1小时持仓回测、跨期限情景字段、追加未来K线前缀不变、事后真实价格与缺失未来数据处理。`cd frontend && npm test && npm run build`：2 passed，TypeScript/Vite生产构建成功。`python3 scripts/check_docs.py`：529个具名Python函数均有文档；`python3 -m compileall -q backend/app backend/tests`成功。
- 尚未完成：本地浏览器以隔离账号验收编辑/运行报告全流程；真实三年行情尚未重新采集，未校准情景覆盖率和方向预测；Web深度模型注册/加载与架构对比仍未实现。未宣称任何新模型获利或优于既有模型。
- 开发单元已提交：`3d22cac feat: add configurable multi-horizon simulation scoring`，推送至PR #11分支。GitHub Actions run `39195935406` 的backend、frontend、risk-engine、publish-hygiene四项检查全部success（核验时间2026-10-19 UTC）。本机`bash scripts/platform.sh up`重建API/Web，服务健康；GPU lab、PostgreSQL、Redis、worker持续运行。`.local/`为用户/工具已有的未跟踪目录，保持不变且未加入提交。
- 下一步：用隔离测试账号完成页面创建/修改/回测/逐笔查看浏览器验收；用户显式采集真实数据后确认覆盖并运行回测，记录校准误差；另行实现并比较GRU、VSN+LSTM和其他候选的冻结滚动样本外实验。恢复命令：`cd /home/xmfsamsara/Documents/quant_lab && bash scripts/resume.sh && bash scripts/platform.sh status`；运行测试用`bash scripts/platform.sh test`，前端用`cd frontend && npm test && npm run build`。

## 本轮修复：恢复脚本的启动指引（2026-10-19）

- 用户附件只包含`bash scripts/resume.sh`与`bash scripts/platform.sh status`的输出，没有失败堆栈，也没有执行模型回测命令。两条命令本来就分别只展示交接文档/Git历史和容器状态，不会启动平台或回测；附件状态显示API、Web、worker、GPU研究容器、PostgreSQL及Redis均在运行。
- 修改`scripts/resume.sh`末尾，明确告知脚本只读并列出Web启动、GPU容器启动、模型回测和只读状态查看的准确命令；没有将恢复脚本改成自动启动，保留会话恢复无副作用约束。
- 验证：`bash -n scripts/resume.sh`通过；`bash scripts/resume.sh | tail -n 18`显示正确命令；`bash scripts/platform.sh status`显示核心服务healthy/running；`bash scripts/container.sh run --check`退出码0，四个标的本地数据及运行环境均通过预检。
- 尚未获得任何具体模型启动失败信息；若按新的`bash scripts/container.sh run`运行仍失败，需要保留该命令的报错全文供定位。无需重建或重置数据库。
- 本项由`bd75e33 docs: clarify recovery and research launch commands`提交并推送至当前PR分支；GitHub Actions backend、frontend、risk-engine、publish-hygiene四项均success。`.local/`继续保持未跟踪且不触碰。

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

## 当前开发单元交接（2026-10-19）

- 已完成功能：系统页面全模型回测面板、排队API、NVIDIA容器执行器、实际阶段进度、历史批次只读浏览、按模型/标的/周期/退出模式读取报告/曲线/逐笔；保持公开历史数据、邮件模拟和无订单边界。
- 已通过测试：`bash scripts/platform.sh test`（51 passed，1条既有Starlette弃用提示）；`cd frontend && npm test && npm run build`（2 passed，构建成功）；`python3 scripts/check_docs.py`（559函数通过）、Python语法检查、`git diff --check`及发布扫描通过。`bash scripts/platform.sh up`后API健康、NVIDIA PyTorch/CUDA可用，并核对runner无法读`.runtime`平台/引导秘密。
- 未解决：未在已认证浏览器触发完整GPU回测；因此全链路运行时长、进度完成和新报告渲染待首次页面操作验收。未实现模型注册与自动择优。
- 最近功能提交：`722d229 feat: add full model backtests to research page`，已推送PR #11分支，GitHub Actions `39215065348` success。
- 下一步：打开 http://localhost:8080 的“模拟与回测”页，选择范围后点击“开始整套回测”，等待进度完成，检查各模型/标的报告与权益曲线。
- 恢复：`cd /home/xmfsamsara/Documents/quant_lab && bash scripts/resume.sh && git status --short --branch`；启动平台用`bash scripts/platform.sh up`；后端测试用`bash scripts/platform.sh test`。

### 下一次继续开发的具体入口

先阅读三个根目录交接文件和Git状态；确认远程CI后创建`feature/phase2-analysis-contract`分支。复用现有technical.py和market_structure.py，定义可序列化的确认时间分析契约，并补充未来追加数据不改变过去确认结果的测试。先不接入训练、账户或实盘模块。

## 本轮用户任务：领域分包、函数说明与评分/风险页面

- 已验证单元：24个研究模块迁移到quant_lab的10个领域包；根入口/导入和旧可信pickle兼容；后端catalog/ingestion/jobs拆分；前端页面/layout/hooks分离。
- 全部379个当时具名Python函数有docstring；新增文档检查脚本纳入CI。具名TS函数/组件已有JSDoc。
- 2026-10-18实际结果：研究引擎144 passed、后端21 passed、前端2 passed、生产构建成功；24模块函数/类AST行为与旧版相同，迁移DDL不变。日志归档runs/maintenance/20261018-refactor。
- 接着开发：独立风险策略版本、实际GRU入场质量分、波动率杠杆/仓位预算、保守分级减仓、同条件回放对比与清晰Web页面。只限研究/模拟，不承诺更大收益，不修改原冻结留出或实际账户。
- 当前分支：refactor/domain-packages-and-function-docs。保存本单元检查点后继续，未验证风险功能不能勾选完成。

## 当前开发单元（2026-10-19）：精简规则回测与资产覆盖

- 已验证：新任务仅登记多周期结构/道氏123规则策略，机器学习估计器不再列入新回测注册表与配置；旧批次仍按只读方式可浏览。回测页面列出13个已核验合约，并验证真实行情来源、资金费、标记价和与冻结计划匹配的SHA256；缺数据资产不可选，不能被误报为已回测。报告额外汇总所选配置的多/空成交笔数。
- 多空核对：本地既有 `priority_structure_gru_20261018` 归档的四标的、多种旧模型报告均含 direction=-1 空单；例如 pattern_only 的 BTC/KORU/CL/NVDA 归档分别有154/259/153/193笔空单。该历史只读归档不是本次规则策略的结果。
- 部署：`bash scripts/platform.sh up`重新构建并重启API、Web和GPU runner；API健康，`http://127.0.0.1:8080`返回HTTP 200。当前真实覆盖为4/13：BTC、KORU、CL、NVDA通过来源/哈希/冻结边界核验。不能通过更换数据源、跳过TLS或伪造行情解决；待代理/网络策略允许访问Binance公开REST后重试。当前新增标的仅作待采集状态。
- 网络阻塞：当前运行环境主机直连 Binance DNS 查询失败；已配置代理请求返回HTTP 451。
- 测试：`bash scripts/platform.sh test` 52 passed（1条既有Starlette/httpx弃用提示）；`cd frontend && npm test && npm run build` 2 passed且构建成功；`bash scripts/container.sh test` 157 passed；`python3 scripts/check_docs.py` 562个函数通过；`git diff --check`通过。提交`ca2d619`已推送至GitHub功能分支；本机没有安装`gh` CLI，未核验GitHub Actions状态。
- 最近提交：`ca2d619 feat: streamline rule backtests and symbol coverage`，已推送分支`feature/phase2-market-structure`；GitHub CI状态未知。
- 未完成：新规则策略尚未生成新报告；13标的都跑出可信结果仍需公开数据访问和每标的冻结评估计划。Web模型训练和将重复多周期背离/底部钝化作为训练特征仍属Phase3规划；已有分析提供确认背离、周期结构和确认支撑，但不生成未经验证的底部预测价格。

恢复本单元：`bash scripts/resume.sh`；后端隔离测试 `bash scripts/platform.sh test`；前端 `cd frontend && npm test && npm run build`；研究测试 `bash scripts/container.sh test`；文档 `python3 scripts/check_docs.py`；状态 `git status --short --branch && git log -5 --oneline`。

## 当前开发单元（2026-10-19）：容器代理接入

- 已实现：Compose为API/worker增加可选HTTP/HTTPS/SOCKS代理环境变量，以及`host.docker.internal`网关映射；不在代码或Git中保存代理地址/凭据。私有配置键为`PLATFORM_HTTP_PROXY`、`PLATFORM_HTTPS_PROXY`、`PLATFORM_ALL_PROXY`和`PLATFORM_NO_PROXY`。
- 已验证：`docker compose ... config --quiet`通过；按要求执行`bash scripts/platform.sh up`完成三镜像重建/平台服务更新；API/Web health均healthy，首页HTTP 200，worker内`host.docker.internal`解析到宿主机网关`172.17.0.1`。
- 未解决：worker当前未配置代理；宿主机代理只监听127.0.0.1:7897，对Binance的代理请求返回HTTP 451。用户需让代理仅在Docker网桥可达接口监听，并允许`fapi.binance.com:443`，之后在私有运行环境文件设置`PLATFORM_HTTPS_PROXY=http://host.docker.internal:7897`并重新运行`bash scripts/platform.sh up`。尚未验证通过代理获取Binance数据。
- 本单元未触碰数据库或个人任务；无GitHub Actions状态。本次代码/Compose修改后已重建Docker服务。
- 恢复：`bash scripts/resume.sh`；配置后启动 `bash scripts/platform.sh up`；查看健康 `bash scripts/platform.sh status`。不要打印`.runtime/platform.env`。


## 当前开发单元（2026-10-19）：快照核验降级

- 用户澄清的历史采集路径已核实：四个优先标的90日文件来自已连接的 Binance 公开行情工具，非私有App Key；使用公开行情工具可读取当前K线。平台Docker后台采集仍调用Binance REST，不会自动调用Codex连接器。
- 代码：DNS/HTTP公开连接错误时，`VERIFY_ASSETS`若有本机`VERIFIED_SNAPSHOT`，保留原资产快照并成功完成为`VERIFIED_LOCAL_SNAPSHOT`，结果标明`live_refresh=false`及警告；任务页面显示此降级原因。历史错误任务保持原样。FETCH_HISTORY/FETCH_ARCHIVE仍要求API容器的DNS/网络可达。
- 测试与运行：`bash scripts/platform.sh test` 53 passed（1条既有Starlette弃用警告）；前端2 passed/构建成功；`python3 scripts/check_docs.py` 564个函数通过；运行`bash scripts/platform.sh up`后API/Web健康、HTTP 200。数据库中最新VERIFY_ASSETS任务为`SUCCEEDED / VERIFIED_LOCAL_SNAPSHOT`，旧失败任务未改写。MCP公开K线探测BTC/KORU/CL/NVDA/OPENAI/INTC均返回记录；并未把这些临时响应写成后台采集成果。
- 限制：需要后台下载新数据仍须容器可访问Binance REST；当前宿主代理返回HTTP 451，API Key不能解决DNS或代理策略。
- 恢复：`bash scripts/resume.sh`；后端测试 `bash scripts/platform.sh test`；前端测试/构建 `cd frontend && npm test && npm run build`；部署 `bash scripts/platform.sh up`。

## 当前开发单元（2026-10-19）：普通行情缓存刷新与技术图表叠加

- 用户要求K线准实时更新、短周期评估更新，并在图表绘制多个独立高低点趋势线、九转、波浪、背驰、MACD、布林线和可调均线。
- 接受条件：普通90天缓存与三年缓存均每5秒请求一次选中精确标的/周期的公开增量；已闭合K线经Decimal校验后存为新不可变Parquet并更新哈希，未闭合K线只显示/计算临时图表指标，不进入结构判断；90天本地数据可支持结构API；图表含至少3个确认独立pivot趋势线、TD 1–9 setup、已有波浪和RSI背驰标注、右侧确认后的价格-MACD背驰、MACD/布林线/可配MA；单元测试覆盖因果和线条条件。
- 已实现：实时服务缺少长期归档时回退到`MarketData`本地Parquet，在上游可达时按底层原生缓存周期增量保存闭合数据并聚合返回所选图表周期forming candle。结构服务缺归档时回退到90日数据库缓存及完整桶聚合。普通/长期图表页均轮询5秒，连接故障保留缓存并明确陈旧状态；用户可输入1–500日、最多8项均线周期，采用UTC日收盘值并投影至当前图表周期；MACD、价格-MACD背驰、布林带、九转和符合三个确认点的支撑/压力线前端绘制，波浪候选点和RSI/结构事件叠加。非闭合K线不传入服务器结构分析。
- 本机行情资产状态：13个精确Binance USDT永续合约的15m Parquet缓存各8,639根，合计112,307根，时间范围约90天；应用`read_dataset`已对全部13个检查SHA256成功。行情来自连接的公开Binance市场数据工具，无密钥；没有更改旧离线回测目录。
- 实际验证（2026-10-19）：`bash scripts/platform.sh test`结果56 passed，1条既有Starlette/httpx弃用警告；`cd frontend && npm test && npm run build`结果6 passed，TypeScript/Vite构建通过；`python3 scripts/check_docs.py`结果569具名Python函数均有docstring；`bash scripts/platform.sh up`重建成功，API健康、Web/worker已启动。隔离检查确认13个MarketData记录及文件哈希均可读。
- 未解决/边界：执行环境主机直连Binance REST DNS失败，配置代理HTTP 451，故虽然轮询代码已部署，实际不能取得实时更新；错误只会陈旧告警，不会伪造报价。尚未完成认证浏览器实时验收；高周期聚合仅从现有15m完整K线合法生成，不能得到更小周期。改动无数据库schema迁移。
- 功能提交`58528d5 feat: add realtime chart refresh and technical overlays`已推送至`feature/phase2-market-structure`。GitHub Actions状态未核验（本机未安装GitHub CLI）；API/Web/GPU runner镜像均已重建，健康检查通过。
- 下一步：经本机浏览器登录Chart页，检查MA周期输入、趋势线/TD/MACD/布林/波浪显示和刷新陈旧状态；待代理允许`fapi.binance.com:443`后验证真实闭合K线更新与页面多周期分析重算。
- 恢复命令：`cd /home/xmfsamsara/Documents/quant_lab && bash scripts/resume.sh && git status --short --branch`；后端 `bash scripts/platform.sh test`；前端 `cd frontend && npm test && npm run build`；文档 `python3 scripts/check_docs.py`；平台重建 `bash scripts/platform.sh up`。

## 当前开发单元（2026-10-19）：K线载入后浏览器黑屏

- 现象证据：Web访问日志显示K线(约100 KB)及多周期结构事件(约0.6 MB)均返回200，实时行情因上游网络失败返回503并由页面保留缓存；因此黑屏发生在前端图表渲染路径，不是缓存加载失败。
- 根因与修复：`PriceChart`曾为每个结构事件和每个MACD背驰单独创建一个ECharts series，历史样本增长会制造大量series并使浏览器卡死。现在按破位/123/底背离/顶背离聚合为最多4个series，MACD背驰最多2个series，波浪候选合并为1个series；事件定位改用排序K线的二分查找，避免逐事件线性扫描。
- 验证：`cd frontend && npm test && npm run build`（6 tests passed，TypeScript/Vite生产构建成功）；`python3 scripts/check_docs.py`（569个Python函数通过）；`git diff --check`通过。随后运行`bash scripts/platform.sh up`完成Web/API/runner镜像构建和平台重启；首页HTTP 200，`/api/health`返回`status=ok`、数据库和Redis已连接。
- 限制：当前无可接管的交互浏览器会话，未做用户浏览器实际截图验收；Binance REST仍有503，图表显示缓存属于预期降级行为。
- 下一步：用户刷新 http://127.0.0.1:8080 并重新打开多周期行情；如仍复现，记录浏览器控制台第一条异常及标的/周期用于进一步定位。
- 恢复：`bash scripts/resume.sh`；前端 `cd frontend && npm test && npm run build`；部署 `bash scripts/platform.sh up`；健康检查 `curl -sS http://127.0.0.1:8080/api/health`。

## 当前开发单元补充（2026-10-19）：全页黑屏启动兜底

- 根据用户截图，故障表现是整个React根节点为空/全黑，超出单一图表画布问题。为便于恢复和定位，HTML在React加载前显示启动状态，15秒内模块未完成启动则显示强制刷新提示；React子树渲染异常由`AppErrorBoundary`捕获并显示重新加载面板。
- 验证：前端`npm test`为6 passed，`npm run build`通过；`python3 scripts/check_docs.py`通过（569个Python函数）；`git diff --check`通过。执行`bash scripts/platform.sh up`重建Web/API/runner后，首页HTTP 200，API健康且数据库/Redis连接正常。通过本机Chrome只读CDP检查实际用户标签页：新bundle加载，React根节点正常挂载登录页；该标签页认证已过期，`/auth/me`返回401。没有以用户身份提交凭据或执行登录。
- 待用户操作：重新登录；进入多周期行情复验K线加载。如果再次出现全黑，当前启动/错误兜底会留下可见提示；收集浏览器控制台第一条异常继续定位。
- 恢复：`bash scripts/resume.sh`；`cd frontend && npm test && npm run build`；`bash scripts/platform.sh up`；健康检查`curl -sS http://127.0.0.1:8080/api/health`。

## 当前开发单元补充（2026-10-19）：ECharts scatter 系列注册故障

- 用户提供的控制台栈明确报错：`Cannot read properties of undefined (reading 'getProgressive')`，位置为ECharts `setOption` 处理scatter系列。检查发现图表创建scatter标记但未导入/注册`ScatterChart`；现已加入`echarts.use`，并新增SSR回归测试实际调用`setOption`和SVG渲染。
- 控制台同时出现的`/api/market/live` 503来自Binance公开REST连通性故障，只影响实时刷新，不是图表series异常的原因。
- 启动兜底曾使用内联脚本，与`script-src 'self'`冲突；现迁移至同源`frontend/public/startup.js`，不放宽CSP。服务端HTML只引用同源脚本。
- 验证：`cd frontend && npm test && npm run build`（scatter SSR回归测试1 passed，TypeScript/Vite构建成功）；`python3 scripts/check_docs.py`（569函数通过）；`git diff --check`通过。`bash scripts/platform.sh up`已重建Web/API/runner；首页和`/startup.js` HTTP 200、API健康。当前Chrome强制刷新后React登录页已渲染，脚本资源使用新bundle；用户会话仍需重新登录。控制台中CSP报错是页面重载前已缓冲的旧日志，现投放的HTML不含任何内联脚本。
- 待用户操作：登录后重开K线图，确认`getProgressive`异常消失；随后重新登录态下检查图表指标和实时503提示。
- 恢复：`bash scripts/resume.sh`；前端`cd frontend && npm test && npm run build`；重建`bash scripts/platform.sh up`；健康检查`curl -sS http://127.0.0.1:8080/api/health`。

## 当前开发单元（2026-10-19）：Parquet + DuckDB + PostgreSQL + Redis 数据层

- 模块变更：新增`backend/app/storage/`，将SHA256流式校验、原子不可变Parquet写入、内容寻址分段和manifest原子更新收进`parquet_files.py`；近期行情、三年K线、标记价与资金费率写入复用该层。`duckdb_query.py`按调用方明确传入的已校验文件做只读SQL查询，不建立共享可变数据库文件；近期与长期K线读取已切换DuckDB，Arrow往返保留Decimal。
- 实时快照：新增`redis_cache.py`，精确核验的Binance资产K线只进入20秒TTL缓存；上游/Redis失效时读本地带SHA256的`live.json`快照，不把Redis或forming candle当作历史事实。Redis仍同时承载RQ任务和认证限流。未改数据库迁移；未覆盖/重写已有市场数据、三年归档或回测产物。
- 实际数据覆盖检查（只读）：资产目录80项；数据库现有13条资产/周期行情记录，全部为15m，DuckDB加载后13个数据文件SHA256全部通过，共112,307根K线。13项是已精确登记的Binance USDT永续代码；另外67项分布于美股、ETF、KRX/HK/CN、商品、外汇及研究指标等类别，不能由Binance USDM符号推断。当前Binance REST仍受DNS/代理HTTP 451限制，因此本轮没有拉取新行情；这些资产要继续接入须逐个绑定合适的公开行情源和产品别名。
- 验证（2026-10-19）：DuckDB ARM64 Python 3.11 wheel SHA256核验通过，版本1.5.6；`bash scripts/platform.sh up`成功构建API/Web/NGC runner并重启服务；`bash scripts/platform.sh test`最终结果63 passed、1条既有Starlette/httpx弃用警告；`python3 scripts/check_docs.py`结果592个具名函数通过；相关`compileall`和`git diff --check`通过；API health返回数据库连接及Redis健康。只读重新核验13个行情文件后，DuckDB仍读取112,307根15m K线且SHA256全部匹配。
- 未解决：本单元建立统一读写底座，但“80项全量接入”没有完成。剩余资产缺少可信供应商/产品代码映射；行情覆盖页面、逐笔/订单簿采集也仍待后续模块实现。当前未发生新行情拉取；Binance网络阻断不能靠私有只读App Key修复。
- Git状态：实现提交`c2557eb refactor: unify market data storage adapters`已推送到`feature/phase2-market-structure`，`git ls-remote`确认远端ref相同；GitHub Actions尚未查询。未查看或输出`.runtime`密钥；`.local/`是已有未跟踪用户目录，保持不动、不提交。
- 下一步：先为其余67项建立有来源证据的`venue + asset_type + provider + symbol + calendar`映射与覆盖状态报告；然后增加可插拔公开行情adapter并按原生周期/交易日历采集。另为Redis TTL键增加隔离Redis端到端过期测试，再分批把回测Parquet读路径切到DuckDB并对比冻结报告。
- 恢复命令：`cd /home/xmfsamsara/Documents/quant_lab && bash scripts/resume.sh`；后端隔离测试`bash scripts/platform.sh test`；文档检查`python3 scripts/check_docs.py`；重建并启动`bash scripts/platform.sh up`；健康检查`curl -fsS http://127.0.0.1:8080/api/health`；Git检查`git status --short --branch && git log -5 --oneline`。

## 当前开发单元（2026-10-20）：精简图表指标与启动自动补档

- 用户要求图表参照交易所布局减少常驻指标，提供可配置指标体系，解决实时行情未更新，并在服务启动时自动补齐缺失行情。当前覆盖范围仍按用户此前选择，仅限已核验Binance USDM合约。
- 只读检查：13个已核验USDM合约均有15m、8,639根近期K线，共112,307根；所有缓存来自本地历史快照，不代表实时。13个长期15m归档缺失。API容器公开GET失败为DNS解析失败；宿主机经当前代理请求得到HTTP 451。未读取或输出代理地址/凭据。
- 已实现：图表默认只开MA(5,10,20)和量柱。布林带、MACD、TD九转、趋势线、道氏123、背离和波浪候选在可折叠“指标设置”面板中分别开关；有效配置通过已认证且CSRF保护的PUT保存到`system_settings.chart_indicators`，没有加迁移。图表数据管理入口收拢到菜单，多周期分析与数据溯源折叠；实时状态条公开显示当前连接状态、最近尝试时间及上游错误。
- 已实现：API启动调用幂等补档协调器，只扫描已核验USDM的三年15m清单；缺失/不完整数据进持久后台任务，完整归档/活动任务会跳过，最近公开连接/HTTP失败有15分钟冷却。API启动线程不发网络请求，下载由worker执行；失败任务仍可在页面看到/手工重试。Compose把宿主机HTTP(S)代理传给API/worker，SOCKS协议客户端已加锁；系统`ALL_PROXY`只在私有`PLATFORM_ALL_PROXY`中显式设置时转发。
- 已验证（2026-10-20）：`bash scripts/platform.sh test`为69 passed、1条既有Starlette TestClient/httpx弃用警告；覆盖指标设置缺省/持久化/范围检查/认证CSRF、启动归档任务创建/去重及修复代理依赖后可重试。`cd frontend && npm test && npm run build`为1 passed，TypeScript/Vite生产构建成功。`python3 scripts/check_docs.py`为608个Python具名函数通过；相关模块`py_compile`和`git diff --check`通过。
- Docker：`bash scripts/platform.sh up`成功重建并启动API/Web/worker/NVIDIA runner；API健康端点报告数据库、Redis connected，Web首页HTTP 200，原lab和PostgreSQL数据卷仍运行。启动自动创建一条13合约`FETCH_BINANCE_ARCHIVES`任务，worker三次尝试后状态为`FAILED`，错误为“代理连接失败或目标不可达”；历史15m缓存仍为13份、112,307根，三年manifest仍为0，未覆盖现有数据。宿主机公开请求之前返回HTTP 451；宿主代理绑定loopback，不能被Docker容器直连。
- 未解决：当前不能取得实时K线或三年历史。需将代理服务绑定到Docker可达的地址并限制Docker网段，配置私有`.runtime/platform.env`中的`PLATFORM_HTTPS_PROXY`（SOCKS时使用显式`PLATFORM_ALL_PROXY`），并由网络侧允许`fapi.binance.com:443`，然后重启服务让启动补档重试。451/连接失败期间系统保留旧缓存并显示错误，不伪造报价。
- Git状态：实现提交`5d3b6a9 feat: simplify chart indicators and auto-backfill archives`已推送到`origin/feature/phase2-market-structure`及PR #11；GitHub Actions CI run #53通过（frontend、backend、publish-hygiene、risk-engine）。`.local/`是既存用户目录，保持未跟踪，不纳入提交。
- 下一阶段：打开多周期行情页验收默认布局与指标偏好保存；配置可达Binance的Docker代理后，观察自动补档进度并核验真实最新K线。恢复命令：`cd /home/xmfsamsara/Documents/quant_lab && bash scripts/resume.sh && bash scripts/platform.sh status`；测试`bash scripts/platform.sh test && (cd frontend && npm test && npm run build)`；文档`python3 scripts/check_docs.py`；部署`bash scripts/platform.sh up`；Git`git status --short --branch && git log -5 --oneline`。

## 当前开发单元（2026-10-20）：Binance永续批量三年归档

- 用户确认本阶段只扩展Binance合约。因此批量入口只选择目录中`venue=BINANCE_USDM`且状态为`VERIFIED`或`VERIFIED_SNAPSHOT`的精确产品ID；当前数据库只读核验为13个：KORUUSDT、CLUSDT、NVDAUSDT、BTCUSDT、SKHYNIXUSDT、SPCXUSDT、SNDKUSDT、XAUUSDT、QQQUSDT、INTCUSDT、TSLAUSDT、OPENAIUSDT、ZHIPUUSDT。未把其余67项映射成Binance代码。
- 已实现：图表页按钮创建一条`FETCH_BINANCE_ARCHIVES`持久任务，批量采集三年15m原生公开K线；任务结果逐合约保存完成ID、完成数量、当前代码和归档摘要，RQ重试会跳过已完成合约，6小时任务上限。页面后台任务显示当前代码和进度条，失败任务可按原资产范围重试。图表归档请求缺少更长目标周期文件时，用SHA256校验后的15m Parquet按完整UTC桶向上聚合30m、1h、2h、4h、6h、12h、1d及1w；不从15m构造更小周期。没有改schema、覆盖90天MarketData、改动既有回测产物或重置数据库卷。
- 验证（2026-10-20，最终运行）：`bash scripts/platform.sh test`为66 passed，1条既有Starlette/httpx弃用警告；首次运行发现批量恢复分支变量初始化顺序问题，修复后重跑通过。`cd frontend && npm test && npm run build`为1 passed且TypeScript/Vite构建通过；`python3 scripts/check_docs.py`为598个具名Python函数通过；`python3 -m compileall -q backend/app backend/tests`及`git diff --check`通过。mock续跑测试确认第一次第二合约失败后，重试跳过第一合约并成功完成剩余项。测试未连接Binance。
- Docker已执行`bash scripts/platform.sh up`，API/Web/worker/NGC runner重建运行，PostgreSQL和Redis健康；`curl -fsS http://127.0.0.1:8080/api/health`结果为`status=ok,database=connected,redis=true`，OpenAPI确认批量任务路由已注册。GPU lab与PostgreSQL数据卷保持运行。只读覆盖复核仍为13份15m近期缓存、112,307根K线；长期manifest为0，本轮没有替用户发起真实采集，等待页面点击。现有历史记录指出Binance REST在此环境受DNS/代理451阻断，真实下载未验证。
- 未解决：需要用户从图表页点击“采集全部 Binance 合约三年15m”，在后台任务页观察实际网络结果；若再次报DNS/HTTP 451，需要在Docker可访问的网络代理上允许`fapi.binance.com:443`，保持TLS校验，然后重试。真实三年覆盖完成前没有新增行情可用于回测。其它67个非Binance目录资产按用户当前选择暂缓。
- Git：实现提交`1d92274 feat: batch verified Binance archive collection`已推送到`feature/phase2-market-structure`，推送后核对远端ref与之匹配；后续交接文档更新也已提交并推送。GitHub Actions状态未核验；需要当前tip时运行`git log -1 --oneline`并重新核对远端。`.local/`是既存未跟踪目录，没有加入提交。
- 恢复命令：`cd /home/xmfsamsara/Documents/quant_lab && bash scripts/resume.sh && git status --short --branch`；后端`bash scripts/platform.sh test`；前端`cd frontend && npm test && npm run build`；文档`python3 scripts/check_docs.py`；部署`bash scripts/platform.sh up`；健康`curl -fsS http://127.0.0.1:8080/api/health`。

## 当前开发单元（2026-10-20）：资产分类、模拟账号保存与三种PyTorch模型

- 已完成：资产API在不改数据库schema的情况下返回`market_category`与`product_type`；资产页能分别按虚拟货币/股票/ETF/其它以及现货/永续/期货/研究指标过滤。BINANCE_USDM合约仍被标为永续；底层ETF/股票分类不代表现货行情接入或可交易。
- 已完成：模拟账号默认名称不再留空，前后端修剪并拒绝纯空白名称；账号可配置标的数上限从10提高到100，可保存当前13个已核验Binance USD-M合约。后端仍只接受精确登记且核验通过的永续资产。
- 已完成：新研究白名单为`analysis_dow123`、`patchtst`、`tft_attention`、`tcn`。新增PatchTST双层Transformer注意力、TFT风格变量选择/LSTM/多头注意力和膨胀时序卷积TCN；每种使用最近48根连续收盘K线、训练折单独缩放、固定8轮/seed 42。共用相同的时间切分、方向门控、成本和退出方式，逐模型保存训练审计、检查点、权益曲线和标的操作。报告记录算法名、seed、特征schema/模型实现代码/行情数据SHA256；既有历史批次未重写。
- 实测：`bash scripts/platform.sh test`为71 passed，1条既有Starlette/httpx弃用警告；覆盖市场/产品分类、账号空名称及13标的保存、四算法任务白名单和报告读取。`bash scripts/container.sh test`为159 passed，含三个网络CPU小样本训练、三类归一化概率与预测前缀不变，以及四模型比较HTML输出。训练测试输出含既有sklearn收敛提示和PyTorch Transformer nested-tensor提示，未阻断测试。`cd frontend && npm test && npm run build`为1 passed且生产构建成功；`python3 scripts/check_docs.py`为635个具名Python函数通过；Python compileall及`git diff --check`通过。
- Docker：`bash scripts/platform.sh up`重建并启动API、Web、worker、GPU研究runner；随后`bash scripts/container.sh start`重建/启动兼容lab容器。API报告`status=ok,database=connected,redis=true`，Web为HTTP 200，核心服务healthy/running。`bash scripts/container.sh run --check --models analysis_dow123 patchtst tft_attention tcn`通过，确认当前四优先标的源数据和四模型命令行白名单可解析。
- 未解决：当前未从已认证Web页面发起完整全标的GPU任务；所以没有真实资产新回测净值，也未比较哪个模型最好。已有研究时间段此前被看过，结果有选择偏差。新模型只作为候选，不能宣称收益率更高；完整任务仍从`/simulation`多模型面板提交并查看进度/模型×标的报告。Binance实时DNS/代理阻断另行存在。
- Git：功能提交`28c732a feat: add multi-model deep research backtests`已推送到`feature/phase2-market-structure`，`git ls-remote`确认远端SHA匹配。GitHub Actions run `39360592258`的backend、frontend、risk-engine、publish-hygiene四项均success（https://github.com/LegendaryFlutist/global-quant-ai/actions/runs/39360592258）。`.local/`为既存未跟踪目录，未加入提交。
- 下一步：登录`http://localhost:8080`，在“模拟与回测”页勾选四算法及有本地数据就绪的标的启动任务；观察进度完成后检查总体报告和每模型/标的曲线、收益、回撤、成交与持仓。独立开发滚动样本外基准，不能用已查看的留出区间择模。
- 恢复命令：`cd /home/xmfsamsara/Documents/quant_lab && bash scripts/resume.sh && git status --short --branch && git log -5 --oneline`；启动/重建`bash scripts/platform.sh up`；隔离后端测试`bash scripts/platform.sh test`；研究测试`bash scripts/container.sh test`；前端`cd frontend && npm test && npm run build`；文档`python3 scripts/check_docs.py`；健康`curl -fsS http://127.0.0.1:8080/api/health`。

## 2026-10-20：回测数据就绪诊断、中文退出原因与模拟边界

- 已完成：多模型回测覆盖接口现在分开返回完整研究包状态与近期15m Parquet缓存状态；缓存文件按元数据SHA256校验，缺项准确列出K线、标记价、资金费率、来源证明及冻结评估区间。前端显示两种覆盖数，不再把有图表K线等同于可进行合约回测。
- 实际环境核验：13/13 Binance USDM标的均有经SHA256验证的15m缓存，每个8,639根；完整多模型研究包为4/13（KORUUSDT、CLUSDT、NVDAUSDT、BTCUSDT）。其余9个本地只有近期K线缓存，仍缺研究所需标记价、资金费率、来源及匹配冻结评估计划；未伪造或绕过校验。平台历史记录显示容器Binance REST访问受DNS/代理限制，需Docker可达且获准访问`fapi.binance.com:443`后采集。完整包仍缺的资产继续禁用回测选择。
- 已完成：多模型回测、账号逐笔和风险研究表统一中文解释退出代码；未知代码以中文“其他退出原因”展示并保留代码文本。账号页注明其支持配置和历史回测，没有持续运行的模拟撮合。
- 边界/未解决：可在多模型页选择已开放的算法做离线回测，尚未把算法版本绑定到配置账号；独立持续纸面账户/余额/持仓台账仍为Phase5 PLANNED。根据根目录AGENTS.md，当前阶段只提供认证公开数据研究，不启用纸面或真实订单。所有13个资产及已有4个研究包的真实回测数据下载和新收益结果未在本轮运行。
- 验证（2026-10-20）：`bash scripts/platform.sh test`：73 passed，1条既有Starlette/httpx弃用警告；`cd frontend && npm test && npm run build`：3 passed，TypeScript/Vite构建成功；`python3 scripts/check_docs.py`：640个具名Python函数通过；`python3 -m compileall -q backend/app backend/tests`及`git diff --check`通过。Docker已按要求执行`bash scripts/platform.sh up`重建；API健康报告PostgreSQL/Redis已连接，Web与worker运行，NGC runner及原lab容器保持运行。没有修改数据库schema或既有研究报告。
- GitHub：实现提交`bc0743b fix: explain research data readiness and exit reasons`已推送至`feature/phase2-market-structure`；Actions run `39366183426` 的backend、frontend、risk-engine、publish-hygiene均success（https://github.com/LegendaryFlutist/global-quant-ai/actions/runs/39366183426）。
- 下一步：恢复Docker对Binance公开REST的DNS/代理访问后，重新采集缺失标记价/资金费率，生成数据哈希绑定的新评估版本，再运行13标的比较；Phase4做账号模型绑定，Phase5独立设计持久纸面撮合器并继续保持未实现状态直至该阶段验收。
- 恢复：`cd /home/xmfsamsara/Documents/quant_lab && bash scripts/resume.sh && git status --short --branch && git log -5 --oneline`；验证`bash scripts/platform.sh test && (cd frontend && npm test && npm run build) && python3 scripts/check_docs.py`；重建`bash scripts/platform.sh up`；健康`curl -fsS http://127.0.0.1:8080/api/health`。`.local/`保持为既有未跟踪用户目录。


## 当前开发单元（2026-10-20）：隔离容器代理并定位Binance DNS阻断

- 诊断：当前宿主机shell含HTTP/HTTPS/SOCKS代理变量，指向本机Clash；私有`.runtime/platform.env`未配置`PLATFORM_*_PROXY`。旧Compose默认把宿主机HTTP(S)代理传入API/worker，容器中的回环地址不可达。宿主机经Clash请求Binance公开时间接口返回HTTP 451；宿主机和容器直连均不能解析`fapi.binance.com`，但宿主机可以解析`example.com`。直连公共DNS超时，DNS-over-HTTPS直连被重置。未打印代理凭据，也未关闭桌面Clash。
- 已实现：Compose移除从shell `HTTP_PROXY`/`HTTPS_PROXY`隐式继承；只有在项目私有配置中显式设置`PLATFORM_HTTP_PROXY`/`PLATFORM_HTTPS_PROXY`时才给API/worker启用代理。Binance适配器现在沿HTTP异常链识别`socket.gaierror`，DNS故障不再被误报为“代理连接失败”。
- 验证（2026-10-20）：`python3 scripts/check_docs.py`通过（643个具名Python函数）；`bash scripts/platform.sh up`成功重建并启动服务；API健康端点HTTP 200，PostgreSQL/Redis/API/Web健康，worker运行，原lab服务仍在运行。容器API与worker均无非空代理变量；API/worker的Binance DNS仍以`gaierror`失败，API错误现为“DNS 解析失败”。`bash scripts/platform.sh test`：74 passed，1条既有Starlette TestClient/httpx弃用警告。没有下载或生成新行情，也未修改现有缓存。
- 未解决：清除了平台误继承的宿主代理，但网络端仍阻止直接解析Binance；宿主机Clash路径返回451。需网络/DNS侧允许`fapi.binance.com`解析并可访问443，或由用户后续显式配置一条Docker可达且确实允许Binance的代理；验证公开GET成功后，再重试实时行情与三年归档。当前历史缓存保持原样且继续标为缓存，不伪装成实时。
- Git：实现提交`c602b23 fix: isolate platform from host proxy and classify DNS failures`已推送到`feature/phase2-market-structure`；远端分支SHA核验与本地一致。GitHub Actions状态未核验。`.local/`保持未跟踪。
- 恢复命令：`cd /home/xmfsamsara/Documents/quant_lab && bash scripts/resume.sh && git status --short --branch && git log -5 --oneline`；服务`bash scripts/platform.sh status`；测试`bash scripts/platform.sh test`；重新构建`bash scripts/platform.sh up`；健康`curl -fsS http://127.0.0.1:8080/api/health`。


## 当前开发单元（2026-10-20）：让平台容器通过Clash代理访问Binance

- 数据来源核对：PostgreSQL中SNDKUSDT的15m缓存为8,639根，`source.provider`记为`binance_mcp_public`。旧SNDK数据由已连接的Binance公共数据连接器拉取并写入本地缓存；它不是本机Docker API/worker通过REST自行取得，两个路径不能混为一谈。
- 根因：Clash Verge当前`allow-lan=false`，混合代理只监听宿主机loopback；Compose的`host.docker.internal`解析为宿主机Docker网关，但原容器代理变量为空/指向容器loopback，无法连接该Clash监听。前一单元的“完全去掉宿主机代理继承”没有满足用户必须走代理的实际网络要求，本单元已恢复并修正。
- 已实现：增加一个无凭据TCP桥接服务，使用host network访问宿主机loopback Clash，再只绑定Docker主机网关地址；桥接拒绝非量化Docker网段客户端，TCP数据不记录日志。私有运行配置显式让API/worker通过此桥接使用HTTP(S)代理；`scripts/platform.sh up/stop`管理桥接生命周期。未改写Clash配置、未开放LAN监听、未关闭用户Clash进程。SNDK既有缓存和其他历史数据保持原样。
- 验证（2026-10-20）：Compose配置校验及`bash -n scripts/platform.sh`通过；`python3 scripts/check_docs.py`通过（649个具名Python函数）；`bash scripts/platform.sh test`为75 passed、1条既有Starlette TestClient/httpx弃用警告。`bash scripts/platform.sh up`成功重建；API、worker、proxy-bridge、Web、PostgreSQL和Redis运行健康，API health HTTP 200。API和worker经Clash桥接请求`https://example.com/`均返回HTTP 200；经同一代理请求Binance Futures时间接口，收到CloudFront HTTP 451。适配器现显示HTTP 451而非代理连接失败，证明容器代理链路已通，当前代理出口/上游仍拒绝该Binance请求。
- 未解决：需要在Clash中选择/配置一个实际允许访问`fapi.binance.com:443`的出口节点或由网络侧解决451；确定可用节点前不重试大批历史下载，避免连续失败。通过该节点验证公开时间接口HTTP 200后，重试页面实时行情和三年归档任务。
- Git：实现提交`d58064a fix: route platform market data through Clash proxy bridge`已推送到`feature/phase2-market-structure`，远端SHA核验与本地匹配；GitHub Actions状态未核验。此前的`c602b23`代理清除变更仍保留在历史中，由本提交恢复实际代理访问，不重写已推送历史。私有`.runtime/platform.env`仅在本机更新，未纳入Git；`.local/`保持未跟踪。
- 恢复命令：`cd /home/xmfsamsara/Documents/quant_lab && bash scripts/resume.sh && git status --short --branch && git log -5 --oneline`；服务`bash scripts/platform.sh status`；健康`curl -fsS http://127.0.0.1:8080/api/health`；回归`bash scripts/platform.sh test`；部署`bash scripts/platform.sh up`。


## 当前开发单元（2026-10-20）：传统资产底层行情源评估

- 用户将传统资产研究扩展到底层证券/商品行情，以降低研究层对 Binance REST 可用性的依赖。已评估 KORU ETF、SK海力士（KRX 000660）、NVDA、WTI 与天然气数据源；映射、供应商候选、实时/日频边界及来源链接见`docs/platform/underlying-market-data.md`，架构总览同步标为PLANNED。
- 来源结论：美国股票/ETF可选 Massive 实时 WebSocket 或有限额的 Twelve Data；Massive 个人实时股票/NYMEX期货候选各约$199/月，费用和许可须在购买前复核。Twelve Data 交易所目录中 XKRX 当前为EOD；SK海力士实时数据需核实 KRX/KOSCOM 或获许可的韩国券商 feed。EIA 的 WTI Cushing 和 Henry Hub 是日频现货参考，不能提供真实5秒现货K线；日内观察需另用明确标注的 NYMEX CL/NG 期货数据。用户还未选择供应商或天然气枢纽，故本轮没有编写适配器、请求密钥、订阅付费数据或新增行情。
- 语义约束：`KORUUSDT`/`SKHYNIXUSDT`/`NVDAUSDT`/`CLUSDT`底层关系仅作为独立研究序列，不替代永续成交价、标记价、保证金或资金费；不跨市场休市前向填充，不绕过地域/供应商限制。
- 验证（2026-10-20，文档单元）：`git diff --check`通过；`python3 scripts/check_publish.py --staged`通过（扫描247个文件，未发现禁止路径或凭据模式）。本轮仅改文档/任务规划，没有改Python/TypeScript代码，所以未运行应用测试、未重建Docker、未新增行情数据。
- 未解决：选定具有适当行情授权的供应商，之后实现US证券、KRX证券、EIA日频参考及/或NYMEX期货的只读适配器与WebSocket消费者；把供应商代码/时区/日历/币种映射到独立产品ID并进行后端隔离测试。项目Binance USDⓈ-M REST仍对当前Clash出口返回HTTP 451，相关合约三年历史归档依旧缺失。
- Git：来源映射文档提交`5eead8b`、交接记录`7e4aa84`及推送状态checkpoint `ccb5db4`均已推送至`feature/phase2-market-structure`；`git ls-remote`确认远端SHA=`ccb5db49f8cc36c084daf58398369dc3b2f336c7`。`gh`未安装，改用GitHub Actions公开API核验 run `39424716255`（run #64）：frontend、publish-hygiene、backend、risk-engine全部success。此处CI结果针对`ccb5db4`；之后只增加本段状态记录的checkpoint不改变源研究文件。`.local/`保持既有未跟踪状态。
- 恢复：`bash scripts/resume.sh`；查看`docs/platform/underlying-market-data.md`和`TASKS.md`；Git检查`git status --short --branch && git log -5 --oneline`；当前分支`feature/phase2-market-structure`。

## 当前开发单元（2026-10-21）：中国指数、资产分类与免key行情主备

- 新增六个A股指数研究资产：上证指数`cn_index:000001`、深证成指`cn_index:399001`、创业板指`cn_index:399006`、科创50`cn_index:000688`、中证500`cn_index:000905`、沪深300`cn:000300`。沪深300既有主键保持不变；新指数使用独立命名空间，避免与未来同代码个股冲突。六项均标注为指数/参考数据和研究用途，不是可交易证券。
- 免key行情：新浪作为主日线源、腾讯作为备用日线源；固定provider/代码映射，严格验证原生OHLCV、日期窗口和Decimal精度。切换备用源时在Parquet来源元数据记录真实提供方；不合并不同源蜡烛、不合成缺失价。数据限日线，刷新冷却至少60秒；当前接口未获官方稳定性/SLA承诺，授权边界见`docs/platform/underlying-market-data.md`。
- 资产页分类拆分为虚拟货币、股票、ETF、指数、商品、外汇、宏观参考等底层类别，并独立标出现货/永续/期货/参考产品类型。展示主源、备用源、密钥需求、计划频率和状态；无需密钥只表示免API key，不表示已连通、实时或可再分发。
- 服务启动已为六项指数创建三年日线后台补档。当前实际尝试：新浪和腾讯HTTPS均为`ConnectError`；六项任务各自动尝试3次后FAILED。没有为这些资产写入K线/伪造数据；两个Provider标记UNAVAILABLE。确认网络恢复后，可在图表页点击“采集底层三年数据”重新发起任务。无需提供API key。
- 其他现有源状态：Massive免费档需注册key且只保证EOD；Twelve Data免费key不包含XKRX权限（当前交易所表列在Pro/Venture）；EIA key免费注册。详细来源链接和市场使用许可约束见底层数据文档。
- 验证（2026-10-21）：`bash scripts/platform.sh test`为87 passed，1条既有Starlette TestClient/httpx弃用提示；`cd frontend && npm ci && npm test && npm run build`为3 passed、0 npm audit vulnerability、TS检查和Vite生产构建成功；`python3 scripts/check_docs.py`为711个具名函数均有docstring；涉及Python模块`py_compile`、市场源JSON解析和`git diff --check`通过。
- 运行：`bash scripts/platform.sh up`完成API、Web、NVIDIA runner重建并保留原`lab`服务和数据卷；健康API返回database/Redis connected，主页HTTP 200。确认资产目录六项已创建、全部没有K线记录；后台任务状态六项FAILED（attempts=3）。没有迁移或重置数据库，没有读取/改动私人凭据。
- 未解决：主机/Docker网络尚不能与`quotes.sina.cn`及`web.ifzq.gtimg.cn`建立HTTPS连接。用户无需申请密钥；需恢复到这两个公开域名443端口的出站网络后再从网页重新采集。网络恢复前页面会如实显示无行情/不可用，不能用于日内触发或实时决策。
- Git：实现提交`34ce6d9 feat: add China index public data sources`和交接提交`a6baf33`、`7ae0e91`均已推送到`origin/feature/phase2-market-structure`及Draft PR #11。Actions run `39531028569`（head `a6baf33`）及`39531363009`（head `7ae0e91`）的`backend`、`frontend`、`risk-engine`、`publish-hygiene`全部success。实现提交run `39530962337`在新提交到达后被GitHub取消。当前交接文档的下一次记录提交只改文档，其CI需在推送后另行观察。`.local/`为既有未跟踪用户目录，不纳入提交。
- 下一步：确认公共行情域名在Docker网络中可达；手动重试六个指数三年日线，再核验六项的首末时间、行数与SHA256；随后逐类为仍未绑定行情源的研究资产选择数据源，不把股票、指数、ETF、现货和相关永续合并。
- 恢复命令：`cd /home/xmfsamsara/Documents/quant_lab && bash scripts/resume.sh && git status --short --branch && git log -5 --oneline`；状态`bash scripts/platform.sh status`；API健康`curl -fsS http://127.0.0.1:8080/api/health`；后端回归`bash scripts/platform.sh test`；前端`cd frontend && npm ci && npm test && npm run build`；重建`bash scripts/platform.sh up`。


## 当前开发单元（2026-10-20）：原始证券与能源参考行情数据层

- 用户授权自主设计并完成原始底层行情数据部分，范围为不依赖Binance的传统证券/能源研究行情，并保持原产品与关联永续分离。源映射和交易频率边界见`config/platform/market_sources.json`与`docs/platform/underlying-market-data.md`。
- 已实现：Massive美国股票/ETF公开REST历史与近端聚合（NVDA、KORU、QID及登记的美国股票/ETF）；Twelve Data对KRX 000660和005930的XKRX日终OHLCV；EIA对WTI Cushing与Henry Hub日频标量值。系统不将能源值伪造成K线，也不混合股票与Binance永续。
- 已实现：三年底层K线以不可变内容寻址Parquet段和SHA256 manifest增量存储，EIA标量值另存Parquet schema；PostgreSQL migration `0003`新增标量元数据，DuckDB读取数据，Redis限制近端刷新。登录API/图表有底层数据查看和任务入口；启动时只对检测到本机密钥且缺缓存的已登记源排入后台导入任务，采集请求仅由worker执行。密钥值只由后端环境读取，不写页面、数据库或日志。
- 验证（2026-10-20，最终Docker运行）：`bash scripts/platform.sh up`成功构建并启动API/Web/worker/NVIDIA runner；`bash scripts/platform.sh status`中API、Web、PostgreSQL、Redis、proxy bridge均健康，worker运行；首页HTTP 200；`alembic current`为`0003 (head)`。安全布尔探测显示Massive/Twelve Data/EIA密钥当前均未配置，因此本机没有向供应商拉取真实行情，也没有声称数据已连通。
- 验证：`bash scripts/platform.sh test`最终为83 passed、1条既有Starlette `TestClient`/httpx弃用警告；覆盖来源注册与永续分离、原生K线Parquet/哈希、EIA标量Schema、密钥边界、启动补档和任务API。`cd frontend && npm ci && npm test && npm run build`为2个测试文件/3 tests passed且Vite/TypeScript构建成功。`python3 scripts/check_docs.py`：696个具名Python函数有docstring；`python3 -m compileall -q backend/app backend/migrations/versions backend/tests`及`git diff --check`通过。两次中间测试发现并修正临时目录隔离和显示/测试断言问题，最终测试通过。
- 未解决/范围边界：美国行情的实时或延迟由Massive账户许可决定，REST刷新间隔至少30秒；韩国源为日终，EIA为日频现货参考。当前没有用户供应商密钥/套餐，也没有供应商WebSocket实现，因此不能宣称任何来源实时已连通。韩国盘中需要获许可的KRX/KOSCOM或券商源；日内CL/NG期货应另按具体到期合约和换月规则实现，不能冒充现货。Binance当前451/DNS问题未被改写或绕过。
- Git/发布：实现提交`61f1177 feat: add original underlying market data pipeline`已推送到`origin/feature/phase2-market-structure`；`git ls-remote`确认远端ref=`61f1177940fbb2664ed50a0ab4fce38b6b08240d`。GitHub Actions CI run #66（SHA `61f1177`）已完成并success。`.local/`是既有未跟踪用户目录，保留且不纳入提交；未读取/改动`.runtime`凭据或既有行情/回测产物。
- 下一步：用户在私有`.runtime/platform.env`自行配置供应商密钥并确认许可后，重启`bash scripts/platform.sh up`，再从全球资产注册表查看待采集/已缓存的原始标的。持续实时需求另实施带许可校验的WebSocket/KRX/期货合约任务。
- 恢复命令：`cd /home/xmfsamsara/Documents/quant_lab && bash scripts/resume.sh && git status --short --branch && git log -5 --oneline`；本机服务`bash scripts/platform.sh status`；健康`curl -fsS http://127.0.0.1:8080/api/health`；后端测试`bash scripts/platform.sh test`；前端`cd frontend && npm test && npm run build`；重建启动`bash scripts/platform.sh up`。

## 2026-10-21：Massive 多 Key 共用配额与请求排队

- 官方规则已核对：同一 Massive 账户下的所有 API Key 共用订阅与请求限额；Basic 免费档每个资产类别每分钟5次，并仅提供EOD聚合，最多两年历史，实时数据需对应付费权限和交易所协议。新增 Key 只能分开集成，不能扩容。
- 已实现：Massive Stocks 的 API 与后台 worker 使用同一个 Redis 分布式分发锁；每次请求至少间隔16秒（留出余量，最多约4次/分钟），同标的图表仍遵守原有30秒缓存。429响应解析数字或HTTP日期格式`Retry-After`，共享队列至少冷却60秒并遵从更长的服务端要求；额度等待时长回传图表，资产页展示限额和冷却。Redis不可用时停止上游请求，不使用多Key轮换。其他集成消耗的账户额度不在本项目队列内，仍可能导致429。
- 验证（2026-10-21）：`bash scripts/platform.sh test`为90 passed，1条既有Starlette TestClient/httpx弃用警告；`cd frontend && npm test && npm run build`为3 passed、TypeScript及Vite构建成功；`python3 scripts/check_docs.py`为722个具名函数有文档；相关模块`py_compile`、配置JSON解析及`git diff --check`通过。测试未向Massive发请求。
- 运行验证：`bash scripts/platform.sh up`完成API、Web与研究镜像重建；API health为`status=ok,database=connected,redis=true`，首页HTTP 200；PostgreSQL、Redis、worker、API、Web、研究runner健康/运行，既有`lab`容器保持运行。没有重置数据库卷或研究数据。
- 当前问题/限制：配额队列只覆盖本项目经Massive Stocks适配器发出的请求；同账户其他应用或其他主机仍共用配额并可能造成429。免费Basic的EOD权限不等于实时行情，排队只能控制请求频率，不能提高数据权限或历史覆盖年限。此轮未做真实上游请求，避免额外消耗免费额度。
- Git：本轮待checkpoint提交后补记提交、远程推送和GitHub Actions状态。
- 下一步：确认项目外没有其它程序同时高频使用同一Massive账户；在额度允许时由页面触发一次小窗口请求，检查真实返回频率/计划权限及图表显示的排队等待。若账户仍429，应等待冷却并检查官方Usage/Plan，不新增同账户Key。
- 恢复命令：`cd /home/xmfsamsara/Documents/quant_lab && bash scripts/resume.sh && git status --short --branch && git log -5 --oneline`；测试`bash scripts/platform.sh test && (cd frontend && npm test && npm run build) && python3 scripts/check_docs.py`；重建`bash scripts/platform.sh up`；健康检查`curl -fsS http://127.0.0.1:8080/api/health`。

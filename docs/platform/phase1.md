# Phase 1：基础 Web 平台

## 开发清单

- [x] 原工程内新增backend/frontend，保留GPU lab和历史报告。
- [x] Python3.11 FastAPI / React TS / PostgreSQL / Redis RQ / Parquet。
- [x] Alembic初始迁移；随机管理员密码；Argon2id、会话失效、CSRF、来源限制、登录限流。
- [x] 全球资产登记、多主题与底层关系；13个精确Binance合约元数据快照；全部产品禁止实盘。
- [x] 四个优先标的真实历史15m导入；来源、SHA256、最近90天；独立队列与重试。
- [x] Binance公开REST适配器；启动时核验；失败状态和原因可见。
- [x] 交互K线、成交量、MA20/60、十字线、缩放；支持的较大周期严格聚合。
- [x] 显示时区、默认周期和标的、密码修改；后续能力显式PLANNED。
- [x] Docker Compose及开发/生产配置样例、README、AGENTS。
- [x] 后端20项测试、前端2项测试、生产构建与真实Chrome验收通过；详情见下方。

## 启动和访问

```bash
cd ~/Documents/quant_lab
bash scripts/platform.sh up
bash scripts/platform.sh credentials
```

访问 http://localhost:8080 。账号`admin`，随机密码只在本机`.runtime/bootstrap-admin.txt`（0600）。首次登录后可修改密码。修改后初始文件失效；本阶段没有公网密码找回流程。不要把该文件发到聊天中。

服务：web、api、worker、postgres、redis；现有lab仍保留。`up`先构建镜像再启动，避免错误拉取本地自建镜像。镜像首次需要网络下载依赖。

```bash
bash scripts/platform.sh status
bash scripts/platform.sh logs api
bash scripts/platform.sh logs worker
bash scripts/platform.sh test
cd frontend
npm ci
npm test
npm run build
```

测试使用独立`quant_test` PostgreSQL和Redis，不连接个人业务库。`scripts/platform.sh stop`只停止新增平台服务，保留数据和GPU lab。重新启动不会重置用户、设置或行情。

开发：`frontend`运行`npm run dev`，Vite代理指向127.0.0.1:8000；需要自行通过受控端口转发连接容器API，并将http://localhost:5173添加开发Origin白名单。默认Compose没有对外发布API或数据库端口。修改Web端口时，需要同步调整`.runtime/platform.env`里的Origin白名单。推荐直接使用构建后的8080界面。

## 已知边界

- 当前容器对Binance域名DNS解析失败，宿主机先前公开REST探测也曾返回451；公开行情自动更新存在网络/上游访问限制；任务显示实际失败原因，13个合约元数据快照与四个真实历史行情仍可研究。其他登记资产无行情时显示缺失。
- 本机8080是HTTP，仅绑定127.0.0.1。远程访问需要SSH转发或自行配置TLS/私网代理；使用HTTPS时配置Secure Cookie和Origin。
- 图表历史以本地已收盘行情为主，不提供逐笔盘口或WebSocket。选中三年缓存视图时每五秒刷新当前标的/周期，并独立标记未收盘K线；1m/3m/5m需各自公开采集，不从15m伪造。
- 总体周期摘要仅使用本地结构数据；无新闻、宏观事件或跨市场传导源。多模拟配置现可读取本地已归档数据生成离线回测权益/逐笔文件；没有已采集行情时不会生成模拟收益，离线既有回测与原模型结果未因本平台改写。
- 当前没有Web训练、实盘信号循环、SMTP/私有API凭据、2FA或实盘交易。原脚本与离线报告可继续使用，未重跑或改变原回测。
- 单人本机版不等于公网多租户生产版。生产需TLS、备份恢复演练、监控、严格角色授权、密钥服务与后续审批/账本测试。

## Phase 2 计划

### Phase2首个增量（2026-10-18）

已实现按需三年历史归档服务：所选标的/周期通过`FETCH_ARCHIVE`任务从公开Kline接口分页回填，逐页保存独立不可变Parquet分段和SHA256 manifest；失败后重试从最后已保存K线继续。图表页面提供“采集三年历史”和“查看三年缓存”，缓存读取只访问本地磁盘，不触发交易所网络请求。归档只允许精确核验的USDT永续合约，使用资产注册表代码，不从底层股票/ETF推定币安产品；因此资产目录里所有经精确核验的永续都可按需使用，其他登记资产维持未验证/缺数据状态。新归档不覆盖90天快照。

实现通过mock分页、续传、损坏检测和API边界测试；本机测试`bash scripts/platform.sh test`：30 passed。前端2 passed，TypeScript及Vite构建通过。由于此前主机访问币安域名失败，本单元没有声称真实回填成功；运行时在用户点击且已核验合约可用时才会采集。

接入已有经典道氏和1-2-3作为独立分析器；配置ATR/幅度/成交量/有效期；保存pivot_at与confirmed_at。添加多候选波浪硬规则及失效点、支撑压力、趋势线、多周期共振、手工标注及历史回放。先用确定样本和prefix不变性测试证明无未来数据，再对接Web图表。模型中心与训练调度仍属于Phase3。

### Phase2 多周期结构归档图层（2026-10-18）

新增已认证只读`GET /api/market/structure`：为所选标的读取本地三年归档，不在查看时请求Binance；每周期最多取配置范围内的真实K线，统一按截止时点分析已收盘样本。返回趋势方向/趋势线和破坏状态、确认版道氏1-2-3、Elliott候选、价格-RSI背离、各事件发生与确认时间、证据及归档manifest SHA256。价格类字段以十进制字符串返回。缓存缺周期时返回缺档清单；页面可叠加当前图表周期线和破位/123/背离标记，并显示多周期状态。相关原理保持研究候选，不代表获利概率，也不调用下单接口。

验证：本机前端`npm test`为2 passed且`npm run build`成功；Python语法检查和`python3 scripts/check_docs.py`通过（437个具名函数）；GitHub PR提交`32f1a9e`的Python3.11隔离backend、frontend、risk-engine和publish-hygiene四项CI全部success。该提交时本机Docker构建受ARM64 NumPy下载停滞影响；后续刷新/情绪/配置账号已分单实现，见下方记录。

### Phase2 当前周期实时刷新（WIP，2026-10-19）

三年缓存视图打开时仅刷新当前选中标的和周期，UI等待前一请求完成后发出带CSRF的同源POST；后端使用固定无密钥Binance公开GET适配器。已收盘K线作为新的不可变Parquet分段写入长期本地缓存，未收盘快照单独原子替换到`live.json`并附SHA256，页面标记未收盘/最近更新时间；断线保留旧行情并递增退避。行情数据不触发结构信号，结构仍只用闭合K线。

验证：`cf678fd`与快照哈希修正`dbb3602`的Python3.11隔离backend、frontend、risk-engine与publish-hygiene检查全部success；后端mock覆盖新闭合K线追加、正在形成的K线快照与SHA256、认证和CSRF，前端2 passed且生产构建通过。未访问真实Binance。当前单API进程锁避免本机清单竞争；跨API副本共享节流需在横向部署前增加。受本机Docker下载ARM64 NumPy wheel停滞影响，新容器尚未重建，当前运行容器仍为旧版；网络可达后运行`bash scripts/platform.sh up`、`bash scripts/platform.sh test`，再以隔离账号验收页面和连续刷新。当前功能仅在已选中的三年缓存图表可用，关闭页面/切换到90天缓存后停止轮询。

### Phase2 总体结构周期摘要（2026-10-19）

新增本地只读`GET /api/market/sentiment`草稿，默认聚合四个优先标的及15m/1h/4h/1d趋势。分数以方向指数呈现：趋势70%、道氏30%，最近八根K线内的已确认RSI背离最多±0.2；总体按各资产等权汇总。20根已收盘对数收益波动率只作背景展示、不改变分数。4h与1d反向或高周期趋势线破坏为TRANSITIONING；缺档保留UNKNOWN。UI会披露每项趋势、道氏、背离、波浪候选、20根波动、支撑/压力及来源哈希，分数明确不是胜率。

验证：提交`9225ecc`的Python3.11 backend、frontend、risk-engine、publish-hygiene四项PR CI全success；后端测试模拟结构响应，使用隔离数据库且不访问外部行情。研究引擎`bash scripts/container.sh test` 156 passed；前端`npm test` 2 passed，`npm run build`成功；457个Python函数docstring检查通过。趋势/道氏分解、背离新近窗口、跨4h/1d方向冲突、缺档UNKNOWN和API周期白名单都有测试。浏览器端到端验收和本机新API镜像启动仍待下载网络恢复；新功能已通过CI但未声称在当前本机容器运行。

### 多模拟回测配置账号（2026-10-19）

Alembic 0002新增用户隔离配置表；起始资本为NUMERIC(28,8)，保存模型/技术理论权重、精确已核验永续标的、周期、杠杆/保证金、1小时至90天最短/最长持仓策略、退出方式、revision与configuration SHA256。止损仍可在最短持仓门槛前执行。React工作台可创建、查看和编辑多个账号配置，并按用户显式请求三年交易价、标记价与资金费数据。

新增Python3.11后端回测服务：以已收盘原生周期为基础，按收盘可知时点对齐高周期趋势/道氏/波浪/背离，融合已实现反转形态分数，并强制方向服从最高配置周期趋势；下根K线执行时使用真实归档标记价、资金费，显式计入固定手续费/滑点/维持保证金假设，支持持仓时间门槛和提前止损。模型权重含未登记深度模型时明确拒绝，不会自动加载或训练。每个用户账号保存单独的净值和逐笔Parquet、收益/回撤/持仓摘要、跳过资产原因以及配置/算法/数据SHA256，API所有者校验后才可读取；前端展示组合曲线、逐笔和每标的结果。缺少本地行情时资产留现金并显示跳过原因；不联网拉取，不下单，不代表持续纸面账户。

验证：`bash scripts/platform.sh test`使用隔离PostgreSQL/Redis及合成归档，回测端到端、时间前缀不变性、资金/哈希文件和API所有权覆盖共47项通过（最终验证记录见`PROJECT_PROGRESS.md`）。前端2项测试、TypeScript与Vite构建通过；`scripts/check_docs.py`及发布扫描通过。真实Binance三年数据尚未由用户点击回填，因此没有真实资产回测收益结果；深度模型注册/加载及账号间叠图比较保留在后续Phase3/4。

### 可配置多周期与多期限回测（2026-10-19）

模拟账号可独立设置15m至1d周期相对权重、顺最高周期趋势/多理论确认逆势反弹/多周期多数方向规则、3/7/30天主要评估期限及跨期限共识权重。必须至少有一个已选周期权重大于0。多周期特征只按已完成K线收盘时间向下对齐；逆势反弹需要基础周期反转形态和背离、道氏或趋势证据之一共同确认。

每次入场保存3/7/30天方向分数和启发式价格中心/波动情景带；回测结束后再用对应未来实际收盘价格统计方向命中和方向化收益。未来价格不进入入场决策；可用数据尚未覆盖评估时点时结果留空。情景带目前未做概率校准，不是置信区间或目标价保证。最短持仓API/UI下限为1小时，止损仍可提前执行；最长持仓允许至90天。

模型调研结论：公开资料没有能证明“某一家量化公司模型普遍最优”的证据。TimesFM 3.0在其公开通用预测基准表现突出，但官方预训练权重目前限非商业/非生产用途，不宜直接作为本平台可发布模型；[官方仓库](https://github.com/google-research/timesfm)列出多变量支持与许可。金融收益研究发现预训练时序模型相对随机游走的优势小且稀疏，[金融收益基准研究](https://arxiv.org/abs/2606.27100)；另一项覆盖2010–2025日频多资产期货的研究报告VSN+LSTM在该实验的总体Sharpe较高，同时不同架构在下行风险和交易成本承受力各有优势，[金融深度模型基准](https://arxiv.org/abs/2603.01820)。因此候选模型应在本地滚动样本外基准中和简单基线、现有GRU、VSN+LSTM、PatchTST/Chronos类预测器比较，按扣费后净值、回撤、换手、稳定性及期限分别评估，不按通用预测排名直接选型。

资源检查（2026-10-19）：宿主机报告121 GiB内存、105 GiB可用、15 GiB swap空闲；`/home`所在卷3.7 TiB、可用约1.0 TiB；本地行情目录约1.2 MiB。平台Compose已运行PostgreSQL持久卷、Redis AOF和RQ worker。当前观测没有显示缓存或磁盘压力；Redis/RQ已承担当下队列需求，暂不增加RabbitMQ/Kafka。随三年行情增长再按磁盘、Parquet增长、Redis内存和队列积压监控调整。

验证（2026-10-19）：`bash scripts/platform.sh test`隔离PostgreSQL/Redis中48 passed、1项既有Starlette TestClient弃用提示；包括账户4小时配置接受、1小时策略回测、逆势模式配置、跨期限字段与未来样本前缀不变性。`cd frontend && npm test && npm run build`为2 passed且生产构建成功；`python3 scripts/check_docs.py`为529个具名函数全通过，`python3 -m compileall -q backend/app backend/tests`成功。提交`3d22cac`的GitHub Actions run `39195935406`中backend、frontend、risk-engine、publish-hygiene四项全success；本机`bash scripts/platform.sh up`后API数据库/Redis健康且首页HTTP 200。尚未接入新的深度模型，也没有真实三年行情上的预测校准或盈利结论。

### 按需采集合约标记价格与资金费（2026-10-19）

新增固定Binance公开GET适配器、持久`FETCH_DERIVATIVE_DATA`任务和账号页面按钮。按精确核验产品、原生周期、最长三年UTC时间窗采集标记价格K线与实际结算资金费率；数据分流写入本机`DATA_ROOT/derivatives/<symbol>/<interval>/mark|funding/`不可变Parquet，分页后原子更新manifest并校验每段SHA256。采集失败保留已成功页面以便续传；只在用户点击后联网。重叠或扩展请求可沿用既有窗口；不支持向更早时间扩展。

验证：新API/任务、分段哈希、Decimal往返、用户所有权、损坏文件和断点续传的mock测试计入`bash scripts/platform.sh test`；Python3.11 ARM64镜像构建成功，隔离PostgreSQL/Redis 44 passed，1项Starlette TestClient弃用提示。前端2 passed且TypeScript/Vite构建成功；498个具名Python函数文档检查、发布扫描和语法检查通过。提交`5d2a028`的GitHub backend、frontend、risk-engine、publish-hygiene CI全success（run 39145561898）。`bash scripts/platform.sh up`后API健康端点/首页HTTP 200且新route已注册。未发起真实Binance请求；随后开发的离线回测/曲线实现详见上一节，本机实际行情收益仍未知。

### 系统页面整套多模型回测（2026-10-19）

模拟与回测页面新增全模型任务面板：可选四个优先标的、八种已登记模型及15分钟至4小时入场周期，在页面启动同一离线研究引擎。任务保存在现有`scheduled_tasks`表，由独立NVIDIA PyTorch容器处理；引擎在每个模型验证折及周期/退出配置完成时上报实际进度，页面每2秒轮询并显示百分比与当前标的/周期/模型/折。报告保存在`.runtime/research-runs/<UUID>`，页面按登录用户隔离读取总体摘要、模型/标的报告、不同退出模式排名、验证期权益曲线和逐笔模拟操作。已有`runs/`中带`batch_summary.json`的历史报告作为只读归档展示，不修改旧报告。GPU容器无Binance私有密钥，邮件仅模拟，禁止下单。

验证：新增认证任务输入/所有权、固定UUID报告路径、曲线/逐笔报告读取、历史报告只读及路径穿越拒绝测试；最终`bash scripts/platform.sh test`隔离PostgreSQL/Redis为51 passed，1条既有Starlette TestClient弃用提示。前端`npm test`为2 passed，`npm run build`成功；`python3 scripts/check_docs.py`覆盖559个具名Python函数，语法检查通过。`bash scripts/platform.sh up`完成API/Web/NVIDIA runner构建和启动，API健康、CUDA可用；未从页面触发完整新回测，因此尚未验证一次完整端到端GPU任务耗时/报告。

## 实际验收记录（2026-10-18，DGX Spark ARM64）

- Docker：Python3.11.17；FastAPI0.143.3；SQLAlchemy2.1.5；RQ2.12.0；PostgreSQL15、Redis6。基础镜像摘要、完整Python约束锁和npm锁文件已固定。
- 资产注册80项；13项Binance精确合约代码有公开元数据快照。未上市/Pre-IPO属性按供应商实际类型保存，不因名称推断。
- 四个优先标的各8,636根真实15m K线，总计34,544根；原始来源、SHA256与UTC时间保留。较大周期仅聚合完整桶。
- 后端：独立PostgreSQL/Redis，20 passed；有一项Starlette关于未来TestClient/httpx2迁移的弃用提示，不影响当前测试。
- 前端：2 passed，TypeScript检查与Vite生产构建成功；npm audit记录0项已知问题（不是对未来漏洞的保证）。
- 真实Chrome：实际登录、四个标的×6周期共24组图表、资产/任务/能力/设置页面和390px移动视图；未捕获浏览器异常。图表支持窗口缩放查看整个90天15m历史。
- 登录退出后再次部署，账号和34,544根行情保留；API、Web、PostgreSQL与Redis健康；GPU lab持续运行。
- 外部连接限制：容器内fapi.binance.com DNS解析失败。自动核验真实失败并最多重试3次；页面不会把缓存标成实时行情。当前使用已验证连接器采集的真实历史快照。

验收产物位于`runs/platform_phase1/`：`browser-check.json`、`runtime-check.json`、测试日志、依赖审计和页面截图。重新验证浏览器需启动隔离Chrome后执行`node scripts/platform_browser_check.mjs 9223`；脚本只在内存中读取本机初始凭据并在结束时退出登录。若已改密码，初始密码文件不能再用于此脚本，需改用独立测试账号。

### Rule backtest coverage update (2026-10-19)

The authenticated research page lists all 13 exact exchange-verified contract IDs. A local coverage check enables only symbols that have real bars, funding, mark-price bars and a matching frozen evaluation plan. New system backtests now select the deterministic multi-timeframe structure/Dow-123 rule model only; old ML reports are preserved for read-only review. The selected report shows how many completed long and short operations it contains. This does not implement model training; causal multi-timeframe training features and bottom-structure watch zones remain planned. Current network access is blocked: direct Binance DNS lookup fails and the configured proxy returns HTTP 451.

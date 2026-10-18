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
- 总体周期摘要仅使用本地结构数据；无新闻、宏观事件或跨市场传导源。多模拟配置尚无回测账本/收益曲线，离线既有回测与原模型结果未因本平台改写。
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

Alembic 0002草稿新增用户隔离配置表；起始资本为NUMERIC(28,8)，保存模型/技术理论权重、精确已核验永续标的、周期、最大杠杆/保证金、24小时至21天持仓策略、退出方式、revision与configuration SHA256。React工作台可创建、查看和编辑多个账号配置；新账号默认研究用途，不建成交/权益记录，不下单，不声称回测收益。

新增Python3.11后端测试覆盖Decimal精度、配置hash变更、修订号、权重/周期/杠杆/深度模型绑定约束、标的核验、CSRF和所有者隔离；测试只用`quant_test`和mock。提交`e9cbc3c`四项GitHub CI通过。本地页面可编辑账号、点击创建三年行情任务；后台尚无回测运行器或收益曲线，不能把普通价K线复制成标记价格或忽略资金费后标记净合约收益。

### 按需采集合约标记价格与资金费（2026-10-19）

新增固定Binance公开GET适配器、持久`FETCH_DERIVATIVE_DATA`任务和账号页面按钮。按精确核验产品、原生周期、最长三年UTC时间窗采集标记价格K线与实际结算资金费率；数据分流写入本机`DATA_ROOT/derivatives/<symbol>/<interval>/mark|funding/`不可变Parquet，分页后原子更新manifest并校验每段SHA256。采集失败保留已成功页面以便续传；只在用户点击后联网。重叠或扩展请求可沿用既有窗口；不支持向更早时间扩展。

验证：新API/任务、分段哈希、Decimal往返、用户所有权、损坏文件和断点续传的mock测试计入`bash scripts/platform.sh test`；Python3.11 ARM64镜像构建成功，隔离PostgreSQL/Redis 44 passed，1项Starlette TestClient弃用提示。前端2 passed且TypeScript/Vite构建成功；498个具名Python函数文档检查、发布扫描和语法检查通过。提交`5d2a028`的GitHub backend、frontend、risk-engine、publish-hygiene CI全success（run 39145561898）。`bash scripts/platform.sh up`后API健康端点/首页HTTP 200且新route已注册。未发起真实Binance请求。模拟净值账本、模型比较、逐笔和收益曲线仍未实现。

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

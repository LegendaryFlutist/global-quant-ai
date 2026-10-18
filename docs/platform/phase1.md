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
- 图表为已收盘历史缓存，不是实时报价或盘口。1m/3m/5m需要独立公开采集；不从15m伪造。月线90天只有很少完整K线，不进行趋势预测。
- 全局风险为UNKNOWN；没有接入新闻、情绪或全球联动计算。图表只实现MA和成交量，其余技术分析与人工标注在Phase2。
- 当前没有Web训练、实时信号循环、持久模拟账户、SMTP/私有API凭据、2FA或实盘交易。原脚本与离线报告可继续使用，未重跑或改变原回测。
- 单人本机版不等于公网多租户生产版。生产需TLS、备份恢复演练、监控、严格角色授权、密钥服务与后续审批/账本测试。

## Phase 2 计划

### Phase2首个增量（2026-10-18）

已实现按需三年历史归档服务：所选标的/周期通过`FETCH_ARCHIVE`任务从公开Kline接口分页回填，逐页保存独立不可变Parquet分段和SHA256 manifest；失败后重试从最后已保存K线继续。图表页面提供“采集三年历史”和“查看三年缓存”，缓存读取只访问本地磁盘，不触发交易所网络请求。归档只允许精确核验的USDT永续合约，使用资产注册表代码，不从底层股票/ETF推定币安产品；因此资产目录里所有经精确核验的永续都可按需使用，其他登记资产维持未验证/缺数据状态。新归档不覆盖90天快照。

实现通过mock分页、续传、损坏检测和API边界测试；本机测试`bash scripts/platform.sh test`：30 passed。前端2 passed，TypeScript及Vite构建通过。由于此前主机访问币安域名失败，本单元没有声称真实回填成功；运行时在用户点击且已核验合约可用时才会采集。

接入已有经典道氏和1-2-3作为独立分析器；配置ATR/幅度/成交量/有效期；保存pivot_at与confirmed_at。添加多候选波浪硬规则及失效点、支撑压力、趋势线、多周期共振、手工标注及历史回放。先用确定样本和prefix不变性测试证明无未来数据，再对接Web图表。模型中心与训练调度仍属于Phase3。

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

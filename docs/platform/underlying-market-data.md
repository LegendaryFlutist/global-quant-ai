# 原始标的行情源评估

评估日期：2026-10-20。本文记录底层股票、ETF、商品参考价与 Binance 永续合约分开接入的设计及当前实施状态。

## 产品映射

| 研究目标 | 原始产品/交易场所 | 建议记录的产品身份 | 边界 |
|---|---|---|---|
| 韩国指数 3 倍 ETF 相关标的 | KORU ETF（美国上市） | `us_equity:KORU`；与 `binance_usdm:KORUUSDT` 建立 `tracks_underlying` 关系 | 这是杠杆 ETF 自身价格，不是 MSCI Korea 指数价格；日杠杆路径效应不能被当作指数收益简单乘 3。 |
| SK 海力士 | 韩国交易所普通股 `000660` | `krx_equity:000660`；与 `binance_usdm:SKHYNIXUSDT` 单独关联 | 保留 KRX 交易日历、韩元计价及休市状态，不与 Binance 合约蜡烛拼接。 |
| NVIDIA | Nasdaq 上市普通股 `NVDA` | `us_equity:NVDA`；与 `binance_usdm:NVDAUSDT` 单独关联 | 股票仅在其交易时段有原生交易；休市时不补价。 |
| WTI 原油 | 现货参考 `WTI Cushing`；日内近似参考为 NYMEX `CL` 期货 | 分别记录 `commodity_spot:WTI_CUSHING` 与 `nymex_future:CL` | EIA 现货参考按日发布；CL 是有到期/换月的期货，不能标成现货，也不能假设等于 Binance CLUSDT。 |
| 天然气 | 默认候选为 Henry Hub 现货；日内近似参考为 NYMEX `NG` 期货 | 分别记录 `commodity_spot:HENRY_HUB` 与 `nymex_future:NG` | 天然气现货按具体枢纽报价，不存在单一全球现货价。Henry Hub 官方序列为日频；NG 是期货。项目当前资产目录还没有天然气资产映射。 |

上述底层序列可以作为技术结构和市场状态的独立研究输入。若要评估 Binance 永续合约的成交、收益、标记价强平和资金费，仍必须保留合约本身的数据；底层资产价格不能替代这些字段。

## 供应商候选

| 覆盖 | 候选来源 | 当前可确认的频率/限制 | 适用判断 |
|---|---|---|---|
| 美国股票/ETF（NVDA、KORU） | Massive Stocks；另可试 Twelve Data | Massive Basic 免费档有每分钟5次/资产类别的请求上限，提供最多两年EOD聚合；不能据此当作实时行情。Twelve Data Basic 免费且有额度，但非美国市场取决于交易所覆盖/计划。 | Massive账户中所有API Key共用订阅和限额；个股数据权限/时效看Stocks订阅计划。key由用户在本机私有环境配置，不进Git或日志。 |
| SK 海力士 | KRX/KOSCOM 官方行情授权；Twelve Data 作为日线候选 | Twelve Data 当前将 Korea Stock Exchange (`XKRX`) 标为 EOD，要求 Pro/Venture 级别；免费 Basic key 不代表有该市场权限。 | 当前只支持日线；若必须实时，需核实KRX/KOSCOM许可或授权券商行情API。 |
| 中国六个指数 | 新浪无key公开日线；腾讯无key公开日线备用 | Sina/Tencent的常见行情/K线接口可免密钥读取，但属于公开页面接口，没有稳定性、频率、延迟或服务级别承诺；本机当前网络未能完成实时连通性实测。 | 已接入Sina主源和Tencent fallback，仅启用日线、60秒请求冷却；标记研究数据，启动时自动排缺档任务。再分发或商业使用前需核对授权。 |
| NYMEX CL、NG 期货 | Massive Futures；CME/NYMEX 授权数据服务 | Massive Futures Advanced 页面列出 CME/CBOT/COMEX/NYMEX 实时 WebSocket、秒/分钟聚合和历史数据，个人方案约 `$199/月`；更低阶方案提供延迟数据。 | 可用于实时观察期货近月/指定合约；必须保留具体到期月份、交易时段和换月规则。价格与 Binance 永续并非同一产品。 |
| 原油、天然气现货参考 | 美国 EIA Open Data | EIA API key需免费注册；EIA bulk download无需key。EIA提供WTI Cushing与Henry Hub日频序列，不能产出真实的5秒K线。 | 适合日线结构、宏观背景和历史参考，不适合日内实时触发。 |
| Binance 加密永续 | 当前 Binance 公开行情适配器/已连接公开行情连接器 | 与传统资产供应商无关；当前本地 REST 经 Clash 对 `fapi.binance.com` 收到 HTTP 451。 | 保留独立状态、缓存和错误；不要用股票/商品行情伪装币安合约行情。 |

来源：[Massive账户Key与共享限额说明](https://massive.com/knowledge-base/categories/account)、[Massive免费档速率/数据时效](https://massive.com/knowledge-base/article/how-quickly-can-i-access-massives-market-date)、[Massive Stocks计划](https://www.massive.com/stocks)、[Twelve Data免费方案](https://twelvedata.com/pricing)、[Twelve Data XKRX权限](https://twelvedata.com/exchanges?level=pro)、[EIA免费key注册](https://www.eia.gov/opendata/register.php)、[新浪公开行情接口技术说明（社区整理，非官方API文档）](https://alanhere.com/2019/06/11/sina-stock-api/)、[上交所行情服务/许可办理说明](https://www.sse.com.cn/transparency/services/index.shtml)、[上证指数数据使用许可规则](https://www.sse.com.cn/market/sseindex/diclosure/c/c_20150911_3984913.shtml)。接口稳定性、权限和价格可能变化，使用前应重新核验。

## 项目接入建议

1. 先按各自交易场所登记底层资产、供应商代码、币种、日历与数据授权；关系表只表达关联，不合并底层证券和永续合约。
2. 需要 5 秒级实时状态时优先使用供应商 WebSocket，在 Redis 暂存最新真实成交/报价；由原始成交事件按对应交易日历更新未收盘图表蜡烛，闭合后写入带来源、时间戳和 SHA256 的不可变 Parquet。供应商仅提供日频时，页面如实显示日频与最后更新时间。
3. 由用户选择/开通供应商账户并按其许可条款提供本地密钥后，才实现相应适配器。候选供应商不代表已购买、已授权或已连通；不为修复 Binance 451 而修改限制地区、隐藏来源或绕过供应商条款。
4. 模型可将底层行情作为独立特征输入；含 Binance 永续成交价、资金费、标记价或保证金的回测仍须取得对应真实合约数据。不同市场休市时段不得前向填充伪造新 K 线。

## 当前实施状态

项目已增加服务端只读适配器与源头行情注册表：Massive对应美国股票/ETF，Twelve Data只用于XKRX日线，EIA只用于WTI Cushing与Henry Hub日度标量参考，新浪公开行情与腾讯备用公开行情服务于六个中国指数。股票/ETF/指数K线按资产与周期写入不可变Parquet分段和SHA256 manifest；EIA原始日值使用独立标量Parquet。PostgreSQL保存来源状态与文件指针，Redis缓存近期图表刷新。原始底层价格与同名Binance USDM永续保持独立资产ID、文件和数据源。

服务启动会为中国指数免key公开源排队补齐已登记的日线；其他需凭据的源仅在本机配置key后排队。Massive的API与后台worker通过同一Redis账户/Stocks类别队列调度，每次请求至少间隔16秒（每分钟最多4次，为官方Basic每分钟5次留出余量）；图表同一标的仍受30秒刷新缓存约束。收到HTTP 429时，队列按服务端`Retry-After`或至少60秒的配置冷却全局暂停。增加同一账户的Key不会增加容量，浏览器外部程序或其他集成消耗的额度不在本系统队列控制范围内，仍可能造成429。Twelve Data至少6小时、Sina/Tencent至少60秒。当前没有实现这些供应商WebSocket；Massive Basic也只承诺EOD聚合，实时数据需匹配的付费权限和交易所协议；免key不代表实时、稳定或已获数据再分发授权。新浪/腾讯上游本轮未能从当前主机完成TLS连通性实测，成功状态须以worker实际请求为准。Basic最多两年历史，不能以该层验证三年完整覆盖；请求窗口和上游最多1023根/返回限制不同，报告需按实际覆盖显示。股票/ETF/指数日线不用于日内实时入场触发。

安全边界：数据源 URL、交易所代码和资产映射由仓库内注册表白名单管理；供应商密钥只从后端环境变量读取，不接受浏览器请求传入。适配器只发公开 GET 请求，响应错误不回显 API key 或原始 URL。该工作不改变 Binance 的地区响应，不对 Binance 合约行情做替代或伪造，也不启用交易执行。

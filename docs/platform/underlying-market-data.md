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
| 美国股票/ETF（NVDA、KORU） | Massive Stocks；另可试 Twelve Data | Massive Stocks Advanced 页面标示实时数据、WebSocket，个人方案页面显示约 `$199/月`；Twelve Data Basic 标示美国股票/ETF实时数据但请求额度有限。具体订阅权利、个人/商业用途和交易所费用以账户及合同为准。 | Massive 更适合作为 5 秒页面更新的流式候选；Twelve Data 可用于低频接入试验。任何密钥由用户在本地私有环境配置，不进 Git 或日志。 |
| SK 海力士 | KRX/KOSCOM 官方行情授权；Twelve Data 作为日线候选 | KRX 的实时市场信息属于市场数据产品；Twelve Data 当前交易所目录把 Korea Stock Exchange (`XKRX`) 标为 EOD（日终），不能满足 5 秒实时。 | 若必须实时，需要核实 KRX/KOSCOM 数据使用许可，或使用用户已有且允许此用途的韩国券商行情 API。不能把 EOD 当作实时。 |
| NYMEX CL、NG 期货 | Massive Futures；CME/NYMEX 授权数据服务 | Massive Futures Advanced 页面列出 CME/CBOT/COMEX/NYMEX 实时 WebSocket、秒/分钟聚合和历史数据，个人方案约 `$199/月`；更低阶方案提供延迟数据。 | 可用于实时观察期货近月/指定合约；必须保留具体到期月份、交易时段和换月规则。价格与 Binance 永续并非同一产品。 |
| 原油、天然气现货参考 | 美国 EIA Open Data | EIA 提供 WTI Cushing 与 Henry Hub 现货序列，频率为日；API 需要注册 EIA API key。它不能产出真实的 5 秒 K 线。 | 适合日线结构、宏观背景和历史参考，不适合日内实时触发。 |
| Binance 加密永续 | 当前 Binance 公开行情适配器/已连接公开行情连接器 | 与传统资产供应商无关；当前本地 REST 经 Clash 对 `fapi.binance.com` 收到 HTTP 451。 | 保留独立状态、缓存和错误；不要用股票/商品行情伪装币安合约行情。 |

来源： [Massive 股票数据与实时 WebSocket](https://www.massive.com/stocks)、[Massive Futures 计划与价格](https://massive.com/pricing?product=futures)、[Twelve Data 计划](https://twelvedata.com/pricing)、[Twelve Data 交易所覆盖表](https://twelvedata.com/exchanges?level=overview)、[KRX 市场数据产品说明](https://data.krx.co.kr/inc/datasale/Market%20Data%20Product%20Brochure.pdf?v=20250732)、[EIA Henry Hub 日频天然气现货](https://www.eia.gov/opendata/browser/natural-gas/pri/fut?data=value&facets=series&frequency=daily&series=RNGWHHD%3B)、[EIA WTI Cushing 现货历史](https://www.eia.gov/dnav/pet/hist/rwtcd.htm)、[EIA API 注册与条款](https://www.eia.gov/opendata/documentation.php)。价格与覆盖信息会变化，应在签订或配置前重新确认。

## 项目接入建议

1. 先按各自交易场所登记底层资产、供应商代码、币种、日历与数据授权；关系表只表达关联，不合并底层证券和永续合约。
2. 需要 5 秒级实时状态时优先使用供应商 WebSocket，在 Redis 暂存最新真实成交/报价；由原始成交事件按对应交易日历更新未收盘图表蜡烛，闭合后写入带来源、时间戳和 SHA256 的不可变 Parquet。供应商仅提供日频时，页面如实显示日频与最后更新时间。
3. 由用户选择/开通供应商账户并按其许可条款提供本地密钥后，才实现相应适配器。候选供应商不代表已购买、已授权或已连通；不为修复 Binance 451 而修改限制地区、隐藏来源或绕过供应商条款。
4. 模型可将底层行情作为独立特征输入；含 Binance 永续成交价、资金费、标记价或保证金的回测仍须取得对应真实合约数据。不同市场休市时段不得前向填充伪造新 K 线。

## 当前实施状态

项目已增加服务端只读适配器与源头行情注册表：Massive 对应美国股票/ETF，Twelve Data 只用于 XKRX 日线，EIA 只用于 WTI Cushing 与 Henry Hub 日度标量参考值。股票/ETF K线按资产与周期写入不可变 Parquet 分段和带 SHA256 的 manifest；EIA 原始日值使用独立标量 Parquet 模式。PostgreSQL 保存来源状态与文件指针，Redis 缓存近期图表刷新，原始底层价格与同名 Binance USDM 永续保持独立资产 ID、文件和数据源。

启动后只有在本机私有环境中检测到对应供应商密钥时，系统才会为尚无三年历史的已登记底层标的排队。登录后的图表可手动创建 1095 天采集任务；页面刷新时读取供应商近端数据并受 Redis 冷却时间约束。当前 Massive 刷新最短缓存间隔为 30 秒，Twelve Data 韩国股票只提供日终周期；EIA 按日发布标量值。当前代码没有实现供应商 WebSocket，也没有实际供应商账户、许可或密钥，因此不声称已经取得实时行情或完成历史下载。若要低延迟显示，Massive 需要有相应实时权限；韩国股票盘中行情需要 KRX/KOSCOM 或用户已有的授权供应商。

安全边界：数据源 URL、交易所代码和资产映射由仓库内注册表白名单管理；供应商密钥只从后端环境变量读取，不接受浏览器请求传入。适配器只发公开 GET 请求，响应错误不回显 API key 或原始 URL。该工作不改变 Binance 的地区响应，不对 Binance 合约行情做替代或伪造，也不启用交易执行。

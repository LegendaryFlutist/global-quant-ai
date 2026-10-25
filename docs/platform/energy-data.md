# 免费能源参考数据与持续刷新

本单元优先解决原油页面停留在一次性 Binance MCP 历史快照的问题。它增加独立能源参考，不把原油期货、现货与 Binance CLUSDT 混写。

## 来源选择与实测

2026-10-22 在现有 API 容器及已有代理环境中验证：Yahoo CL=F、NG=F 15分钟接口均返回 HTTP 200，源报价时间到10月22日；EIA公开历史页/XLS可达。FRED公开CSV一次请求超时，未作为自动备用。该探测不是持续可用性或授权实时行情保证。

|用途|来源与固定产品|免费范围/限制|系统请求周期|
|---|---|---|---|
|盘中能源参考|Yahoo CL=F、NG=F；新ID yahoo_energy:CL/NG|无需密钥的非官方个人研究接口；延迟未承诺；供应商连续代码，换月规则未核验|15分钟原生K线每300秒检查；15m历史请求上限59天|
|长期连续参考|同上原生1d|最多请求1095天，以实际返回覆盖为准；不承诺三年分钟线|每21600秒检查|
|官方现货参考|EIA RWTC / RNGWHHD，原eia:*身份|公开历史HTML表，无需API key；日度数值按发布周期更新，不提供盘中OHLC|每21600秒检查，首次请求1095天|

官方说明：[CME延迟报价](https://www.cmegroup.com/market-data/browse-data/delayed-quotes.html)至少延迟10分钟，这不等于本系统取得CME实时授权。[EIA WTI](https://www.eia.gov/dnav/pet/hist/RWTCd.htm)和[Henry Hub](https://www.eia.gov/dnav/ng/hist/rngwhhdd.htm)明确提供现货日度参考；[Yahoo能源页面](https://finance.yahoo.com/markets/commodities/)标注延迟产品。未使用多个key轮换规避配额，未绕过地区/付费限制。

## 运行结构

- `adapters/energy_public.py`：固定来源、严格代码/币种/市场/周期校验、HTTP超时/大小边界、429退避、公共原始响应内容寻址SHA。EIA表结构变化即拒绝解析，缺日期/缺值不填充。
- `services/energy_market.py`：独立`market-refresh`容器，开机随平台启动；不依赖浏览器，也不受旧回填RQ队列阻塞。每流Redis租约、PostgreSQL Setting检查点、指数退避（盘中普通错误最多300秒，供应商Retry-After更长时照常遵守）和成功/源时间分别持久化。Yahoo与已有NVDA路径共用yahoo_chart配额锁；长冷却让出当前周期，不阻塞其他流。
- 延用Parquet不可变分段、DuckDB读取、PostgreSQL元数据、Redis页面快照。原始证据在私有`energy-public-raw/`，不进入Git；新增参考不会修改旧CLUSDT历史SHA。
- 页面5秒读取缓存不等于5秒获得上游行情；能源API不发起上游请求。缓存到期或失败保留磁盘历史，明确标旧。手动补齐仍是既有认证/CSRF任务入口。
- Yahoo末尾非15分钟网格的tick报价不作为K线；保守等待周期结束后30分钟才写入已确认区，其他仅临时展示。parser v2会丢弃并计数OHLC矛盾的未确认K线（实际发现NG当天open低于low），不修改价格；已确认历史有同类错误仍整页拒绝，保留旧缓存。解析器升级只触发一次旧校验失败重试，429不被绕过。1d使用供应商原生开盘标签加24小时保守边界，尚非核验的期货交易日历；连续代码换月、缺量和停盘可能形成缺口。不得据此声称已完成点时训练数据或撮合验收。
- 日度EIA参考可有负值，无成交量，不转换为OHLC。Yahoo原油负价只在精确登记的能源参考存储边界允许，不改变加密资产正价校验。

## 未完成边界

免费源没有交易所级SLA；盘中能源暂无经过同产品身份核验的第二来源，失败时只能明确降级至旧缓存。EIA是独立现货参考，不是期货盘中备用价。完整交易日历、换月版本、D1 DatasetVersion接入、回测/交易准入仍待后续验收。其他股票/ETF/指数来源暂沿用现有配置，本单元没有声称全目录已经持续同步。

启动：`bash scripts/platform.sh up`；状态：`bash scripts/platform.sh status`；采集日志：`bash scripts/platform.sh logs market-refresh`。备份停写和恢复流程须包括market-refresh。验收应检查实际最新行情日期、参考源SHA、旧合约SHA未改变、后台至少两次成功周期和API健康，而非仅检查HTTP 200。

## 能源诊断分层（2026-10-25）

`services/energy_health.py`只在读缓存时计算状态，不请求上游、不写行情/模型特征。`/api/market/live`对Yahoo能源增加`health`：采集状态（等待/成功/失败/超期）、缓存状态（存在/过期/缺失）、源报价状态（延迟参考/偏旧/缺失/时间异常）、上次成功/下次计划和常规时段。旧`stale`保留，周末也不把旧报价标新。前端页面请求失败时撤下当前“正常”徽标并注明保留上次状态，避免缓存成功状态掩盖页面断连。

常规时间参考[CME WTI说明](https://www.cmegroup.com/education/courses/event-contracts-underlying-markets/wti-overview)和[CME能源交易时段说明（含Henry Hub）](https://www.cmegroup.com/articles/whitepapers/leveraging-cme-group-asia-trading-hours-liquidity-for-cross-commodity-trading.html)：芝加哥时间周日17:00至周五16:00，工作日16:00–17:00维护。诊断版本`nymex_weekly_reference_20261025_v1`使用IANA America/Chicago处理夏令时；只显示常规时段，不保证Yahoo持续合约当时交易或报价交付。节假日/半日/临时停市未核验，常规交易窗口不标记为“已开市”；恢复时间只是常规计划。不得用于历史聚合、known-at特征、执行许可或暂停采集，不等于A2完整交易日历。

报价偏旧阈值沿用15m的90分钟和1d的8小时；缓存期限为采集周期+120秒，下一采集计划逾期120秒另标超期。长Retry-After期限内不会误判任务超期。休市前报价已经超过对应阈值时单独提示缺口。HTTP失败只输出固定中文类别或状态码，不暴露异常原文。能源面板同时保留研究/延迟限定，故障与常规休市独立显示。

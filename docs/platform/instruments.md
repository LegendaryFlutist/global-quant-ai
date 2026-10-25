# 产品规格与来源代码：A1首个验证单元

本单元新增可查询的历史规则，不是全球证券撮合或行情扩容完成。原有88项身份、人工源绑定、行情指针和旧研究报告保留，新增`xtks:1321`和`xetr:EXW1`两个独立ETF身份；目录90项不代表90项有行情。

## 身份和证据

- `xtks:1321`：东京上市NEXT FUNDS日经225 ETF，JPY，ISIN JP3027650005。按[JPX 2026-09-15公告](https://www.jpx.co.jp/english/news/1030/20260915-01.html)，2026-10-05起交易单位从1变10，价位类别从One unit ETF表变为TOPIX 500表。旧规格仅登记自公告日起至变更前，不推断2001年上市以来始终如此。
- [发行人1321产品资料](https://nextfunds.jp/uk/lineup/1321/)用于身份/上市信息核验。交易规则变更日和2026-10-07份额拆分生效日不同；本单元不实现公司行为复权、份额变化或分红回测。价位表只保存名称，价格分档算法未核验，`tick_size=null`。
- `xetr:EXW1`：[iShares挂牌资料](https://www.ishares.com/nl/professionele-belegger/nl/producten/251783/ishares-euro-stoxx-50-ucits-etf-de-fund?siteEntryPassthrough=true&switchLocale=y)确认Deutsche Boerse Xetra、EXW1、EUR、ISIN DE0005933956及挂牌日期。只登记本次2026-10-22核验日起的身份/币种覆盖，不用挂牌日期回填交易规则。其他场所或其他币种的同基金挂牌不能合并。

证据及适用范围保存在`config/platform/instrument_timelines.json`，2026-10-22核验。这里保存链接和人工整理的事实，不宣称已归档原始供应商网页。JPX/iShares reference供应商只用于代码/证据登记，没有行情适配器、授权实时feed或自动采集任务。

## 时间与版本语义

1. `effective_from <= 规则时点 < effective_to`，结束为空表示未指定，输入必须带时区，持久化UTC。日本变更边界是日本本地2026-10-05零点，即UTC前一天15点，不是UTC10月5日零点。
2. `recorded_at`在本机导入时由服务生成；来源公布日期、核验日期不能代替本机真正收到记录的时间。查询先取`recorded_at <= known_at`的最新完整版本，再在该版本内选有效期。不用后来收到的规则伪装历史当时可知。
3. 一个版本包含完整规格和别名时间线。更正须用`publish_timeline(db, payload)`显式导入新完整版本并提交事务；旧版本保留。新版本出现空档不回退旧版本。相同内容重复导入幂等，不提升旧版本为最新；恢复旧内容时也要以新的更正说明形成可审查版本。
4. 同版本规格不能重叠；同提供方只允许一个有效代码。别名是字符串，保留前导零。跨资产同源同代码若在查询时点存在冲突，解析返回409，不能凭排序挑一个。旧`asset_aliases`不改写，新路径不接管旧行情解析。
5. `currency/quote/base/settlement_currency`分别表达；数量、价位、乘数、最低名义额用NUMERIC(38,18)存储，API用字符串。未知保持null；购入后卖出限制、现金交收、证券交收、卖空和保证金是独立字段，未核验时UNKNOWN，不能自动填T+1或杠杆。

版本ID是规范完整内容的SHA256。父记录和子记录都拒绝ORM/直接SQL更新删除；读路径还验证所有子记录重组后的哈希，缺失/额外插入/内容变化均拒绝返回。发布由单事务及产品级advisory lock保护，无外部写入API；不宣称数据库管理员或持有数据库凭据者不能破坏记录。

## 页面与API

登录 → 资产 → 搜索1321/EXW1 → **规格历史**。默认查看现在规则；可输入规则时点和系统已知截止，控件明确使用UTC，显示按用户时区设置转换。展开版本查看有效范围、别名、身份、证据和未实现日历。老资产尚无时间线时明确提示，不把旧specification JSON当作完整历史。

- `GET /api/instruments/specification?asset_id=xtks:1321&effective_at=2026-10-04T15:00:00Z`
- `GET /api/instruments/history?asset_id=xtks:1321`
- `GET /api/instruments/resolve-alias?provider_id=jpx_reference&symbol=1321`

全部需已登录会话，只有GET；不能通过页面改写规格或获得交易权限。返回`research_only=true`、`execution_ready=false`、`live_tradable=false`。源身份核验不等于行情就绪，也不等于完整交易规则已经核验。

## 兼容、测试与剩余工作

迁移0005只新增三张表，0001–0004保持不变。启动只为没有版本的产品导入已审核种子；已有人工Asset字段、源绑定和新版本都不覆盖。测试容器挂载当前迁移/配置，避免镜像旧迁移造成假通过；只在quant_test/redis-test执行。

验收覆盖1321精确边界、实际known-at、修订与旧版本重现、UNKNOWN空档、别名期限/前导零/冲突、人工修改保护、38位Decimal、哈希/ORM/SQL不变性、迁移、认证和无写入口。实际结果与部署记录见PROJECT_PROGRESS.md；fixture更正规则仅用于隔离测试，不写生产。

完整A1仍未完成：中美日韩欧指数/ETF/股票小池及更多规格逐项核验、实际数据供应商历史别名、listing/delisting/结算/卖空完整证据均待后续。A2实现交易日历与午休/半日/周月聚合；D4再处理公司行为。此处XTKS/XETR日历仅识别与PLANNED标记，不把时区存在当作日历实现。

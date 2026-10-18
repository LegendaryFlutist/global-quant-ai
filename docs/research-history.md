# 多模型合约量化研究工程

这是独立的新工程，不覆盖之前的交易机器人。用途是研究、比较和淘汰策略，**不接账户、不读取密钥、不发真实或模拟盘订单**。无法承诺盈利，更不是快速致富产品。

## 直接启动（本机环境已配置）

在 VS Code 打开 `run.py`，点击运行，或在已激活虚拟环境的终端执行：

```bash
python run.py
```

直接运行 `python lab.py` 也会自动转入同一个启动器，不再报缺少 command。
默认顺序运行 BTC、ETH 的最新12类反转形态研究，入场周期15m/30m/1h/2h/4h、6种退出、5倍逐仓、10%保证金，邮件仅模拟记录。默认信号响应延迟15分钟、常规持仓4小时至7天；报告包含持仓时长和分批减仓。
使用项目已有历史行情，启动时显示实际数据日期；不会自动下载最新行情。每次自动创建 `runs/run_时间戳_随机后缀/`，完成后在终端给出 `index.html` 汇总路径，不覆盖已有研究。

VS Code 已配置项目 `.venv/bin/python` 解释器。按 F5 可选择“运行回测：BTC + ETH”或“运行回测：仅 BTC”。
下面两条命令分别用于启动检查和仅跑 BTC：

```bash
.venv/bin/python run.py --check
.venv/bin/python run.py --symbols BTCUSDT
```

原 `lab.py patterns ...`、`lab.py research ...` 等显式子命令继续可用；需要自选数据目录、周期或手续费时使用对应参数。

## 已实现

- 公开 USDⓈ-M 15 分钟历史 K 线与资金费率下载，支持 BTCUSDT、ETHUSDT 分别研究。
- 5 个模型：逻辑回归、随机森林、ExtraTrees、HistGradientBoosting、小型 MLP。
- 3 个规则对照：EMA 趋势、短期均值回归、道氏 123；另列空仓和永续多头基准。
- 前 80% 数据中做 3 折扩展窗口验证，边界剔除 2 根，末尾 20% 留给最终候选。
- 仅在验证集比较模型和 0.55/0.60 信号阈值；按验证折净收益中位数排序。
- 标签和回测都对应 close[t] 出信号、open[t+1] 成交、open[t+2] 估值。
- 同方向信号维持固定合约数量；换方向/退出时再交易；计入首次建仓、反手、最终平仓成本。
- 历史资金费率乘结算标记价格估算现金流，手续费、滑点可配置，额外做双倍费用压力测试。
- 输出排名、各验证折指标、最终候选测试指标、资金曲线及研究模型；没有合格候选则输出 NO_CANDIDATE。

## 快速开始（Python 3.11 或 3.12）

```bash
unzip quant_research_lab.zip
cd quant_lab
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
python -m unittest discover -s tests -v

# 不需要任何 API Key。需要当地及账户允许访问的网络环境。
python lab.py download --symbol BTCUSDT --days 730 --out data/btc_2y
python lab.py research --data data/btc_2y --out runs/btc_first

python lab.py download --symbol ETHUSDT --days 730 --out data/eth_2y
python lab.py research --data data/eth_2y --out runs/eth_first
```

下载与输出目录必须不存在，避免覆盖实验。下载失败可保留失败目录排查，然后使用新的目录重新下载。HTTP 451/403 应检查服务的地区可用性；程序不会绕过限制，也不会自动用其他市场数据替代。

默认单边手续费 0.05%、滑点 0.02%、建仓名义价值为策略净值 20%，只是研究假设，必须按实际费率和成交环境调整。不是账户仓位建议。回测无杠杆，参数 exposure 不允许大于 1。

```bash
python lab.py research --data data/btc_2y --out runs/btc_cost_check \
  --fee 0.0005 --slippage 0.0002 --exposure 0.2
```

注意：更改参数反复观察同一测试集，就不再是独立样本外检验。需要另外留存之后的新数据作前向验证。BTC/ETH 分别比较后再挑赢家，同样增加选择偏差。

## 结果怎么看

| 文件/字段 | 含义 |
|---|---|
| ranking.csv | 验证集排名，不是实盘收益排名 |
| report.json / ranking[].folds | 每折净收益、最大回撤、开仓次数、近似夏普 |
| selected | 验证阶段选出的候选；可能为 null |
| holdout | 只评估这个候选的预留测试集，不在测试集重新挑模型 |
| holdout_double_cost | 手续费与滑点翻倍的压力测试，资金费率保持历史值 |
| holdout_equity.csv | 候选存在时的测试集信号和净值路径 |
| research_model.joblib | 冻结的研究模型，仅加载自己生成且可信的文件 |
| baseline_long_perpetual | 同等初始敞口的永续多头，含费用和资金费率，并非现货买入持有 |

示例：net_return=0.08 表示所示阶段策略权益 +8%；max_drawdown=0.05 表示 5% 最大回撤。各阶段长度不同，不直接拿累计收益互比。夏普按 15 分钟频率年化，未校正自相关，因此只作描述，不是可靠的显著性证据。

筛选门槛为示范：3 折中至少 2 折盈利、每折回撤不超过 10%、合计至少 30 次建仓、折收益中位数为正。最终测试须盈利、回撤不超过 10%、至少 20 次建仓且双倍费用仍盈利。

- NO_CANDIDATE：验证阶段没有合格候选。应保留结果，不降低门槛凑赢家。
- REJECTED：测试不合格，或输入明确为合成数据。
- FORWARD_PAPER_REVIEW：仅表示可进一步审查并做前向纸面验证，**绝不是实盘放行**。

## 无网络自测

```bash
python lab.py demo --out data/demo_new
python lab.py research --data data/demo_new --out runs/demo_new
```

本次 9 项单元测试通过。小样本模型接口测试中 MLP 出现未收敛警告，意味着该次拟合迭代未充分，不影响接口测试通过，也不能据此宣称模型有效。

附带 example_data 和 example_report 是固定种子生成的合成行情及真实运行输出，只用于验证代码路径，不能说明任何币种或模型的赚钱能力。本次环境查询 Binance 行情返回 HTTP 451，未完成真实市场训练或回测，不提供虚构收益榜。

## 数据契约

下载生成 bars.csv（open_time、close_time 为 UTC 毫秒，open/high/low/close/volume），funding.csv（fundingTime/fundingRate/markPrice）及 source.json。可使用可信的同格式历史数据；source.json 至少给出 symbol 与 synthetic 布尔值。系统记录数据 SHA256，检查 15 分钟间隔、OHLC、未收盘数据、重复资金费率及超过 9 小时的资金费率数据缺口。9 小时检查是 BTC/ETH 常见间隔的粗检，其他品种/历史制度需另审，不证明数据完全无缺漏。

资金费率恰好落在开盘时间时归属调仓前的仓位；实际交易所结算和下单延迟可能不同。历史资金费率仅用于收益核算，不进入预测特征。

## 为什么不能只看历史收益最高

比较模型越多，越容易挑到偶然适合历史噪声的模型。更高的杠杆、忽略资金费率、同根收盘成交、反复使用测试集都可能制造好看但不可实现的收益。这个版本限制选择过程，但没有消除多重检验和市场变化风险。

原 research 引擎没有模拟强平、盘口冲击、部分成交、断网、盘中止损，也没有独立账户风控或交易所保护单；新增 leveraged 命令仅实现文末说明的近似逐仓强平。20% 敞口及回撤门槛不是实时止损。不能把它的净值当作实盘承诺。多个品种的报告不能简单相加成组合收益。

通过验证后的合理下一步是冻结模型和参数，用未来新到的数据做纸面逐笔前向记录，并检查信号、费用、回撤和稳定性。此工程不自动晋升模型、不自动操作账户。长期运行、订单对账、热加载、告警等属于另外的执行系统范围，未包含在本交付中。

## 修改模型

在 lab.py 的 models() 中添加遵循 fit/predict_proba 接口的分类器。所有变换必须放在训练用 Pipeline 中。特征写在 features.py 的 make_features，只使用当时及过去数据。调整策略的同时记录实验，不能用已看过的测试集再次宣称独立验证。

## 官方接口来源

https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data

使用公开 GET /fapi/v1/klines 与 GET /fapi/v1/fundingRate。接口可用性与规则以币安最新官方说明为准。

## 特殊关注、道氏 123 与多周期趋势线

`watchlist.json` 保存 BTCUSDT、ETHUSDT，以及特殊关注的美股 ETF：

- **KORU**：韩国 MSCI Korea 25/50 指数每日 3 倍做多，不等同于 KOSPI 指数本身。[发行商](https://www.direxion.com/product/daily-msci-south-korea-bull-3x-etf)
- **QID**：纳斯达克 100 指数每日 2 倍做空。[发行商](https://www.proshares.com/our-etfs/leveraged-and-inverse/qid)

倍数是每日目标，不是长期累计收益倍数。策略分析的是 ETF 自身价格；QID 的看涨信号表示产品价格看涨。关注清单不表示币安可交易这些产品。

```bash
python lab.py watchlist
# 已有的币安 15 分钟行情：中周期 4h、大周期日线/周线
python lab.py analyze --symbol BTCUSDT --bars example_data/bars.csv --out runs/btc_trends
# 独立数据源导出的美股日线：日线/周线/月线
python lab.py analyze --symbol KORU --bars data/koru/bars.csv --out runs/koru_trends
python lab.py analyze --symbol QID --bars data/qid/bars.csv --out runs/qid_trends
# 可指定周期、拐点左右确认宽度（默认为 2）
python lab.py analyze --symbol ETHUSDT --bars data/eth_2y/bars.csv --out runs/eth_trends --timeframes 4h 1d 1w --pivot-width 3
```

打开输出的 `trends.html` 查看收盘价格、支撑线、压力线和反转信号；每周期 CSV 给出逐根趋势线、锚点、123 阶段、事件及持仓信号，`report.json` 给出摘要。图中趋势线连接最近两个已确认低点/高点并延长，可能已经破位，不能一概当作有效支撑/压力；没有足够拐点时标为 INSUFFICIENT_PIVOTS，不编造趋势线。

附带 `example_trends/trends.html` 是基于合成行情生成的功能示例；约 52 天历史不足以确认周线趋势。此次更新通过全部 16 项单元测试，并完成包含 dow123 的合成数据研究流程验证；这不代表真实市场策略表现。

ETF 行情需自行提供可信日线 CSV，目前没有自动下载 ETF 行情。字段为 `open_time,close_time,open,high,low,close,volume`；时间为 UTC 毫秒，每个交易日用 UTC 零点标记 session 日期，close_time 为次日 UTC 零点减 1 毫秒。使用同一复权口径的全部 OHLC（特别留意拆股），不要只复权 close。程序不填充周末和休市日，也不能仅凭缺失行区分休市与数据缺漏。ETF 数据不进入原有 15 分钟连续币安永续资金费率回测。

聚合按 UTC 日历，周线从周一开始；只纳入数据覆盖完整的周期，首尾不完整周期排除。ETF 采用保守覆盖边界，最后一周/月可能需要后续交易日数据才会纳入。短历史无法形成周线/月线趋势时应补充更长历史。示例行情仍为合成数据。

`dow123` 已作为第三个规则策略加入 `research` 的同一验证、筛选和成本压力测试流程。规则采用严格顺序：上升支撑线被收盘价跌破 → 破位后确认更低高点 → 收盘价跌破破位时冻结的最近回调低点，输出 -1；下跌反转完全对称，输出 +1。候选若创新的原趋势极值则失效；未满足三步时不触发。参考 [OANDA 的 123 法则说明](https://www.oanda.com/bvi-ft/lab-education/technical_analysis/123faze-1/)，这是固定参数的程序化实现，并非唯一画线方式。

宽度为 2 的拐点需等待右侧 2 根收盘才确认，历史信号不会回填到拐点当天；突破时冻结触发位，防止后续画线移动触发位。确认后的方向保持到相反 123 信号出现，未实现盘中止损。研究中的 123 在输入 15 分钟周期计算；当前版本所有模型和规则策略统一接受已收盘 1 小时 EMA12/48 方向过滤，15 分钟信号只顺小时趋势，周期比为 1:4。多周期趋势线仍分别绘制。新策略增加了比较次数，需要新的前向样本继续验证。

## 邮件通知与手动操作

支持运行报告邮件，以及冻结模型的最新 15 分钟前向观察信号邮件，全部由你决定是否手动操作。收件人模板已填写为 `research@example.invalid`，发信账号也暂按此邮箱配置。公开币安行情不需要 API 密钥，这个版本没有账户访问或订单接口，也不保存聊天中提供的密钥。

先在 163 邮箱开启 SMTP 并生成客户端授权码，在本地配置，不要把授权码贴到聊天中：

```bash
cp -n .env.example .env
chmod 600 .env
# 用本地编辑器填写 .env 的 QUANT_SMTP_PASSWORD（邮箱客户端授权码）
# .env 按 shell 语法填写；值包含特殊字符时用单引号包住
set -a
source .env
set +a
.venv/bin/python lab.py mail-test
```

`.env` 已忽略提交。只使用 SSL 或 STARTTLS，证书验证开启；不支持明文 SMTP。配置未完成会明确报错，不会标记邮件已发送。`SMTP_ACCEPTED` 只表示 SMTP 接受邮件，不保证到达收件箱，请检查收件箱/垃圾箱。

研究或趋势分析结束后发送报告（包括 NO_CANDIDATE / REJECTED，邮件明确标为研究报告，不是历史信号重放）：

```bash
.venv/bin/python lab.py research --data data/btc_2y --out runs/btc_mail --email
.venv/bin/python lab.py analyze --symbol BTCUSDT --bars data/btc_2y/bars.csv --out runs/btc_trends_mail --email
# 报告已生成但发信失败时，单独重试，不必重新跑研究
.venv/bin/python lab.py notify-report --report runs/btc_mail/report.json
```

冻结模型生成即时观察信号：

```bash
# 仅加载自己生成且可信的 joblib。fresh_btc 包含最新完整 bars.csv、source.json
# 先预览，不发邮件
.venv/bin/python lab.py signal --model runs/btc_mail/research_model.joblib \
  --data data/fresh_btc --out runs/btc_signal_preview
# 实际发送；每次运行使用新的输出目录
.venv/bin/python lab.py signal --model runs/btc_mail/research_model.joblib \
  --data data/fresh_btc --out runs/btc_signal_send --email
```

前向信号读取最新已收盘 K 线，包含方向、模型、参考价、上海时间及 1h/4h/日线趋势背景，不使用回测中需要未来两根行情的标签计算。只允许本次版本生成、策略版本为 trend_1h_entry_15m_v1、状态为 FORWARD_PAPER_REVIEW 的真实数据模型；合成模型、REJECTED、旧版缺少审核状态的模型、品种不符、缺失 K 线、超过 15 分钟的新鲜度窗口，都会拒绝发送即时观察信号。ETF 仍使用独立日线分析入口，可发分析报告，不支持这个币安 15 分钟模型入口。前向纸面审查状态并不表示模型已适合实盘。

首次运行发送当前方向，之后同一模型、同一邮箱只在方向改变时发送（含回到观望）；同根重复运行、乱序旧数据及方向不变会跳过。状态保存在 `runs/email_state`，请保留并让重复运行共享此目录。模型文件更新会视为新模型。发信失败不提交去重状态，可以重试；SMTP 接受邮件后进程若在状态落盘前崩溃，重试仍有小概率重复邮件。

当前命令是单次运行，不会自行开启后台任务、刷新行情或定时发送。需由持续运行的机器或任务调度器定期刷新真实行情后执行 `signal --email`。项目尚未配置真实 SMTP 授权码，也未连接账号或发送实际邮件；邮件测试用替身 SMTP 验证，不宣称真实投递成功。

## 短线周期与全部模型收益曲线（当前运行版本）

当前默认 **1小时定方向、15分钟找入场，比例4:1**。大周期使用已收盘小时 K 线的 EMA12/48：快线高于慢线仅允许小周期做多，低于慢线仅允许做空，其余观望。小时线需至少 48 根完成热身；未完成的小时线不可提前参与方向计算。5 个机器学习模型和 EMA、均值回归、道氏123 共8个策略都使用同一过滤规则。小时趋势不一致时会退出到观望，仍按下一根15分钟开盘成交的原回测约定计算。此版本固定这一周期配对，没有将其他配对也宣称为已测试。

每次 `research` 自动生成：

| 文件 | 内容 |
|---|---|
| comparison.html | 全部模型比较入口 |
| model_comparison.png / .svg | 8个策略及空仓、永续多头基准的叠加收益曲线 |
| individual_models.png / .svg | 每个策略单独的收益曲线，便于查看交易频率及回撤 |
| validation_equity.csv | 逐时间点的费用后验证净值（初始值1） |
| model_comparison.csv | 每个模型阈值、验证收益、回撤、开仓次数及筛选结果 |
| report_email_preview.txt | 本次研究报告的邮件文本预览 |

这些曲线使用前80%数据中的3折扩展窗口验证，折内信号由此前数据训练得到；折间平仓后按净值复合，剔除边界的两根行情。每模型的阈值在验证集选择，仍存在选择偏差。**末尾20%测试集只评估验证阶段选出的候选，不把所有模型在测试集上重排**；如果你按已经展示过的曲线重新改策略，需要留新的未来行情验证。

候选继续按“验证折收益中位数、至少2折盈利、回撤与交易次数门槛”筛选，不能把零交易的0%收益模型称作最好。没有合格候选时输出 NO_CANDIDATE，不生成可供前向通知运行的模型；不会为了启动而自动降低门槛。

### 已实际运行的结果

本地直连币安 API 返回 HTTP 451；本次通过已连接的 Binance 工具读取 BTCUSDT、ETHUSDT 各90天行情，每个品种8640根15分钟K线、270条资金费率记录，已通过工程的数据校验。数据保存在 `data/btcusdt_90d_connector`、`data/ethusdt_90d_connector`，来源与时间范围见各自 `source.json`，没有使用账户密钥。

运行命令如下，可用新的输出目录复现：

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python lab.py research \
  --data data/btcusdt_90d_connector --out runs/btc_reproduce
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python lab.py research \
  --data data/ethusdt_90d_connector --out runs/eth_reproduce
```

本次查看入口为 `runs/comparison_1h_15m_20261017/index.html`，两个品种均为 NO_CANDIDATE，未启动前向模型通知后台任务。数据范围是 UTC 2026-07-19 15:45 至 2026-10-17 15:44:59.999；曲线只覆盖其中验证期，不是全90天拟合收益或样本外测试收益。KORU/QID 没有独立日线数据，本次没有纳入币安短线模型比较。

### 启用真实邮件

已在项目本地创建权限600的 `.env` 模板，收件人及默认发件人为 `research@example.invalid`；程序启动会自动读取其中的邮件配置，不执行文件中的 shell 命令。你只需在本地编辑器填 `QUANT_SMTP_PASSWORD`（163客户端授权码），无需在聊天中发送。已有环境变量优先。然后运行：

```bash
.venv/bin/python lab.py mail-test
.venv/bin/python lab.py notify-report --report runs/btcusdt_1h_15m_20261017/report.json
.venv/bin/python lab.py notify-report --report runs/ethusdt_1h_15m_20261017/report.json
```

本次已尝试发送报告，程序因缺少SMTP授权码明确拒绝发送。报告预览保留在各输出目录，没有声称实际送达。未来得到通过审查的模型后，可使用 `signal --email` 发送顺小时趋势的15分钟观察信号；当前没有持续后台进程，外部行情连接器也不会自动变成本机定时下载器。本地直接API仍为451，连续刷新还需要一个可访问的行情运行环境。

## 逐仓合约杠杆回放与模拟邮件（2026-10-18）

按当前偏好，项目本地 `.env` 已设 `QUANT_MAIL_MODE=simulate`。`research/analyze --email`、`signal --email` 和 `notify-report` 通过通知入口时只写本地模拟邮箱，不调用 SMTP，不要求授权码。记录状态统一为 `SIMULATED_NOT_SENT`，模拟状态与真实投递状态分开保存；`mail-test` 在此模式拒绝真实发信。以后需实际发送时，在本地填好授权码并将模式切换为 `real`。

新增 `leveraged` 命令在同一份验证行情上重放各模型，默认逐仓 3/5/10 倍，每笔保证金为当前账户权益10%。仓位公式：**开仓名义价值 = 账户权益 × 保证金比例 × 杠杆倍数**。因此这些组合的初始名义敞口分别为账户30%、50%、100%，收益图按账户权益计算，不是单笔保证金收益率。原 `research --exposure` 仍表示名义敞口，原报告不会被回写成杠杆报告。

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python lab.py leveraged \
  --data data/btcusdt_90d_connector \
  --baseline-report runs/btcusdt_1h_15m_20261017/report.json \
  --out runs/btc_leverage_new --leverages 3 5 10 --margin-fraction .1
```

`mark_bars.csv` 是与交易K线逐时间匹配的币安标记价格行情。本次已通过 Binance 连接器补齐 BTC/ETH 各8640根15分钟标记价格K线。模拟器维持固定合约数量，按名义价值计算手续费和资金费率，价格滑点体现在成交价；不自动追加逐仓保证金。已有仓位在开盘遇标记价格跳空时，先检查强平，再处理主动调仓；同根价格恢复也不能抹去盘中触发的强平。强平后该方向持续信号被阻止重新开仓，直到信号方向发生改变。

维持保证金率默认1%、维持保证金速算额0、强平清算费1%，均为可配置研究假设，**未匹配真实币安档位，不是交易所精确强平复刻**。15分钟K线无法还原盘中高低点及资金费率事件顺序：资金费率先计入风险余额，压力回撤按有利极值后不利极值计算；强平时间只能记录到近似K线边界。未实现分档/部分强平、ADL、自动追加保证金和保险基金细节。[币安标记价格与强平说明](https://www.binance.com/en/support/faq/detail/360033525271)

阈值沿用先前报告中各模型选出的验证阈值，扩展窗口逐折重新训练，不用杠杆回放结果重新挑阈值。末尾20%预留数据未参与。本次历史已展示过，因此杠杆比较仅为敏感性回放，不能称为新独立样本或前向验证。逐仓资金归一为初始账户权益1，`trades.csv` 的数量和费用也按这个资金单位缩放；它们不是你真实账户的下单数量。

本次实际查看入口为 `runs/leverage_comparison_20261018/index.html`。BTC/ETH 各27440条逐根历史决策，共54880条；模拟回放邮箱分别785/1274封，包括方向变化、折边界平仓及回放汇总。零交易的MLP收益为0，其他有交易模型在三组杠杆下累计收益均为负。本次未发现近似强平事件，不代表实际杠杆安全，也不证明其他期间不会强平。

| 输出 | 内容 |
|---|---|
| decisions.csv | 每模型每根验证K线的方向、小时趋势、参考收盘价、动作、模拟状态 |
| simulated_outbox/*.json / *.eml | 去重后的模拟邮件正文、收件人、历史决策时间与实际记录时间；从未调用SMTP |
| sample_simulated_email.txt | 一封可直接查看的模拟决策邮件 |
| trades.csv | 各杠杆情景的模拟成交、手续费、逐仓保证金及近似强平事件 |
| leverage_comparison.csv / validation_equity.csv | 场景指标和账户净值 |
| leverage_comparison.png / .svg、comparison.html | 杠杆收益曲线和指标表 |

当前完成的是历史回放，未启动连续后台采集/模型运行。真实前向模型通知仍遵守原来的审核和行情新鲜度要求，不能把本次回放模型直接标为可用实盘模型。

## 12类反转形态与多止盈实验（当前新增版本）

新增 `patterns` 研究入口：把5个机器学习模型改为使用12类反转形态、ATR、RSI及大周期趋势特征，另设纯形态基准。这里的12类是可选形态集合，不要求互相矛盾的12种形态同时出现；至少一种同方向形态触发，且票数方向与已收盘大周期EMA方向一致，才允许入场；当前版本在信号收盘确认后延迟15分钟，用实际15m开盘价格回放。形态票数会重叠，不等于独立概率。

形态来自 [TA-Lib官方目录](https://ta-lib.org/functions/) 与 [StockCharts反转形态定义](https://chartschool.stockcharts.com/table-of-contents/chart-analysis/candlestick-charts/candlestick-bullish-reversal-patterns)，采用明确的固定几何阈值实现，不宣称与TA-Lib输出逐项相同：

| 类别 | 双向反转形态 |
|---|---|
| 1 | 锤头 / 上吊线 |
| 2 | 倒锤头 / 流星线 |
| 3 | 看涨 / 看跌吞没 |
| 4 | 看涨 / 看跌孕线 |
| 5 | 刺透 / 乌云盖顶 |
| 6 | 早晨 / 黄昏之星 |
| 7 | 十字星突破确认 |
| 8 | 平头底 / 平头顶 |
| 9 | 红三兵 / 黑三鸦 |
| 10 | 三内升 / 三内降 |
| 11 | 三外升 / 三外降 |
| 12 | 看涨 / 看跌弃婴 |

每种形态都检查形成之前的5根局部趋势。刺透和星线对24小时连续市场采用前序平均振幅10%的开盘容差；弃婴保留严格缺口，因此币安上可能很少出现。定义、来源和实际各周期检测数量保存在报告及 `pattern_counts.csv`，未检测到的形态不会为了凑数而制造信号。

| 小周期入场 | 大周期过滤 |
|---|---|
| 15分钟 | 1小时 |
| 30分钟 | 2小时 |
| 1小时 | 4小时 |
| 2小时 | 8小时聚合线 |
| 4小时 | 16小时聚合线 |

保持此前1:4要求；所有入场周期位于15分钟至4小时。8h/16h线由已完成15m行情按UTC固定时间桶聚合，不读取未收盘的大周期。EMA12/48需48根大周期热身，90天数据使4h入场第一折可训练样本不足；这类配置即使有正收益，也不能越过完整3折的门槛。

### 模型与标签修改

逻辑回归、随机森林、ExtraTrees、HistGradient使用类别平衡；MLP使用平衡样本权重。除了原特征，还加入12个有方向的形态列、看涨/看跌数量、ATR比例、RSI及已收盘大周期EMA差。当前v2标签为：**信号确认后等待15分钟，以实际15m开盘入场，持有12小时至该段最后一根15m收盘**的收益（旧v1为次根开盘至未来第4根收盘），越过 `2×(手续费+滑点)+0.25×ATR/close` 才标记上行或下行，其他为观望。概率门槛固定0.45，所选方向必须胜过观望与反向类别，并与形态及大周期一致。

全部特征严格只用当根及以前数据；未来标签与特征完全分开。训练样本要求 `label_end_time < 验证起点`。周期、模型和退出方式会增加比较次数；这些修改不能消除过拟合，也不能保证收益。部分MLP折达到600次迭代仍未收敛，已在 `fit_warnings` 记录，不能据其少量交易的正收益称为稳定模型。

### 6种止盈/退出模式

所有模式都保留入场时已知风险ATR的1.5倍保护止损；v2风险ATR取信号周期ATR与已收盘4小时ATR的较大值，避免用15m噪声尺度支撑4小时以上持仓，ATR参考 [Fidelity的ATR说明](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/atr)。0形态表示不新开仓，不会因下一根没有新形态就立即平仓；明确反向形态或已收盘大周期反向，会等待最短持仓时间后在可用15m开盘退出，同根不反手。

| 模式 | 做多定义（做空对称） |
|---|---|
| fixed_rr | 初始止损距离为1R，达到2R目标止盈 |
| atr_trailing | 以已收盘最高价减2ATR更新下一根的跟踪止损 |
| three_bar_breakout | 价格突破信号周期此前三根已收盘K线的最高价，且扣费后净盈利才止盈；这是你提供规则的默认解释 |
| three_bar_trailing | 盈利后跌破此前三根已收盘K线最低价，跟踪止盈 |
| time_exit | 按实际时间持仓上限退出，当前默认168小时（7天） |
| fixed_three_bar_target | 入场前已知三根K线最高价冻结为目标，不能事后移动 |

三根K线不包含正在执行的这一根，止盈必须覆盖实际进出滑点、手续费和已入账资金费。若目标已经低于入场价，不会虚构“盈利成交”；跳空按实际开盘可成交价格处理。同根同时碰止损与目标，止损优先；强平优先于二者。无法从OHLC还原盘中先后，因此标记为近似执行，而不是逐笔撮合复刻。资金费的精确事件时间被记录，但同根资金与盘中退出先后仍用资金先入账的约定；若本应早退出，负资金费收入可能被高估。

### 运行与已产出结果

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python lab.py patterns \
  --data data/btcusdt_90d_connector \
  --baseline-report runs/btcusdt_1h_15m_20261017/report.json \
  --out runs/btc_patterns_new \
  --timeframes 15m 30m 1h 2h 4h \
  --leverage 5 --margin-fraction .1
```

本次以逐仓5倍、10%保证金、单边手续费0.05%、滑点0.02%、固定维持保证金率假设1%实际运行。BTC/ETH各比较180组，共360组。所有周期用同一组验证日期，旧报告的预留边界 `1790703900000` 不变；仅当验证选出合格候选时，才能对单一候选做一次保留测试及费用压力测试，其他候选不会看到测试集。

查看入口：`runs/patterns_comparison_v1_20261018/index.html`。BTC11组、ETH21组验证累计收益为正；但交易次数、三折稳定性或完整训练折数等仍未达标，均为 NO_CANDIDATE，真实数据的预留测试未使用，也没有可启动的前向模型。少量交易的正收益不应当作最佳模型证明。

| 文件 | 含义 |
|---|---|
| models_comparison.png/.svg | 每周期比较6个模型；每模型退出方式按验证折中位数选择 |
| exits_comparison.png/.svg | 使用完全相同纯形态入场，比较6种退出效果 |
| ranking.csv / profitable_validation_configs.csv | 全部组合及累计盈利组合；包含未通过原因，不等于实盘排名 |
| validation_curves.json | 每个组合的净值，按折平仓后复合 |
| decisions.csv | ENTRY_CANDIDATE：逐根模型入场候选，不等于实际成交或持仓动作 |
| trades.csv | 每种退出执行后真正发生的模拟交易，含目标、止损、退出原因、费用、资金费和利润 |
| simulated_outbox/ | 代表配置的平仓历史回放与汇总，SIMULATED_NOT_SENT，不是真实入场时点的邮件 |

每折交易金额以该折初始权益1归一，跨折核对需用 `stitched_net_pnl`（已乘 fold_start_equity），不能直接加原始 `net_pnl`。本次曾遇到模拟邮件重复config字段的导出错误，已修复并从既有结果恢复导出，没有重算验证或重复读取保留测试。完整73项测试通过，覆盖形态前缀因果性、多周期聚合、止损/止盈冲突、资金边界、单候选测试隔离及导出恢复。

## 查看标的、操作次数和整体收益曲线

`patterns` 运行结束会额外输出 **operations.html**，并在终端打印完整路径和 `xdg-open` 打开命令。`run.py` 的汇总页也优先链接这个操作报告。

你已经完成的 BTC 运行可直接打开，无需重新回测：

```bash
xdg-open ~/Documents/quant_lab/runs/btc_patterns_manual_01/operations.html
```

旧结果只需补生成这个页面时运行：

```bash
python lab.py report --run runs/btc_patterns_manual_01
```

页面会显示本次交易标的、独立实验数量、全部实验开平仓总次数，以及下拉选中的单一模拟账户的收益曲线、开平仓次数、做多做空次数、胜率、手续费和资金费。每笔交易给出上海时间、开仓价、平仓价、退出原因、本笔账户收益和累计收益。逐笔K线内退出只能提供近似时间，页面用≈标识。

当前 `btc_patterns_manual_01`：BTCUSDT，180个独立配置合计4999笔完整交易、9998次开平仓操作。默认只观察报告排名首位的 `4h|pattern_only|fixed_rr`，7次开仓、7次平仓、净收益约0.73%；完整训练折仅2/3且交易不足30笔，因此仍未合格。可以在页面切换任意其他组合，曲线、统计和交易表同步更新。不同配置的净值各自从1起算，不把180条策略收益相加为单一账户或组合收益。

新增文件：`operations.csv`（每次开平仓单独一行、含标的与上海时间）、`trades_readable.csv`（完整交易）、`operation_summary.json`（逐配置统计）、`account_equity.csv`（各配置完整净值）、`overall_equity.png/.svg`（默认观察账户曲线）。网页无需服务器或网络即可打开；网页曲线最多显示500点，CSV保留完整数据。


## 持仓时间控制 v2（2026-10-18）

已按手工决策场景修改 `patterns` 和 `run.py` 默认策略。信号完全收盘后等待15分钟，再使用真实的原始15分钟K线开盘价加滑点成交；4h信号在12:00确认，实际最早12:15成交，不再假设瞬间响应。

- 常规退出最短持仓4小时，最长168小时（7天），按真实毫秒计算，所有信号周期共用同一时长范围。止盈、反向形态、大周期反向在最短时间后才执行；保护止损和近似强平始终有效，研究折末截尾另记例外。盘中退出仍只精确到15m K线近似收盘时间。
- 持仓满72小时且价格与入场价相差至少3倍入场风险ATR时，每隔24小时减初始数量的25%；有利和不利偏移都适用。剩余仓位继续受止损、趋势退出和7天上限控制，不补仓。规则不保证每笔都触发减仓。
- 风险ATR取当前入场周期与已收盘4h ATR的较大值；形态和ML特征仍按原来的入场周期计算。训练标签统一为延迟入场后的12h收益，训练末端按真实标签结束时间剔除，保持原测试集边界。
- 报告可切换180个独立账户，显示平均、中位、最短、最长、累计持仓时间、有仓位时间占比、固定4h～7天分布、逐笔持仓时间线及分批减仓。`holding_summary.csv`、`trades_readable.csv`、`partial_exits.csv` 可直接查看。减仓损益包含在完整交易中，不重复相加。模拟邮件也记录代表配置的减仓决策。

```bash
# 默认：4小时～7天、信号响应延迟15分钟
.venv/bin/python run.py

# 参数示例（输出目录请使用新的名字）
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python lab.py patterns \
  --data data/btcusdt_90d_connector \
  --baseline-report runs/btcusdt_1h_15m_20261017/report.json \
  --out runs/btc_holding_custom \
  --execution-delay-minutes 15 --min-holding-hours 4 --max-holding-hours 168 \
  --scale-out-after-hours 72 --scale-out-interval-hours 24 \
  --scale-out-fraction .25 --scale-out-deviation-atr 3 --label-holding-hours 12
```

本轮已实际运行BTC/ETH各180组合，查看入口 `runs/holding_comparison_v2_20261018/index.html`。BTC共2137笔完整仓位、77次减仓；ETH共2230笔、21次减仓。默认观察账户BTC为 `1h|pattern_only|three_bar_breakout`：验证46笔，平均7.87h、中位5.25h、最长47h，其中2笔硬止损不足4h。ETH为 `2h|hist_gradient|fixed_rr`：4笔，平均54.625h、中位21.375h、最长168h。不同配置的交易不能合并成一个账户的收益或持仓占比。

BTC单一候选验证账户收益+1.37%，但预留测试20笔净收益−1.00%、双倍费用−2.10%，最终 **REJECTED**；ETH仍 **NO_CANDIDATE**。两者都没有通过最终模型筛选，继续只生成研究和模拟通知。此前手工运行目录保留原交易与曲线，已补持仓报告：旧默认7笔平均18.29h、中位16h、最长32h。

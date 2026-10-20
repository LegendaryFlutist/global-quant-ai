# 优先合约、多周期结构与PyTorch GRU

默认顺序为 **KORUUSDT → CLUSDT → NVDAUSDT → BTCUSDT**。前三个已通过币安USD-M exchangeInfo核实为 `TRADIFI_PERPETUAL`，BTC为加密永续。使用的是各自合约的成交价K线、标记价和资金费，没有替换成ETF现货、原油现货或股票日线。ETH保留为可选研究标的。

三个新增合约的90天原始快照保存在 `data/priority_raw_20261018/`，组装后的数据为 `data/<symbol>_90d_connector/`。每份各8640根15m K线；固定验证/留出边界声明于 `evaluation_plan.json`，在模型训练之前创建。KORU/CL/NVDA资金费事件分别为270/540/275条，按实际时间和金额计入，不假设所有标的都是8小时一次。

## 分析报告

入口：`本次运行/analysis/index.html`，每个标的有独立目录，包含 **15m、30m、1h、2h、4h、1d、1w、1mo**。

- 实际K线实体/影线；最近确认高低点连接的趋势线，并区分守住/已破位。
- 确认高低点结构和EMA12/48趋势分别显示，允许出现分歧。
- 支撑/压力来自最近120个已确认转折；以 `max(0.3%价格,0.35ATR)` 合并附近位置，列区域、中心、距离和触点数。价位相对最新15m快照分类，明确区分它与较大周期的最后完整收盘价。
- 周期为过去32～256根K线的去线性趋势、加窗频谱峰值，只在至少观察到两次周期时描述。它不确定预测未来拐点。
- 波浪只列候选：标准推动浪检查浪2不越起点、浪3非最短、浪4不重叠浪1；三浪部分结构不得提前声称完整五浪已确认。简化ABC和其他解释明确区分，列失效参考位置。不匹配就报告歧义，月线少于5根不强行标浪。
- 38.2% / 50% / 61.8%是最近确认波段的回撤参考，不直接认定必然支撑/压力。
- 每个周期保存完整CSV与独立SVG，可导出复核。所有转折需等待右侧2根K线确认，训练特征从确认时开始使用，不回填到原转折时刻。

## 分析型道氏123：analysis_dow123

独立规则模型，不依赖12类K线形态投票。开仓条件为：

1. 低周期确认趋势线突破；
2. 突破后确认更高低点（看多）或更低高点（看空）；
3. 收盘突破冻结的前高/前低；原趋势新极值会使未完成结构失效；
4. 四倍大周期已收盘EMA趋势同向，确认高低点结构不能反向；
5. 通过历史7日2%～50%振幅筛选。

只把新的123确认事件作为入场候选，历史持续状态不能当作每根新信号。波浪候选用于分析解释，避免主观计数成为强行开仓的条件。执行与其他模型共用15m响应延迟、24h～21天持仓、费用、资金费和风险退出规则。

## 历史深度时序模型：torch_gru

- PyTorch双层GRU，隐藏单元32，输入最近24根**连续**、完整、已知的K线特征。
- 特征包含原12反转家族、价格/量能/ATR/RSI、大周期趋势，以及确认结构、123、支撑压力距离、波浪候选和过去转折周期等上下文。
- 标准化均值和尺度只来自当折训练集。训练样本的48h标签必须结束于验证开始之前。
- 训练样本按时间顺序构造，不能跨缺失K线压缩；验证输入可包含已知但因标签跨度从训练中清除的过去K线特征，绝不读取其未来标签。
- 类别为做空/观望/做多。固定训练20轮、加权交叉熵、AdamW和梯度裁剪，不按验证收益挑轮次。GPU采用IEEE FP32，关闭TF32近似以改善跨CPU/GPU的检查点一致性。
- 推理读取方向概率，再结合四倍大周期趋势、不反向的结构、历史波动筛选。它可在没有特定K线形态时提出方向，区别于原有模型的形态入场门槛。
- 实际训练设备、GPU名称、样本数、类别分布、损失、参数量、训练标签截止与验证起点保存在 `symbols/<标的>/deep_training.json`；训练图在 `deep_training.html`，权重在 `deep_checkpoints/<周期>/fold_N.pt`。
- 训练损失下降不等于验证收益。只有通过共同验证门槛的一个候选可以评估固定留出区间；不因新增深度模型就放宽交易数/稳定性门槛。

8个模型/规则 ×5个入场周期 ×7种退出 = **280组/标的**；四个优先标的共1120组独立账户。不同模型或标的的收益不相加为组合收益。

## 运行

```bash
# 使用当前默认策略生成本地研究报告
bash scripts/container.sh run

# 显式选择当前注册的四个规则/深度算法
bash scripts/container.sh run --models analysis_dow123 patchtst tft_attention tcn

# 单独生成多周期分析，不训练
bash scripts/container.sh lab analyze-multi \
  --symbol KORUUSDT --data data/koruusdt_90d_connector --out runs/koru_analysis_new
```

原研究目录均保留。BTC此前已有实验被查看过，新增策略仍可能存在选择偏差；这些新增分析不能把已看过的历史重新变成独立样本。

## Web系统页：四模型统一研究白名单

“模拟与回测”页中的多模型面板与上述历史CLI批次分开，当前可选多周期道氏123、PatchTST、TFT-Attention和TCN。后三者各输入48根连续已收盘K线，训练折内拟合缩放参数，采用固定8轮和seed 42；PatchTST用重叠patch与双层Transformer encoder，TFT-Attention用特征选择门、LSTM及多头注意力，TCN用膨胀卷积残差块。推理方向还需通过相同的已收盘高周期、确认结构和历史波幅筛选。

四个算法在同一标的/周期/验证折/费用/滑点/退出集合下生成独立权益曲线、模型×标的逐笔报告和训练审计。报告包含每折算法、参数量、序列数、loss、seed、训练标签截止、验证起点，以及研究代码和数据SHA256。训练loss不等于交易收益，且历史验证区间已被检查过；新架构只作为候选比较，不能据此宣称收益率更高，也不自动挑选或启用模型。新任务从Web页面启动：`/simulation` → “开始整套回测”。

## 参考

[PyTorch GRU](https://docs.pytorch.org/docs/stable/generated/torch.nn.GRU.html)、[PyTorch浮点精度](https://docs.pytorch.org/docs/stable/notes/cuda)、[StockCharts波浪规则](https://chartschool.stockcharts.com/table-of-contents/market-analysis/elliott-wave-analysis-articles/identifying-elliott-wave-patterns)、[KORU官方产品](https://www.direxion.com/product/daily-msci-south-korea-bull-3x-etf)、[币安KORU合约调整公告](https://www.binance.com/en-IN/support/announcement/detail/c226162366c54b78a7f98021b38e10c5)。本轮从7月19日起，避开7月15日拆股调整区间；不直接将ETF多日收益视作指数收益乘3。

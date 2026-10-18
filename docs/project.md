# 代码与数据结构

入口、参数、计算、报告、容器和测试按职责分开；保留原 `lab.py` 子命令与模块导入，便于旧报告和脚本继续使用。

| 层 | 文件 | 职责 |
|---|---|---|
| 启动与配置 | `run.py`、`research_config.py`、`config/swing.json` | 预检、读取参数、生成唯一批次目录、顺序执行标的、汇总报告 |
| 形态与特征 | `candlestick_patterns.py`、`pattern_features.py` | 12类反转、已收盘多周期、历史波动筛选、48h标签及训练截断 |
| 训练与验证 | `pattern_research.py`、`model_registry.py` | 8模型/规则、扩展窗口、候选筛选、单候选留出测试、模拟通知 |
| 合约执行 | `pattern_execution.py` | 实际15m延迟回放、时间/价格目标、硬止损、逐仓强平近似、资金费和部分减仓 |
| 新增深度与结构 | `deep_model.py`、`market_structure.py`、`structure_report.py` | PyTorch时序训练、确认转折/波浪/周期、独立道氏123、多周期K线与支撑压力 |
| 报告 | `operation_report.py`、`batch_report.py` | 独立账户统计、持仓时间线、收益曲线、总体→模型→标的报告与CSV |
| 通知 | `paper_mail.py`、`notifications.py`、`settings.py` | 本地去重模拟邮件；容器固定模拟且屏蔽本机SMTP配置 |
| 原研究能力 | `lab.py`、`technical.py`、`strategy.py`、`futures_backtest.py`、`leverage_research.py` | 历史基准、道氏123、趋势线、旧杠杆敏感性回放等兼容入口 |
| 容器与开发 | `Dockerfile`、`compose.yaml`、`scripts/`、`.devcontainer/`、`.vscode/` | 基于NVIDIA镜像的环境、GPU诊断、统一运行命令和VS Code接入 |
| 数据与实验 | `data/`、`runs/` | 原始行情只读；每次实验另建目录，不覆盖历史 |
| 验证 | `tests/` | 因果性、执行与费用对账、时间约束、报告口径、批次结构和入口 |

流程：`run.py → pattern_research → pattern_features + 模型 → pattern_execution → operation_report → batch_report`。

## 数据与时间边界

当前原始目录为 `data/{btcusdt,ethusdt}_90d_connector`，包含15m成交价K线、标记价K线、精确资金费事件、来源和数据日期。旧基准报告仅提供对应SHA256及固定测试边界；它们不作为新策略的训练数据。所有高周期只聚合完整K线，未来标签单独存储并按标签结束时间清除跨验证边界的训练样本。

本轮约前36天作为最早训练历史，后续三个约12天验证窗口逐次扩展训练；剩余区间留给单一合格候选。每个验证折结束时清仓，所以长持仓可能受折末截断。当前不把整段90天都描述为可交易样本，也不伪造未实际持有的21天交易。

## 收益分母

- 标的数量加权方向价格收益：整笔交易毛损益 / 初始开仓名义金额，已计各次部分平仓的价格与数量，未扣手续费和资金费。
- 保证金净收益：整笔净损益 / 初始分配逐仓资金，扣手续费、资金费，计入执行滑点。
- 本笔账户收益：整笔净损益 / 开仓前账户权益。
- 验证曲线：各折净值复合，每笔用 `stitched_net_pnl = net_pnl × fold_start_equity` 对账。

每个模型×周期×退出×标的是不同实验账户，不能相加作为组合收益；也不能相加持仓时间得到一个账户的持仓占比。

## 后续深度学习

原有sklearn模型保留CPU训练；新增双层GRU已在GB10上训练，详见 `docs/deep-structure-models.md`。容器继承NVIDIA优化的PyTorch/CUDA并已实际做GPU计算验证；引入序列模型时，可在 `pattern_research.estimator/fit_model` 的契约外新增独立PyTorch适配器，并保持 `predict_proba` 类别顺序、样本时间与模型保存接口。序列输入需进一步清除跨训练/验证边界的重叠，不能直接把当下容器GPU可用称为已实现深度学习策略。

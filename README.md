# Global Quant AI

[![CI](https://github.com/LegendaryFlutist/global-quant-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/LegendaryFlutist/global-quant-ai/actions/workflows/ci.yml)

全球市场量化研究工作台 · React / FastAPI / PostgreSQL / PyTorch · Phase 1及部分Phase 2研究功能。

```bash
git clone https://github.com/LegendaryFlutist/global-quant-ai.git
cd global-quant-ai
bash scripts/platform.sh up
bash scripts/platform.sh credentials
```

访问 http://localhost:8080 。首次启动随机生成本机登录密码。需要 Docker Engine 与 Compose v2。基础 Web 服务不需要 GPU；已有 NVIDIA 研究容器独立保留。

新克隆不包含个人行情、交易记录、权重或数据库。首次启动会尝试通过 Binance 公开接口采集四个优先标的最近90天数据，网络失败会明确显示；示例目录是标明来源的合成数据，不能作为真实收益证据。其余研究登记资产尚未接入行情源。实盘交易未实现且后端禁止下单。

会话恢复：先运行 `bash scripts/resume.sh`，读取[项目进度](PROJECT_PROGRESS.md)、[任务清单](TASKS.md)和[恢复/备份流程](docs/platform/recovery.md)。每个验证单元提交Git，换会话不依赖聊天记忆。

持续维护：[参与指南](CONTRIBUTING.md) · [安全说明](SECURITY.md) · [提交问题](https://github.com/LegendaryFlutist/global-quant-ai/issues)。尚未指定开源许可证。

## 当前本机版本


已新增本机 Web 研究工作台，保留下方原有量化研究引擎。启动：

```bash
cd ~/Documents/quant_lab
bash scripts/platform.sh up
bash scripts/platform.sh credentials
```

浏览器打开 **http://localhost:8080**，使用本机生成的 `admin` 登录信息；登录后可修改密码。不要在聊天中发送密码。当前提供资产注册表、四个优先标的真实历史多周期 K 线、公开行情采集任务、设置与明确的阶段能力状态。资产列表可按虚拟货币、股票和ETF底层类别，以及现货、永续、期货或研究指标产品类型筛选；模拟账号最多可选100个已核验标的。系统页“模拟与回测”的多模型面板可统一比较道氏123、PatchTST Transformer、TFT-Attention和TCN。深度架构不会自动部署，历史结果也不能保证未来收益。实盘执行未实现且服务端禁止下单。

- [完整架构](docs/platform/architecture.md) · [核心数据表](docs/platform/schema.md)
- [Phase 1 使用、验证及限制](docs/platform/phase1.md)
- [Compose 配置](compose.platform.yaml) · [工程规则](AGENTS.md)

测试：`bash scripts/platform.sh test`；前端：`cd frontend && npm test && npm run build`。

---

# Quant Lab：DGX Spark 合约波段研究

当前入口是 `run.py`，推荐从已启动的 NVIDIA 容器运行：

```bash
cd ~/Documents/quant_lab
bash scripts/container.sh run
```

容器：`quant-lab-lab-1`；项目镜像：`quant-lab:ngc25.11`，基于你本机的 `nvcr.io/nvidia/pytorch:25.11-py3`。已验证 GB10 GPU / CUDA / NVIDIA PyTorch 可用。此镜像没有 TensorFlow；旧离线GRU和系统页的PatchTST、TFT-Attention、TCN均基于PyTorch。环境细节和重建步骤见 [容器说明](docs/container.md)。

## VS Code

按 `Ctrl+Shift+B` 使用“回测：DGX Spark容器 优先四合约”任务。也可安装 Dev Containers 扩展，执行 **Dev Containers: Reopen in Container**，在容器里选择 `/opt/quant-venv/bin/python` 后运行 `run.py` 或按 F5。

宿主机 `.venv` 保留，容器使用独立环境，不需要在宿主机重新安装深度学习库。容器和宿主机共享项目数据与报告。

观察行情已更新至2026-10-18 15:44（上海时间），模型回测仍保留截至10月17日23:44的统一90天区间，新增观察K线没有计入拟合/收益。

当前四标的分析：[多周期分析总览](runs/priority_structure_gru_20261018/analysis/index.html)；[八模型回测总览](runs/priority_structure_gru_20261018/index.html)。每标的包含15m/30m/1h/2h/4h/1d/1w/1mo的K线、趋势线、支撑压力、周期和候选波浪。

## 当前默认策略

参数集中在 [config/swing.json](config/swing.json)，每次运行会把最终参数保存到本次目录的 `config.json`。本轮只使用现有最近90天 KORUUSDT、CLUSDT、NVDAUSDT、BTCUSDT 合约行情（按此优先顺序）；启动和报告均显示实际数据日期，不会将训练用历史称为全部可交易回测期，也不会默默下载行情。

- 入场周期：15m、30m、1h、2h、4h；趋势过滤使用各自4倍的已收盘大周期。
- 原6模型的入场：至少一种同向反转K线形态，大周期同向，通过模型概率条件，再检查最近7天完整4h K线的 `(最高价−最低价)/最低价` 在 **2%～50%**。这是历史机会筛选，不能知道未来一定有多少涨幅。
- 执行：信号收盘确认后等待15分钟，按真实15m开盘及滑点成交，不假设瞬间响应。
- 常规持仓：**24小时～21天**。保护止损、强平和研究期末截尾可提前退出；大周期或明确反向信号在最短持仓后退出。
- 价格目标：普通止盈至少覆盖 **2%** 的有利标的价格变动；新增 `swing_percent` 的默认目标为 **5%**。该模式止损取 **3%价格止损**与ATR止损中较近的一档，价格目标可配置在2%～50%。趋势退出、时间退出、止损、减仓和截尾可以在价格目标前发生。
- 风险ATR：取信号周期与已收盘24h ATR的较大值；训练标签按延迟入场后的48h收益，超过2%加往返成本才标记方向。
- 时间减仓：持仓满7天且与入场价偏离3ATR后，每48小时减初始数量25%，不补仓；实际没有触发就如实报告0次。
- 研究资金：5倍逐仓，初始分配账户权益10%，因此标的5%的波动不等于整个账户5%的收益。杠杆、保证金比例、成本和资金费在报告单列。

此前已生成的八模型报告为冻结历史产物。当前新研究任务的四模型白名单为多周期道氏123、PatchTST、TFT-Attention与TCN，详见 [深度模型与结构分析](docs/deep-structure-models.md)。旧报告仍可只读比较；新回测不连接真实账户或下单，邮件只做本地模拟记录。

## 统一报告目录

```text
runs/本次运行/
├── index.html                         # 总体报告、8模型比较、全部独立账户可切换
├── config.json                        # 本轮实际参数
├── runtime.json                       # 实际执行环境（新版启动器）
├── run_status.json / batch_summary.json
├── accounts_summary.csv               # 全部独立账户指标
├── analysis/index.html                # 四优先标的8周期K线、趋势/周期/支撑压力/波浪
├── symbols/
│   ├── KORUUSDT/                      # 原始研究、GRU训练权重/损失、账本与模拟通知
│   ├── CLUSDT/
│   ├── NVDAUSDT/
│   └── BTCUSDT/
└── models/
    ├── logistic/index.html            # 每模型报告，含各标的及全部周期/退出
    │   └── symbols/BTCUSDT/
    │       ├── operations.html        # 分标的操作、持仓统计/时间线、账户收益曲线
    │       ├── trade_economics.html    # 每笔标的涨跌/保证金收益/账户收益
    │       └── *.csv                  # 交易、减仓、收益分母、完整净值等
    └── 其他模型/...
```

每条曲线是一个独立模拟账户，各策略收益不相加为“整体收益”。总体页可以切换每个账户、按标的比较各模型代表曲线，点击模型后查看该模型下各优先合约操作。每笔同时列出方向价格收益、保证金收益、账户收益和各自分母。

上轮两币报告仍保留：[runs/swing_20261018/index.html](runs/swing_20261018/index.html)。当时BTC/ETH各210组，均为 `NO_CANDIDATE`：延长持仓没有保证提高收益，不因出现少量盈利交易就降低筛选门槛。验证每折约12天，折末会强制平仓，所以90天数据不足以充分检验21天持仓；21天是执行上限，不是本轮实际最长持仓。

## 常用命令

```bash
bash scripts/container.sh run --check
bash scripts/container.sh run --symbols KORUUSDT CLUSDT NVDAUSDT BTCUSDT
bash scripts/container.sh run --models analysis_dow123 patchtst tft_attention tcn
bash scripts/container.sh run --out runs/swing_custom
bash scripts/container.sh run --price-target-pct .08 --min-holding-hours 24 --max-holding-hours 504
bash scripts/container.sh check
bash scripts/container.sh test
bash scripts/container.sh shell

# 重生成统一报告，不训练、不重读测试集
bash scripts/container.sh lab report-batch --run runs/swing_20261018
```

输出目录必须不存在，避免覆盖既有实验。参数也可用 `--config config/自定义.json` 指定JSON，命令行覆盖JSON值。改动参数后不要反复挑看起来最好的历史曲线；之前查看过的数据已带选择偏差。只有验证候选可使用原留出的测试区间，当前轮次没有合格候选，所以没有重算测试集。

项目各模块职责见 [代码与数据结构](docs/project.md)。此前各版本研究说明保存在 [历史说明](docs/research-history.md)，旧数据与回测结果未搬动或覆盖。

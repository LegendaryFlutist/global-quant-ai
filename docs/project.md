# 工程模块与函数文档

## 研究引擎

实际实现位于`quant_lab/`，根目录同名Python文件只保留旧导入/命令行兼容。新代码必须从领域包导入，不继续向根兼容文件堆业务逻辑。

| 包 | 职责 |
|---|---|
| cli | 批量研究和旧子命令入口；`python -m quant_lab`与`python run.py`等价 |
| core | 项目资源路径、研究配置和本机配置解析 |
| data | 公开K线解析与历史基础特征 |
| analysis | 12类反转形态、经典道氏/123、确认摆动点与波浪候选 |
| models | 注册名称、PyTorch GRU训练/推断及可信检查点序列化 |
| strategies | 只用已收盘大周期的入场过滤、特征和未来标签隔离 |
| backtesting | 逐仓执行、手续费/滑点/资金费、近似强平与完整仓位账本 |
| research | 冻结时间切分、候选筛选、杠杆场景对比、模拟审计 |
| reports | 独立账户曲线、持仓/逐笔操作、结构图与三级汇总 |
| notifications | 明确区分真实显式SMTP、预览及本地模拟记录 |
| risk | GRU入场质量、只用过去收盘估计的波动预算、止损/回撤上限、仅减仓规则 |

`quant_lab.services.risk_research`复用本机冻结检查点，在已使用验证窗生成只读风险对照产物；`config/risk.json`为版本化策略。输出不是当前信号，页面只提供历史研究和预算试算，不连接账户、不下单。

`quant_lab.compat.alias_legacy`让旧名与新模块指向同一个对象，保留旧测试patch和`deep_model.TorchGRUClassifier`模型的反序列化路径。不要删除兼容层或直接加载不可信joblib/pickle。

`quant_lab.core.paths.PROJECT_ROOT`统一定位根目录config、watchlist和.env；不依赖调用时工作目录。GPU镜像显式复制领域包，API镜像保留独立Python3.11依赖。

## Web后端

- `app/api/`：按认证、资产、行情、任务、设置、系统状态和只读风险研究分路由。
- `app/core/`：配置、数据库和认证边界。
- `app/domains/`：持久模型、公开资产目录和后续领域契约。
- `app/services/catalog.py`：幂等注册资产；`ingestion.py`：真实数据导入/核验/采集；`jobs.py`：队列、有限重试、终态对账。
- `app/bootstrap.py`：首次本机管理员与任务恢复；不混入资产长列表。
- `app/adapters/`：固定主机公开GET适配器。

## Web前端

`App.tsx`只协调路由和任务反馈。`pages/`每页一个组件，`layout/`负责导航和公共反馈，`hooks/useAuth.ts`负责会话，`hooks/useWorkspaceData.ts`负责只读轮询，`services/`负责同源API，`components/`负责图表和状态徽标。风险页显示本机历史研究、单标的曲线和受限的假设仓位预算；页面不直接访问交易所或存储会话令牌。

## 注释规范

具名Python函数（含内部函数、方法、脚本、测试夹具和迁移入口）有中文docstring，说明用途、参数口径、返回值及必要约束。参数默认值以签名为准；价格变动、保证金收益、账户收益不能混称。毫秒/小时/分钟、标签的未来窗口和摆动点确认时间必须明确。

具名TypeScript函数、组件和hooks使用JSDoc；匿名映射/事件回调由所属具名函数的文档及局部注释解释，不机械逐行翻译语法。新风控模块也必须遵守相同规范。

```bash
python3 scripts/check_docs.py
bash scripts/container.sh test
bash scripts/platform.sh test
cd frontend
npm test
npm run build
```

此次迁移只给旧Alembic迁移补说明，revision和DDL不变；不会重新训练或修改冻结数据、模型权重、收益记录。新增风险策略应另设版本和验证产物，不能覆盖原基线。

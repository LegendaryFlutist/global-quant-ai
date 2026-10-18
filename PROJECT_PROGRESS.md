# 项目进度与会话交接

更新时间：2026-10-18（Asia/Shanghai）。每个经过验证的开发单元都更新本文件、TASKS.md并创建本地提交。本文件不依赖聊天历史。

## 当前阶段

**Phase 1 已交付；正在发布 GitHub 基线并建立长期恢复机制。Phase 2 尚未开始。**

目标仓库：https://github.com/LegendaryFlutist/global-quant-ai

Git分支：`main`。准确最新提交以`git log -1 --oneline`为准；本文件与代码一起提交，不能在自身内容里保存自身提交哈希。已发布与CI状态见下方交接记录。

## 已完成功能

- Web：React/TypeScript/Vite/ECharts；总览、资产、多周期K线、任务、能力状态、设置、登录；Python3.11 FastAPI按领域拆分。
- 数据：PostgreSQL/Alembic 0001、Redis/RQ、不可变Decimal Parquet/SHA256；公开Binance精确代码核验、分页采集、断点恢复、异常重试；不伪造小周期或缺失行情。
- 身份：Argon2id、服务端过期会话、CSRF/Origin检查、登录限流、密码变更撤销会话；本机loopback部署。无实盘执行能力。
- 资产：80项登记、13个真实精确合约代码的公开元数据快照；四个优先标的KORUUSDT、CLUSDT、NVDAUSDT、BTCUSDT。本机保留34,544根真实15m行情，最近90天大周期严格聚合。
- 离线研究：既有12类反转信号、多周期、经典道氏与1-2-3、波浪候选、GRU等模型、杠杆合约回测、持仓统计、分层报告、模拟邮件。Web集成尚未完成。策略配置`config/swing.json`、模型代码与冻结数据计划保持原状。
- 容器：保留NVIDIA NGC PyTorch 25.11 GPU lab；独立Python3.11 API、worker、Web、PostgreSQL、Redis。镜像摘要与依赖锁固定。
- 分享准备：匿名配置/示例邮箱、凭据和私人数据排除、发布扫描、贡献指南、安全说明、Issue/PR模板、固定官方Actions摘要的CI、Dependabot。
- 新克隆：没有私人历史目录时改为公开采集任务；不把不存在的本机数据标为导入成功。
- 恢复规范：AGENTS.md架构/恢复约定；本进度文件；TASKS.md；只读`resume.sh`、显式文件提交`checkpoint.sh`；持久状态备份/恢复说明。

## 实际验证记录

| 日期 | 开发单元 / 命令 | 真实结果 | 证据 |
|---|---|---|---|
| 2026-10-18 | Phase1 Docker + 浏览器 | 4个标的×6周期共24组图表通过，80项资产、34,544根真实K线；无浏览器未捕获异常 | 本机`runs/platform_phase1/browser-check.json`、截图、runtime-check.json |
| 2026-10-18 | `bash scripts/platform.sh test`（原Phase1） | 20 passed | 本机`runs/platform_phase1/backend-tests.log` |
| 2026-10-18 | `cd frontend && npm test && npm run build` | 2 passed；TypeScript与Vite生产构建成功 | 本机`runs/platform_phase1/frontend-tests.log`、frontend-build.log |
| 2026-10-18 | `npm audit --json` | 当时0项已知问题；不是未来安全保证 | 本机`runs/platform_phase1/frontend-dependency-audit.json` |
| 2026-10-18 | 分享准备：`bash scripts/platform.sh build`、`test` | 构建成功；21 passed（含新克隆采集路径）；1项Starlette TestClient弃用提示 | `/tmp/quant_github_build.log`、`/tmp/quant_github_backend_tests.log`，会归档至本机runs |
| 2026-10-18 | 匿名邮箱：`docker exec quant-lab-lab-1 python -m unittest tests.test_notifications tests.test_futures -q` | 14 passed，SMTP均为测试mock，无真实投递 | `/tmp/quant_github_mail_tests.log`，会归档至本机runs |
| 2026-10-18 | `python3 scripts/check_publish.py --staged` | 首次136文件扫描通过；最终提交前需重新扫描新增恢复文档 | 见本阶段交接记录 |

未把本地测试声称为远程CI结果。`/tmp`日志不耐重启，正式阶段结束应归档至`runs/maintenance/`；公开文档仅保留无敏感信息的结果摘要。

## 当前问题与边界

1. 容器对fapi.binance.com DNS解析失败；宿主先前也曾返回HTTP451。自动更新不可用，已有真实历史快照可研究；不切换来源绕过限制、不伪造行情。
2. Phase2–8 Web技术分析、模型注册/训练、持久模拟账本、SMTP/私有API凭据、2FA审批与实盘执行均未完成，UI明确PLANNED。禁止擅自启用。
3. 原模型结果未达晋升门槛；不能把最高样本收益称为稳定盈利模型，不能因为恢复会话而重新使用冻结留出集。
4. 本机账号、历史数据、权重和数据库不在GitHub；重启后应保留原持久卷/目录。磁盘丢失必须依赖另行备份，不能靠Git恢复。
5. 单人本机环境，不等于公网生产系统；测试提示未来需迁移Starlette TestClient到httpx2；项目暂未选择开源许可证。

## 进行中与下一步

- 本次：完成恢复文档/脚本验证 → 本地基线提交 → 推送公开仓库 → 检查Actions实际状态 → 记录交接。
- 下次：先按TASKS.md完成GitHub CI或恢复验证中仍未解决的P0任务，再进入Phase2。
- Phase2：复用离线市场结构模块，先以确认时间和prefix不变性测试锁定无未来数据，再接Web道氏/波浪/支撑压力/趋势线/回放。

## 发布交接记录

- GitHub已通过官方CLI登录；不在此文件记录任何授权码或Token。
- 公开仓库已创建；首次提交、推送、远程CI与恢复脚本最终验证：**进行中，不能当作已完成；阶段结束前更新此处。**

## 新会话首先运行

```bash
cd <你的项目路径>
bash scripts/resume.sh
# 核对当前分支、未提交改动、最近提交、TASKS.md，按需只读查看服务
bash scripts/platform.sh status
```

需要启动已停止服务时：`bash scripts/platform.sh up`。需要测试时：`bash scripts/platform.sh test`，前端进入frontend运行`npm test && npm run build`。禁止为了恢复而删除卷、覆盖.env或重建冻结数据计划。

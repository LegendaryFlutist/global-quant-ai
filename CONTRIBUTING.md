# 参与和持续维护

当前版本：Global Quant AI V2.0，Phase 1。本项目用于研究，Web 不支持实盘下单。

## 分支和提交

- `main` 保持可运行；改动使用 `feature/<name>`、`fix/<name>` 分支并提交 Pull Request。
- 描述问题、修改后的行为、实际测试和已知限制；一个 PR 聚焦一个目的。
- 后端业务结构变更新增 Alembic revision，不修改已交付迁移；不重写模型版本和冻结回测结果。
- 如需第三方代码复用，请先确认许可证。当前项目尚未指定开源许可证；公开源码不代表授予任意再分发许可。

```bash
git switch -c feature/my-change
# 修改并运行相应检查
git add <changed-files>
git commit -m "feat: describe the change"
git push -u origin feature/my-change
```

## 运行和验证

```bash
bash scripts/platform.sh up
bash scripts/platform.sh test
cd frontend
npm ci
npm test
npm run build
```

普通 Web 开发不需要本机 GPU；NVIDIA 研究服务需另外准备 NGC 基础镜像和 NVIDIA Container Toolkit。新克隆不含私人行情或模型文件：可启动 Web 注册表，在任务/图表页通过 Binance 公开接口采集；成功与否取决于网络和上游可用性。只有原本机保有四标的历史快照。

CI 使用独立 PostgreSQL/Redis 执行后端测试，并验证前端构建；不获取账户密钥，不发送邮件，不训练或晋升模型。

## 数据与凭据

遵守 [AGENTS.md](AGENTS.md)。不得提交 `.env`、`.runtime/`、密码、API Key、会话令牌、SMTP授权码、账户数据、真实交易记录。`data/`、`runs/`、模型权重、数据库和虚拟环境只保存在本机，发布前运行：

```bash
python3 scripts/check_publish.py
```

不要把个人交易盈亏或账户截图直接附到公共Issue。报告数据问题时提供匿名的小样本及来源信息，并区分真实数据和合成测试数据。

## 后续阶段

参见 [完整架构](docs/platform/architecture.md)、[核心表设计](docs/platform/schema.md) 和 [Phase 1](docs/platform/phase1.md)。后续模块在通过验证前必须保持 PLANNED，模型不能绕过风控与逐笔审批。

## 中断恢复

每次新会话先运行`bash scripts/resume.sh`；每个验证单元更新PROJECT_PROGRESS.md、TASKS.md并提交。详细流程见[恢复说明](docs/platform/recovery.md)。

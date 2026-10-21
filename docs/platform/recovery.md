# 长期开发、中断恢复与私人状态备份

恢复入口是版本库内的AGENTS.md、PROJECT_PROGRESS.md、TASKS.md和Git状态，不是任何单次聊天。维护者可以更换Codex会话、模型或重启计算机。

数据版本D1恢复：同时保留PostgreSQL（迁移0004）与`.runtime/market-data/dataset_versions/`的完整目录；manifest/raw/Parquet及共享blobs缺一不可。硬链接可以由保留链接的备份工具复制，也可恢复为独立但字节相同的文件，不删除被历史manifest引用的内容。先查`INGEST_DATASET`任务进度和版本SHA，再从页面重试；已经成功的版本不会覆盖。失败的新源不得用旧缓存冒充成功。新源任务本身不在启动时全量重拉；旧来源启动策略仍独立运行。

## 新会话 / 额度耗尽后

```bash
cd <项目路径>
bash scripts/resume.sh
bash scripts/platform.sh status
```

`resume.sh`只读取交接文件与Git，不会拉取、迁移、覆盖文件、重置工作区或显示凭据。先检查未提交/暂存修改，确认已有实现，再选择TASKS.md中最高优先级未完成任务。后台任务可能仍在运行，先查看状态，不能直接重跑训练/回测或采集。

在另一台机器克隆：

```bash
git clone https://github.com/LegendaryFlutist/global-quant-ai.git
cd global-quant-ai
bash scripts/resume.sh
bash scripts/platform.sh up
```

新克隆不含个人历史/账号/权重。它会创建新的本机账户和数据库，尝试公开采集，不能声称恢复了旧账户或模型权重。原机上服务停止后`up`复用持久卷和已有目录，不重置密码、模型配置或冻结数据计划。

## 每个可验证单元

1. 明确当前任务、验收条件；检查现有实现，避免重复建设。
2. 修改代码，运行相应测试；把真实命令、结果、警告和未完成事项写入PROJECT_PROGRESS.md，更新TASKS.md。
3. 审查显式文件后提交：

```bash
git diff -- <changed-file>
bash scripts/checkpoint.sh "fix: verified behavior" <changed-file> <other-files>
git log -1 --oneline
# 确认本阶段允许推送后：
git push
```

脚本还会暂存两份交接文件，检查整个暂存区，然后提交。它不会运行测试或替你判断工作已验证；暂存区含未显式指定的其他文件时脚本直接拒绝提交，且不改动原暂存内容。平时使用feature分支和PR。无相关测试可跑或被阻塞时，明确标WIP/UNVERIFIED并保留下一条执行命令，不勾选完成。

准备中断时，先完成已验证单元的本地提交；记录正在运行的任务/产物路径、未验证改动、失败原因和下一步。未推送的本地提交也能在本机续作，但只有推送后的版本能从GitHub恢复。禁止为了简化恢复执行reset --hard、clean -fdx或删除Docker卷。

## 哪些信息在哪保存

| 状态 | 持久位置 | GitHub是否包含 |
|---|---|---|
| 应用/模型代码、策略配置 | root Python、backend、frontend、config | 是 |
| 数据库迁移和依赖版本 | backend/migrations、requirements.lock、package-lock | 是 |
| 进度、任务、架构、安全规范 | 根目录三份交接文件、docs、AGENTS | 是 |
| 个人数据库（用户/会话/任务/行情指针） | Compose platform_postgres持久卷 | 否 |
| 队列/重试进度 | platform_redis AOF卷 + PostgreSQL任务表 | 否 |
| 不可变Parquet行情 | .runtime/market-data | 否 |
| 原始真实行情、来源、冻结评估计划 | data | 否 |
| 训练权重、运行元数据、分层报告、模拟邮件 | runs | 否 |
| 本机口令、数据库配置和初始密码 | .env、.runtime中的本机配置 | 否；不得写入进度文件 |

重启不应删除这些位置。Git不会恢复从未备份的私人数据，也不能保证磁盘损坏后恢复。模型恢复必须保留匹配的权重、特征/数据/代码校验、训练划分和版本元数据，不能只留一个收益率数字或重新训练冒充原模型。

## 私人完整备份（本地手工操作）

以下只写入已忽略的本机目录，**不会上传GitHub**。可包含个人行情、模型、数据库和凭据，需要足够磁盘空间，并自行复制到受控的加密备份介质。它不是已经执行并验证的灾备承诺。

```bash
umask 077
BACKUP_ROOT="$PWD/.runtime/backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_ROOT"
git rev-parse HEAD > "$BACKUP_ROOT/git-commit.txt"
# 应用当前无交易执行；做完整一致备份时先停止写入端，期间网页暂不可用。
docker compose --env-file .runtime/platform.env -p quant-lab -f compose.yaml -f compose.platform.yaml stop worker api
docker exec quant-lab-postgres-1 pg_dump -U quant -d global_quant -Fc > "$BACKUP_ROOT/postgres.dump"
# 根据本机存在的文件选取；首次克隆没有.env/data/runs时先删去不存在项。
tar -czf "$BACKUP_ROOT/private-state.tar.gz" \
  .runtime/market-data .runtime/platform.env .runtime/bootstrap-admin.txt \
  .env data runs config backend/migrations
sha256sum "$BACKUP_ROOT/postgres.dump" "$BACKUP_ROOT/private-state.tar.gz" > "$BACKUP_ROOT/SHA256SUMS"
bash scripts/platform.sh up
```

示例先停止写入端，再做pg_dump和文件归档。私人模型目录很大时可使用经过校验的增量备份工具，但必须保留原评估计划和模型元数据。不要归档或分享GitHub登录令牌/系统密钥环。

## 恢复边界

- 先检查SHA256、备份对应Git提交、所需镜像和磁盘空间。
- **先在独立路径、Compose项目名、端口和新数据库上演练**，不要覆盖当前个人业务库。Phase1尚未完成自动隔离恢复演练，TASKS.md保留未完成项。
- 恢复数据库dump和匹配Parquet、data/runs、受控本机配置；原数据库指针必须能对应同一批不可变文件。
- 先用备份对应代码验证，再按顺序执行新增Alembic迁移；不修改旧迁移或拿较新模型权重配较旧特征schema。
- 数据与密钥分开受控保存；未来有加密凭据时，密文数据库不能替代密钥管理备份。
- 记录实际恢复测试及限制后再接入使用。恢复失败停在隔离环境，不能自动下单、发送邮件或改写冻结研究结果。

## 阶段结束报告格式

每个阶段必须说明：已完成功能、实际通过的测试、未解决问题、`git log -1`、下一阶段具体任务、恢复命令。区分本地提交、已推送提交、远程CI成功；不能把排队/未验证当作成功。

# 核心数据表与演进计划

所有时刻为UTC TIMESTAMPTZ，K线时间为交易所epoch毫秒；界面按设置显示时区。金额/价格/数量用NUMERIC(38,18)/Decimal，Arrow行情为Decimal128(34,12)；浮点仅用于图表显示和研究统计。模型计算类型不替代账本精度。

## Phase1 已创建表（Alembic 0001）

| 表 | 主键 / 关键字段 | 约束与用途 |
|---|---|---|
| users | id, username, password_hash, role, created_at | username唯一；Argon2id；初始单管理员 |
| sessions | token_hash, user_id, csrf_token, expires_at | FK users，令牌仅存SHA256；12小时过期 |
| security_audit_logs | id, actor, action, detail JSON, created_at | 不保存密码或令牌；应用只追加 |
| providers | id, name, status, detail JSON | 状态、最近核验成功/尝试时间、实际错误 |
| assets | id, symbol, name, underlying_id, asset_type, venue, priority, verification, specification JSON, verified_at | id为市场+精确产品代码；相同底层不合并不同产品 |
| asset_aliases | id, asset_id, provider_id, alias | FK assets/providers；provider+alias唯一 |
| sector_membership | asset_id, sector | 组合主键；一个资产可有多个行业/主题 |
| market_data_metadata | id, asset_id, interval, path, row_count, first_open_ms, last_close_ms, sha256, source JSON, updated_at | asset+interval唯一；指向不可变Parquet快照 |
| scheduled_tasks | id UUID, kind, status, parameters JSON, result JSON, attempts, created_at, updated_at | 持久任务记录，对应RQ job_id；批量Binance归档将已完成合约ID、当前合约和计数保存在result供重试/页面恢复；失败原因可见 |
| system_settings | key, value JSON | 当前仅显示偏好；不接受API凭据 |
| system_events | id, kind, detail JSON, created_at | 预留系统事件记录，目前无监控告警引擎 |

行情事实文件由本地不可变Parquet保存，`market_data_metadata.path/sha256/row_count/time bounds/source`只作为PostgreSQL清单指针。DuckDB以进程内只读连接查询通过SHA256校验的文件；Redis用于任务队列、认证限流和20秒实时K线TTL缓存，缓存未命中时可读本地`live.json`快照。两种实时快照都不作为历史事实或策略配置的权威来源。当前尚无逐笔成交、历史订单簿、技术指标宽表或实盘账本表。

三年Binance长期历史仍以`DATA_ROOT/long_history/<symbol>/<interval>/`内的不可变Parquet分段及manifest为权威。`FETCH_BINANCE_ARCHIVES`任务参数保存本次确切资产ID列表，任务result保存合约级检查点，不新增数据库表或迁移；仅对已核验USDM合约排队。长期图表目标周期缺少独立归档时，API可从较长覆盖范围的15m真实K线聚合更大周期，并返回`source_interval`和源manifest/分段哈希；向下插值禁止。

## Phase2 已迁移配置表（Alembic 0002）

| 表 | 主键 / 关键字段 | 约束与用途 |
|---|---|---|
| simulation_accounts | id, owner_user_id, name, initial_equity NUMERIC(28,8), base_currency, configuration JSON, configuration_sha256, revision, created_at, updated_at | 每用户名称唯一；资金使用Decimal/NUMERIC；配置绑定标的、周期、理论权重和风险上限；revision/hash便于回测引用；仅研究配置，不是资金余额、成交或订单账本 |

回测执行由`scheduled_tasks`的`RUN_SIMULATION_BACKTEST`任务承载；任务参数固定账号所有者、配置修订/哈希和UTC时间窗，结果保存算法及行情来源哈希与报告相对路径。组合权益、回撤和逐笔数据作为按任务UUID隔离的本地不可变Parquet，不新增交易账本数据库表，也不记录模拟账号余额变更。

整套离线模型比较复用`scheduled_tasks`并使用`RUN_FULL_RESEARCH`类型；JSON参数只保存用户ID、固定输出UUID及白名单标的/模型/周期。GPU runner状态和进度写入任务`result`，完整模型/标的报告保存在`.runtime/research-runs/<UUID>/`；该目录不进入Git。已有`runs/`报告只读挂载，通过`batch_summary.json`发现并浏览，不迁移、不覆盖原有产物。

## Phase2–8 其他规划表（尚未迁移创建）

以下是完整领域规划，不表示存在业务功能；除上方明确列出的revision 0002配置账号表外，其他表都还不存在。后续迁移会补齐更精确字段、外键和并发约束。

| 阶段 / 表 | 核心字段、关联和关键约束 |
|---|---|
| 6 encrypted_credentials | id, owner_user_id, provider_id, environment, ciphertext, key_version, permissions_verified_at, revoked_at；密文+独立密钥管理，禁止浏览器回显 |
| 3 market_regimes | id, as_of, known_at, scope, score, regime, source_coverage, method_version；未知状态明确保存 |
| 3 sector_risk_events | id, sector, occurred_at, known_at, severity, source, evidence；可追溯 |
| 2 indicators | id, asset_id, interval, definition_version, params_hash, as_of, known_at, values；版本+参数+时间唯一 |
| 2 swing_points | id, asset_id, interval, pivot_at, confirmed_at, kind, price, detector_version；确认时间不可早于实际可知时间 |
| 2 wave_candidates | id, asset_id, interval, detector_version, swing_ids, rules_passed, invalidation_price, known_at；多候选，不伪造主观概率 |
| 2 pattern_signals | id, asset_id, interval, pattern_type, points, detected_at, confirmed_at, expires_at, filters, version；经典趋势与1-2-3分开 |
| 2 chart_annotations | id, owner_user_id, asset_id, interval, annotation_type, points, created_at；人工与机器结果隔离 |
| 2 multi_timeframe_analysis | id, asset_id, as_of, known_at, timeframe_config_hash, component_ids, result；只引用已知组件 |
| 3 model_definitions | id, name, family, input_contract, target_definition；算法定义与训练实例分离 |
| 3 model_versions | id, model_definition_id, semantic_version, code_hash, data_hash, feature_hash, split_manifest, seed, artifact_uri, artifact_sha256, metrics, approval_status；版本不可变 |
| 3 training_runs | id, model_version_id, task_id, status, params, dataset_manifest, started_at, finished_at, logs_uri；任务可恢复 |
| 4 strategy_definitions | id, name, decision_contract, risk_policy_id |
| 4 strategy_versions | id, strategy_definition_id, version, code_hash, params_hash, rules, created_at；不可变 |
| 4 strategy_model_bindings | strategy_version_id, model_version_id, role, weight；组合外键绑定明确版本 |
| 4 backtest_runs | id, strategy_version_id, task_id, dataset_manifest, evaluation_plan_hash, costs, seed, period, status |
| 4 backtest_results | id, backtest_run_id, asset_id, split, metrics, equity_uri, trades_uri, holding_stats；禁止把不同独立账户净值相加伪装组合 |
| 4 experiment_registry | id, hypothesis, code_hash, candidate_set, frozen_holdout_hash, preregistered_at, result_run_ids, promotion_status |
| 5 paper_accounts | id, owner_user_id, name, strategy_version_id, initial_equity NUMERIC, base_currency, status, cloned_from_id；策略固定版本 |
| 5 paper_balances | account_id, currency, available NUMERIC, locked NUMERIC, version；组合主键+并发版本 |
| 5 paper_positions | account_id, asset_id, side, quantity NUMERIC, entry_price NUMERIC, margin NUMERIC, leverage NUMERIC, version；按账户独立 |
| 5 paper_orders | id, account_id, client_order_id, strategy_version_id, asset_id, side, quantity, price, status；账户+client_order_id唯一 |
| 5 paper_fills | id, account_id, order_id, fill_key, price, quantity, fee, funding, filled_at；fill_key唯一，Decimal事务核算 |
| 5 paper_equity_snapshots | account_id, as_of, equity, cash, unrealized_pnl, drawdown；组合唯一 |
| 5 paper_account_events | id, account_id, event_key, event_type, amount, currency, reference_id, created_at；事件唯一且不可变，支持对账恢复 |
| 6 broker_connections | id, owner_user_id, environment, credential_id, account_fingerprint, verified_permissions, status；模拟/只读/实盘严格隔离 |
| 7 trade_proposals | id, account_id, strategy_version_id, immutable_snapshot, snapshot_hash, expires_at, status；参数变更必须新建议 |
| 7 trade_approvals | id, proposal_id, snapshot_hash, approver_id, two_factor_event_id, approved_at, expires_at, decision；不可变审计，不从邮件直接批准 |
| 7 execution_requests | id, proposal_id, approval_id, risk_recheck_id, client_order_id, request_hash, state；client_order_id唯一，超时未知不盲重试 |
| 7 exchange_orders | id, broker_connection_id, execution_request_id, exchange_order_id, client_order_id, status, raw_status, updated_at；连接+交易所订单ID唯一 |
| 7 exchange_fills | id, broker_connection_id, exchange_order_id, trade_id, quantity, price, fee, filled_at；连接+trade_id唯一 |
| 7 reconciliation_records | id, connection_id, execution_request_id, reconciliation_at, exchange_snapshot_hash, differences, resolution；未知订单先查询再处理 |
| 6 notifications | id, owner_id, kind, payload_hash, dedup_key, channel, created_at, status |
| 6 email_delivery_logs | id, notification_id, attempt, mode, provider_message_id, delivered_at, error；模拟投递不标记为实际发送 |
| 8 ideas | id, owner_id, title, hypothesis, acceptance_criteria, experiment_ids, status, created_at；假设需验证后才能晋升 |

## 事务与外键原则

账户资金、仓位、订单、成交、事件和权益变动必须同事务提交，账户行/版本锁隔离。成交引用的账户必须与订单、策略版本一致，不能仅靠API检查跨账户完整性。未来用组合外键、唯一约束与账本守恒测试实现。

模型版本与策略版本禁止原地改产物；晋升状态与运行账户绑定需显式流程。审批引用建议内容哈希、用户和2FA事件，执行请求引用审批与风控复核。审批修改、参数漂移、过期、撤回、重复提交、未知交易所结果均需独立状态机测试。

The full-research task allowlist now admits up to 13 exchange-verified symbols but only `analysis_dow123` for new runs. Existing archived model names remain readable through the archive path; no database migration is needed. Task parameters continue to pin selected symbol/model/timeframe lists and the output UUID; local source readiness is checked before enqueueing.

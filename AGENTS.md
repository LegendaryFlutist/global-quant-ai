# Global Quant AI engineering rules

- Root Python scripts are the existing offline research engine. Keep historical datasets, evaluation plans and reports immutable. New Web code lives in `backend/` and `frontend/`.
- Phase 1 implements authenticated public-data research only. `backend/app/domains/contracts.py` contains future contracts, not live or paper execution. Do not enable orders, ingest private exchange keys, or send real mail as part of platform work.
- Run platform with `bash scripts/platform.sh up`; preserve the existing `lab` service and unrelated Docker projects. Never run `docker compose down -v` or reset the user's database.
- Python backend must run on 3.11; GPU research currently uses NVIDIA's Python 3.12 runtime. Do not replace NVIDIA Torch/CUDA packages.
- Never print `.env`, `.runtime/platform.env`, bootstrap passwords, session tokens or broker credentials in tool logs. `.runtime/` must remain excluded from source and build contexts.
- Store monetary values as PostgreSQL NUMERIC / Python Decimal; chart conversion to float is display only. Preserve provenance, timestamps and dataset SHA256. Never manufacture missing quotes or upsample candles.
- Every future model version must pin feature/code/data hashes, splits and seed. Fit preprocessing on training data only. Pivots and signals must respect known-at timestamps.
- Unverified assets are research-only. Underlying IDs connect stocks, ETFs and related perpetuals without merging distinct products or inventing tradable symbols.
- API changes require authentication, CSRF, origin, input and server-side execution-boundary checks. Credentials stay server-side. Same-origin private localhost deployment is the development default; HTTPS is required for remote credential entry.
- Tests use only `quant_test` PostgreSQL and isolated Redis test service: `bash scripts/platform.sh test`. Never point tests at production. Frontend: `cd frontend && npm ci && npm test && npm run build`.
- Mark later phases as PLANNED in UI and docs until actually implemented and tested. Keep `docs/platform/architecture.md`, `schema.md`, and `phase1.md` current.

## Architecture map

- `frontend/src/`: React/TypeScript pages, chart components and same-origin API client.
- `backend/app/api/`: auth, assets, market, jobs, settings, system routers; `core/`: settings/database/security; `domains/`: Phase 1 ORM and future type contracts; `services/`: ingestion/queue; `adapters/`: public GET-only Binance.
- `backend/migrations/`: immutable Alembic revisions; `config/platform/`: public asset registry/snapshot; `config/swing.json`: research strategy policy.
- Root Python modules: existing offline backtesting, model families, confirmed market structure, reporting and simulated mail. Web integration is not complete for these domains.
- Persistent local state: PostgreSQL named volume, Redis AOF volume, `.runtime/market-data/`, `data/` and `runs/`. Git intentionally excludes these. Git is not a backup of personal research artifacts.

## Mandatory session recovery and checkpoints

1. At every new session or after interruption, read `AGENTS.md`, `PROJECT_PROGRESS.md`, `TASKS.md`, then `git status --short --branch` and `git log -5 --oneline` **before deciding what to implement**. `bash scripts/resume.sh` performs these read-only steps.
2. Treat the files and Git as the source of truth, not remembered chat. Check for user edits and running background tasks. Do not overwrite or repeat completed work. If the sandbox presents a masked `.git`, use the permitted normal Git access; never initialize over an unknown history just because sandbox status fails.
3. Pick the highest-priority unfinished task, state its acceptance conditions, and make one coherent, reviewable change. Existing modules should be reused and extended.
4. Run relevant tests. Record the actual command, date, result, warning/blocker and evidence location in `PROJECT_PROGRESS.md`; update `TASKS.md`. A local pass is not a GitHub Actions pass. A queued workflow is not successful CI.
5. Commit each verified development unit locally. Use explicit file paths, review staged diffs, run `python3 scripts/check_publish.py --staged`. `bash scripts/checkpoint.sh "type: verified unit" <files...>` adds the handoff docs and specified files, checks and commits; it does not run tests or push automatically.
6. Before quota/time limits, preserve verified work first. If interrupted work is saved, clearly mark it WIP/UNVERIFIED and record the exact next command; never call incomplete work finished. Do not amend/reset/rebase a published history to hide interruptions.
7. Keep configuration, strategy identifiers, data/evaluation hashes, requirements locks and migration revisions durable. Do not auto-retrain/reseed databases or replace a running model to resume development. Do not alter frozen evaluation files or existing artifacts.
8. Local data/weights survive reboot on persistent disk but are not in GitHub. Use `docs/platform/recovery.md` for application-aware backup/restore. Never put private backups, tokens or account data in Git. GitHub cannot recover artifacts that were never backed up.
9. End each phase with: completed features; actual tests; unresolved issues; latest Git commit; specific next tasks; recovery commands. Record GitHub push/CI state honestly. Default developer `main` changes should go through a feature branch/PR once the initial baseline is established.
10. Progress/tasks/CI artifacts must not contain passwords, API keys, tokens, personal account records or full environment dumps. No automatic destructive cleanup, `git reset --hard`, `git clean -fdx`, or `docker compose down -v` during recovery.

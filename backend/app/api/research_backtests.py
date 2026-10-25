"""整套多模型回测的页面任务、进度与报告读取API。"""

import hmac
import csv
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.core.config import DATA_ROOT, RESEARCH_ROOT, RESEARCH_RUNS_ROOT, RESEARCH_WORKER_TOKEN
from app.core.db import get_db
from app.core.security import current_user
from app.domains.models import Audit, MarketData, Task, User, utcnow
from app.storage.parquet_files import sha256_file

router = APIRouter()
RESEARCH_SYMBOLS = (
    "KORUUSDT", "CLUSDT", "NVDAUSDT", "BTCUSDT", "SKHYNIXUSDT", "SPCXUSDT",
    "SNDKUSDT", "XAUUSDT", "QQQUSDT", "INTCUSDT", "TSLAUSDT", "OPENAIUSDT", "ZHIPUUSDT",
)
RESEARCH_TIMEFRAMES = ("15m", "30m", "1h", "2h", "4h")
RESEARCH_MODELS = ("analysis_dow123", "patchtst", "tft_attention", "tcn")
ARCHIVE_MODELS = (
    *RESEARCH_MODELS, "torch_gru", "pattern_only", "logistic", "random_forest",
    "extra_trees", "hist_gradient", "mlp",
)
RESEARCH_KIND = "RUN_FULL_RESEARCH"


class ResearchBacktestRequest(BaseModel):
    """页面提交的规则策略历史回测范围；不接受路径或执行参数。"""

    model_config = ConfigDict(extra="forbid")
    symbols: list[str] = Field(min_length=1, max_length=13)
    models: list[str] = Field(min_length=1, max_length=4)
    timeframes: list[str] = Field(min_length=1, max_length=5)

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, values):
        """限制为交易所已核验的十三个公开合约标的。"""
        if len(values) != len(set(values)) or any(value not in RESEARCH_SYMBOLS for value in values):
            raise ValueError("标的须来自交易所已核验的回测名单，且不能重复")
        return values

    @field_validator("models")
    @classmethod
    def validate_models(cls, values):
        """限制为研究引擎已登记的模型名称，并拒绝重复运行项。"""
        if len(values) != len(set(values)) or any(value not in RESEARCH_MODELS for value in values):
            raise ValueError("模型列表包含未开放或重复算法；仅支持道氏123、PatchTST、TFT-Attention与TCN")
        return values

    @field_validator("timeframes")
    @classmethod
    def validate_timeframes(cls, values):
        """限制入场周期为研究引擎支持的原生K线周期。"""
        if len(values) != len(set(values)) or any(value not in RESEARCH_TIMEFRAMES for value in values):
            raise ValueError("周期列表包含未支持或重复周期")
        return values


class WorkerProgress(BaseModel):
    """GPU研究进程报告的有限进度信息，不接收路径或用户标识。"""

    model_config = ConfigDict(extra="forbid")
    percent: int = Field(ge=0, le=99)
    phase: str = Field(min_length=1, max_length=40)
    symbol: str | None = None
    timeframe: str | None = None
    model: str | None = None
    fold: int | None = Field(default=None, ge=1, le=10)

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value):
        """拒绝进度事件引用研究范围外的产品标识。"""
        if value is not None and value not in RESEARCH_SYMBOLS:
            raise ValueError("未知进度标的")
        return value


def _worker_authorized(token: str | None):
    """使用常量时间比较验证GPU runner的内部认证令牌。

    Args:
        token: Docker内部请求头中的共享随机值；不写入任务或审计记录。

    Returns:
        认证成功返回None；令牌缺失或不匹配时抛出HTTP 403。
    """
    if not RESEARCH_WORKER_TOKEN or not token or not hmac.compare_digest(token, RESEARCH_WORKER_TOKEN):
        raise HTTPException(403, "研究worker认证失败")


def _owned_task(db: DBSession, task_id: str, owner_id: int):
    """读取当前用户拥有的全模型研究任务，隐藏其他用户任务是否存在。"""
    """读取并验证任务所有权，防止跨用户枚举报告。

    Args:
        db: 当前PostgreSQL请求会话。
        task_id: 由服务端分配的任务UUID。
        owner_id: 当前会话用户的数据库整数ID。

    Returns:
        当前用户拥有的全模型研究任务；不存在或不属于用户时返回HTTP 404。
    """
    task = db.get(Task, task_id)
    if task is None or task.kind != RESEARCH_KIND or task.parameters.get("owner_user_id") != owner_id:
        raise HTTPException(404, "回测任务不存在")
    return task


def _output_directory(task: Task) -> Path:
    """按UUID固定研究报告目录并确认解析路径位于专用运行根目录。"""
    try:
        output_id = str(uuid.UUID(task.parameters["output_id"]))
    except (KeyError, ValueError, TypeError):
        raise HTTPException(409, "回测报告索引无效") from None
    root = RESEARCH_RUNS_ROOT.resolve()
    output = (root / output_id).resolve()
    if output.parent != root:
        raise HTTPException(409, "回测报告路径无效")
    return output


def _archive_directory(archive_id: str) -> Path:
    """Resolve a read-only historical run folder without accepting path traversal.

    Args:
        archive_id: Local directory name returned by the authenticated archive list.

    Returns:
        Existing report directory directly beneath the read-only research runs root.
    """
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", archive_id) or archive_id in {".", ".."}:
        raise HTTPException(404, "历史报告不存在")
    root = (RESEARCH_ROOT / "runs").resolve()
    directory = (root / archive_id).resolve()
    if directory.parent != root or not (directory / "batch_summary.json").is_file():
        raise HTTPException(404, "历史报告不存在")
    return directory


def _task_response(task: Task):
    """生成不泄露内部输出路径的用户任务响应。

    Args:
        task: 已由所有权查询确认属于当前用户的全模型研究任务。

    Returns:
        含任务状态、筛选范围、进度及错误摘要的JSON兼容字典。
    """
    parameters = {key: value for key, value in task.parameters.items()
                  if key not in {"owner_user_id", "output_id"}}
    return {
        "id": task.id,
        "status": task.status,
        "parameters": parameters,
        "progress": task.result.get("progress", {"percent": 0, "phase": "queued"}),
        "result": task.result.get("report", {}),
        "error": task.result.get("error"),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


@router.post("/api/research/backtests", status_code=202)
def create_research_backtest(
    payload: ResearchBacktestRequest,
    user: User = Depends(current_user),
    db: DBSession = Depends(get_db),
):
    """从系统页面创建完整模型×标的验证任务，不在HTTP请求中运行训练。

    Args:
        payload: 经过白名单校验的标的、模型和入场周期集合。
        user: 已认证的当前用户；所有权写入服务端任务参数。
        db: 本次请求的SQLAlchemy会话。

    Returns:
        已持久化的排队任务ID和状态；GPU worker稍后领取并报告进度。
    """
    missing = [symbol for symbol in payload.symbols if not _has_local_source(symbol)]
    if missing:
        raise HTTPException(422, "缺少已验证的本地回测数据：" + ", ".join(missing))
    active = db.scalars(select(Task).where(
        Task.kind == RESEARCH_KIND,
        Task.status.in_(("QUEUED", "RUNNING", "RETRYING")),
    )).all()
    if any(item.parameters.get("owner_user_id") == user.id for item in active):
        raise HTTPException(409, "你已有整套模型回测在排队或运行")
    task_id = str(uuid.uuid4())
    parameters = {
        "owner_user_id": user.id,
        "output_id": task_id,
        "symbols": payload.symbols,
        "models": payload.models,
        "timeframes": payload.timeframes,
    }
    task = Task(id=task_id, kind=RESEARCH_KIND, parameters=parameters,
                status="QUEUED", result={"progress": {"percent": 0, "phase": "queued"}})
    db.add(task)
    db.add(Audit(actor=user.username, action="FULL_RESEARCH_QUEUED",
                 detail={"task_id": task_id, "symbols": payload.symbols,
                         "models": payload.models, "timeframes": payload.timeframes}))
    db.commit()
    return {"id": task.id, "status": task.status}


def _research_input_status(symbol: str) -> dict:
    """检查历史研究所需行情文件、来源证明和冻结验证边界。

    Args:
        symbol: 已核验的公开合约代码之一。

    Returns:
        含就绪状态和缺失项的诊断字典；不发起网络请求或改写冻结文件。
    """
    data = RESEARCH_ROOT / "data" / f"{symbol.lower()}_90d_connector"
    baseline = RESEARCH_ROOT / "runs" / f"{symbol.lower()}_1h_15m_20261017" / "report.json"
    plan = data / "evaluation_plan.json"
    if plan.is_file():
        baseline = plan
    filenames = {
        "bars.csv": "交易价K线",
        "funding.csv": "资金费率",
        "mark_bars.csv": "标记价K线",
        "source.json": "行情来源证明",
    }
    missing = [label for name, label in filenames.items() if not (data / name).is_file()]
    if not baseline.is_file():
        missing.append("冻结评估区间")
    if missing:
        return {"ready": False, "status": "MISSING_INPUTS", "requirements": missing,
                "reason": "缺少" + "、".join(missing)}
    bars, funding, mark = (data / name for name in ("bars.csv", "funding.csv", "mark_bars.csv"))
    try:
        source = json.loads((data / "source.json").read_text(encoding="utf-8"))
        plan = json.loads(baseline.read_text(encoding="utf-8"))
        digest = hashlib.sha256(bars.read_bytes() + funding.read_bytes()).hexdigest()
        if source.get("symbol") != symbol or source.get("synthetic") is not False:
            return {"ready": False, "status": "INVALID_SOURCE", "requirements": ["真实来源与标的匹配"],
                    "reason": "行情来源证明与标的不匹配或标注为合成数据"}
        if plan.get("dataset_sha256") != digest:
            return {"ready": False, "status": "FROZEN_PLAN_MISMATCH", "requirements": ["匹配当前数据的冻结评估区间"],
                    "reason": "冻结评估区间与当前行情数据哈希不匹配"}
        holdout = plan.get("holdout_period_ms")
        if not isinstance(holdout, list) or len(holdout) != 2 or not all(isinstance(value, int) for value in holdout) or holdout[1] <= holdout[0]:
            return {"ready": False, "status": "INVALID_EVALUATION_WINDOW", "requirements": ["有效冻结评估区间"],
                    "reason": "冻结评估区间缺失或格式无效"}
        return {"ready": True, "status": "READY", "requirements": [], "reason": "完整研究行情包与冻结评估区间已校验"}
    except (OSError, ValueError, TypeError, AttributeError):
        return {"ready": False, "status": "INVALID_INPUTS", "requirements": ["有效行情来源和评估文件"],
                "reason": "本地研究文件无法读取或格式无效"}


def _has_local_source(symbol: str) -> bool:
    """确认本机具备完整、可审计的多模型研究数据输入。

    Args:
        symbol: 已核验的公开合约代码之一。

    Returns:
        存在真实交易价、标记价、资金费率、来源证明及匹配冻结评估边界时返回True。
    """
    return _research_input_status(symbol)["ready"]


def _market_cache_status(db: DBSession, symbol: str) -> dict:
    """报告15分钟Parquet行情缓存是否存在并通过SHA256校验。

    Args:
        db: 当前认证请求使用的SQLAlchemy数据库会话。
        symbol: 已核验的Binance USD-M合约代码。

    Returns:
        本地缓存的存在状态、已验证行数及最后收盘UTC毫秒；不请求上游。
    """
    record = db.scalar(select(MarketData).where(
        MarketData.asset_id == f"binance_usdm:{symbol}", MarketData.interval == "15m"))
    if record is None:
        return {"available": False, "verified": False, "rows": 0, "last_close_ms": None}
    root = DATA_ROOT.resolve()
    path = (root / record.path).resolve()
    if path.parent != root or not path.is_file():
        return {"available": True, "verified": False, "rows": 0, "last_close_ms": None}
    try:
        valid = sha256_file(path) == record.sha256
    except OSError:
        valid = False
    return {"available": True, "verified": valid,
            "rows": int(record.row_count) if valid else 0,
            "last_close_ms": int(record.last_close_ms) if valid else None}


@router.get("/api/research/backtests/coverage")
def research_backtest_coverage(user: User = Depends(current_user), db: DBSession = Depends(get_db)):
    """Return exchange-verified contract coverage and local frozen-plan readiness.

    Args:
        user: Authenticated user; coverage itself contains no private account data.
        db: 当前请求的SQLAlchemy会话，用于读取经过本地哈希验证的K线缓存索引。

    Returns:
        One row per verified contract with separate recent-cache and full-research readiness.
        This endpoint performs no network requests and never fabricates coverage.
    """
    del user
    assets = []
    for symbol in RESEARCH_SYMBOLS:
        research = _research_input_status(symbol)
        assets.append({"symbol": symbol, "ready": research["ready"],
                       "status": research["status"], "reason": research["reason"],
                       "requirements": research["requirements"],
                       "cached_15m": _market_cache_status(db, symbol)})
    return {"assets": assets, "models": list(RESEARCH_MODELS)}


@router.get("/api/research/backtests")
def list_research_backtests(user: User = Depends(current_user), db: DBSession = Depends(get_db)):
    """列出当前用户任务及本机既有批次的只读报告索引。

    Args:
        user: 已认证用户；只返回其本人创建的运行任务。
        db: 本次API请求的SQLAlchemy会话。

    Returns:
        最近任务与可安全读取的归档报告列表，不包含报告文件系统路径。
    """
    tasks = db.scalars(select(Task).where(Task.kind == RESEARCH_KIND)
                       .order_by(Task.created_at.desc()).limit(100)).all()
    response = [_task_response(task) for task in tasks
                if task.parameters.get("owner_user_id") == user.id]
    archive_root = (RESEARCH_ROOT / "runs").resolve()
    for directory in archive_root.iterdir() if archive_root.is_dir() else ():
        summary_path = directory / "batch_summary.json"
        if (not directory.is_dir() or directory.resolve().parent != archive_root
                or not summary_path.is_file()):
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            response.append({
                "id": f"archive:{directory.name}", "status": "SUCCEEDED", "kind": "ARCHIVED_REPORT",
                "parameters": {"symbols": summary.get("symbols", []), "models": list(summary.get("models", {})),
                               "timeframes": []},
                "progress": {"percent": 100, "phase": "archived"}, "error": None,
                "created_at": datetime.fromtimestamp(directory.stat().st_mtime, timezone.utc).isoformat(),
            })
        except (OSError, ValueError, TypeError):
            continue
    return sorted(response, key=lambda item: str(item.get("created_at", "")), reverse=True)[:100]


@router.get("/api/research/backtests/{task_id}/summary")
def research_summary(task_id: str, user: User = Depends(current_user), db: DBSession = Depends(get_db)):
    """读取已完成全模型报告的全局模型/标的目录摘要。"""
    task = _owned_task(db, task_id, user.id)
    if task.status != "SUCCEEDED":
        raise HTTPException(409, "总体报告将在整套回测完成后提供")
    path = _output_directory(task) / "batch_summary.json"
    if not path.is_file():
        raise HTTPException(404, "总体报告文件不存在")
    report = json.loads(path.read_text(encoding="utf-8"))
    return {"models": list(report.get("models", {})), "symbols": report.get("symbols", []),
            "configurations": report.get("configurations", 0),
            "sources": [{key: source.get(key) for key in ("symbol", "status", "configurations", "selected", "holdout")}
                        for source in report.get("sources", [])],
            "independent_accounts": report.get("independent_accounts", True),
            "real_orders_enabled": False}


@router.get("/api/research/archives/{archive_id}/summary")
def archived_research_summary(archive_id: str, user: User = Depends(current_user)):
    """读取本地已有批次的总体摘要，不写入或重新运行历史产物。

    Args:
        archive_id: 已验证目录名，不接受绝对路径或目录穿越片段。
        user: 已认证用户；归档源只读且来自本机研究产物。

    Returns:
        已登记模型、标的、配置数量及脱敏来源摘要。
    """
    path = _archive_directory(archive_id) / "batch_summary.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    return {"models": list(report.get("models", {})), "symbols": report.get("symbols", []),
            "configurations": report.get("configurations", 0),
            "sources": [{key: source.get(key) for key in ("symbol", "status", "configurations", "selected", "holdout")}
                        for source in report.get("sources", [])],
            "independent_accounts": report.get("independent_accounts", True), "real_orders_enabled": False}


@router.get("/api/research/backtests/{task_id}/assets/{symbol}/models/{model}")
def research_model_report(
    task_id: str,
    symbol: str,
    model: str,
    timeframe: str = Query(default="15m", pattern="^(15m|30m|1h|2h|4h)$"),
    exit_mode: str = Query(default="swing_percent", pattern="^(swing_percent|fixed_rr|atr_trailing|three_bar_breakout|three_bar_trailing|time_exit|fixed_three_bar_target)$"),
    user: User = Depends(current_user),
    db: DBSession = Depends(get_db),
):
    """读取用户任务内指定模型和标的的验证报告。

    Args:
        task_id: 任务UUID，服务端会核对会话用户所有权。
        symbol: 白名单合约标识。
        model: 白名单研究模型标识。
        timeframe: 原生入场K线周期。
        exit_mode: 预定义模拟退出模式。
        user: 当前认证用户。
        db: 当前请求数据库会话。

    Returns:
        各退出排名、选定配置权益曲线及逐笔模拟操作。
    """
    task = _owned_task(db, task_id, user.id)
    if task.status != "SUCCEEDED":
        raise HTTPException(409, "该模型报告尚未完成")
    if symbol not in task.parameters["symbols"] or model not in task.parameters["models"]:
        raise HTTPException(404, "回测报告不存在")
    report_dir = _output_directory(task) / "models" / model / "symbols" / symbol
    return _read_model_report(report_dir, symbol, model, timeframe, exit_mode)


@router.get("/api/research/archives/{archive_id}/assets/{symbol}/models/{model}")
def archived_model_report(
    archive_id: str, symbol: str, model: str,
    timeframe: str = Query(default="15m", pattern="^(15m|30m|1h|2h|4h)$"),
    exit_mode: str = Query(default="swing_percent", pattern="^(swing_percent|fixed_rr|atr_trailing|three_bar_breakout|three_bar_trailing|time_exit|fixed_three_bar_target)$"),
    user: User = Depends(current_user),
):
    """读取已有只读批次中的单模型、单标的报告。

    Args:
        archive_id: 归档目录名；由服务器约束在runs的直接子目录。
        symbol: 已登记优先标的代码。
        model: 已登记模型代码。
        timeframe: 原生入场K线周期。
        exit_mode: 预定义模拟退出模式。
        user: 已认证用户。

    Returns:
        排名、权益曲线和对应的历史模拟成交；不改写归档文件。
    """
    if symbol not in RESEARCH_SYMBOLS or model not in ARCHIVE_MODELS:
        raise HTTPException(404, "回测报告不存在")
    report_dir = _archive_directory(archive_id) / "models" / model / "symbols" / symbol
    return _read_model_report(report_dir, symbol, model, timeframe, exit_mode)


def _read_model_report(report_dir: Path, symbol: str, model: str, timeframe: str, exit_mode: str):
    """读取固定报告目录内的有限字段、曲线和所选独立账户交易明细。

    Args:
        report_dir: 服务端解析出的模型/标的目录，不能来自请求路径。
        symbol: 已登记标的代码。
        model: 已注册模型代码。
        timeframe: 已白名单验证的原生入场周期。
        exit_mode: 已白名单验证的退出策略代码。

    Returns:
        页面所需的排名、验证曲线、持仓交易和来源哈希。
    """
    report_path = report_dir / "report.json"
    operations_path = report_dir / "operation_summary.json"
    curves_path = report_dir / "validation_curves.json"
    if not report_path.is_file() or not operations_path.is_file():
        raise HTTPException(404, "指定模型/标的报告不存在")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    operation_summary = json.loads(operations_path.read_text(encoding="utf-8"))
    key = f"{timeframe}|{model}|{exit_mode}"
    curves = json.loads(curves_path.read_text(encoding="utf-8")) if curves_path.is_file() else {}
    curve = curves.get(key, {})
    trades_path = report_dir / "trades.csv"
    operations = []
    if trades_path.is_file():
        with trades_path.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                if row.get("config") == key:
                    operations.append({name: row.get(name) for name in (
                        "position_id", "direction", "entry_time", "exit_time", "holding_hours",
                        "entry_price", "exit_price", "net_pnl", "exit_reason", "partial_exit_count",
                    )})
    short_count = sum(1 for row in operations if row.get("direction") in ("-1", -1))
    return {
        "symbol": symbol,
        "model": model,
        "status": report.get("status", "UNKNOWN"),
        "dataset_sha256": report.get("dataset_sha256"),
        "selected": report.get("selected"),
        "holdout": report.get("holdout"),
        "holdout_double_cost": report.get("holdout_double_cost"),
        "rankings": [{name: row.get(name) for name in (
            "timeframe", "model", "exit_mode", "net_return", "max_drawdown",
            "entries", "liquidations", "validation_score", "eligible", "rejection_reasons",
            "holding_hours_mean", "holding_hours_median", "holding_hours_max",
        )} for row in report.get("ranking", [])],
        "account_summary": [{name: row.get(name) for name in (
            "timeframe", "exit_mode", "round_trips", "open_operations", "close_operations",
            "partial_exit_operations", "net_return", "win_rate", "max_drawdown",
            "holding_hours_mean", "holding_hours_median", "holding_hours_max",
        )} for row in operation_summary.get("accounts", {}).values()],
        "curve": [[int(timestamp), float(value)] for timestamp, value in curve.items()],
        "operations": operations,
        "short_operations": short_count,
        "long_operations": len(operations) - short_count,
        "curve_key": key,
        "actual_email_sent": False,
        "real_orders_enabled": False,
    }


@router.post("/api/internal/research/claim")
def claim_research_task(
    x_research_token: str | None = Header(default=None), db: DBSession = Depends(get_db),
):
    """由Docker内部GPU runner原子领取最早的排队任务。

    Args:
        x_research_token: 内部共享令牌，仅容器网络传输。
        db: PostgreSQL会话；行锁避免多个runner重复领取。

    Returns:
        单个任务的固定UUID/白名单参数，或`task: null`表示队列为空。
    """
    _worker_authorized(x_research_token)
    task = db.scalar(select(Task).where(Task.kind == RESEARCH_KIND, Task.status == "QUEUED")
                     .order_by(Task.created_at).with_for_update(skip_locked=True).limit(1))
    if task is None:
        return {"task": None}
    task.status = "RUNNING"
    task.attempts += 1
    task.result = {"progress": {"percent": 0, "phase": "starting"}}
    task.updated_at = utcnow()
    db.commit()
    return {"task": {"id": task.id, "output_id": task.parameters["output_id"],
                      "symbols": task.parameters["symbols"], "models": task.parameters["models"],
                      "timeframes": task.parameters["timeframes"]}}


@router.post("/api/internal/research/{task_id}/progress")
def update_research_progress(
    task_id: str,
    progress: WorkerProgress,
    x_research_token: str | None = Header(default=None),
    db: DBSession = Depends(get_db),
):
    """持久化有限范围的GPU研究进度，供登录页面轮询。

    Args:
        task_id: 当前正在运行的任务UUID。
        progress: 已验证的百分比、标的、周期、模型和验证折信息。
        x_research_token: 内部runner共享认证令牌。
        db: PostgreSQL会话。

    Returns:
        固定成功状态；不接受由worker指定的输出路径。
    """
    _worker_authorized(x_research_token)
    task = db.get(Task, task_id)
    if task is None or task.kind != RESEARCH_KIND or task.status != "RUNNING":
        raise HTTPException(404, "活动回测任务不存在")
    task.result = {"progress": progress.model_dump(mode="json")}
    task.updated_at = utcnow()
    db.commit()
    return {"status": "ok"}


@router.post("/api/internal/research/{task_id}/complete")
def complete_research_task(
    task_id: str,
    x_research_token: str | None = Header(default=None),
    db: DBSession = Depends(get_db),
):
    """仅在总体批次摘要已落盘后结束GPU任务。

    Args:
        x_research_token: 内部runner共享认证令牌。
        db: PostgreSQL会话。

    Returns:
        终态`SUCCEEDED`及100%进度；缺少摘要时拒绝完成。
    """
    _worker_authorized(x_research_token)
    task = db.get(Task, task_id)
    if task is None or task.kind != RESEARCH_KIND or task.status != "RUNNING":
        raise HTTPException(404, "活动回测任务不存在")
    summary = _output_directory(task) / "batch_summary.json"
    if not summary.is_file():
        raise HTTPException(409, "回测进程尚未生成总体报告")
    task.status = "SUCCEEDED"
    task.result = {"progress": {"percent": 100, "phase": "completed"},
                   "report": {"available": True}}
    task.updated_at = utcnow()
    db.commit()
    return {"status": task.status}


@router.post("/api/internal/research/{task_id}/fail")
def fail_research_task(
    task_id: str,
    message: str = Query(default="回测执行失败", max_length=300),
    x_research_token: str | None = Header(default=None),
    db: DBSession = Depends(get_db),
):
    """持久记录GPU任务的简短错误并保留已生成报告。

    Args:
        task_id: 当前运行任务UUID。
        message: 最长300字符的内部失败摘要，不作为HTML执行。
        x_research_token: 内部runner共享认证令牌。
        db: PostgreSQL会话。

    Returns:
        `FAILED`状态；数据文件不删除或覆盖。
    """
    _worker_authorized(x_research_token)
    task = db.get(Task, task_id)
    if task is None or task.kind != RESEARCH_KIND or task.status != "RUNNING":
        raise HTTPException(404, "活动回测任务不存在")
    task.status = "FAILED"
    task.result = {"progress": task.result.get("progress", {}), "error": message[:300]}
    task.updated_at = utcnow()
    db.commit()
    return {"status": task.status}

"""NVIDIA-container worker for authenticated, local full-research jobs."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
API_URL = os.environ.get("RESEARCH_API_URL", "http://api:8000").rstrip("/")
TOKEN = os.environ.get("RESEARCH_WORKER_TOKEN", "")
PYTHON = "/opt/quant-venv/bin/python"


def api(client: httpx.Client, path: str, body: dict | None = None) -> dict:
    """Call the private same-network research API with the worker credential.

    Args:
        client: HTTP client with bounded connect/read timeouts.
        path: Fixed internal API path; callers do not supply a host.
        body: Optional JSON body containing only task progress or lifecycle data.

    Returns:
        Decoded JSON response or raises for transport/HTTP failures.
    """
    response = client.post(API_URL + path, json=body, headers={"X-Research-Token": TOKEN})
    response.raise_for_status()
    return response.json()


def progress_callback(client: httpx.Client, task_id: str, symbol_index: int,
                      symbol_count: int, event: dict) -> None:
    """Map per-symbol engine progress into a durable whole-task percentage.

    Args:
        client: Authenticated internal API client.
        task_id: UUID assigned by the user-facing task API.
        symbol_index: Zero-based index of the currently running symbol.
        symbol_count: Number of selected symbols in the immutable task request.
        event: Structured progress event emitted after real research work units.

    Returns:
        None; persists progress capped below 100 until report completion.
    """
    percent = min(99, int((symbol_index + int(event.get("percent", 0)) / 100) * 100 / symbol_count))
    api(client, f"/api/internal/research/{quote(task_id)}/progress", {
        "percent": percent,
        "phase": str(event.get("phase", "research"))[:40],
        "symbol": event.get("symbol"),
        "timeframe": event.get("timeframe"),
        "model": event.get("model"),
        "fold": event.get("fold"),
    })


def run_task(client: httpx.Client, task: dict) -> None:
    """Run one queued full backtest and persist success or a bounded failure.

    Args:
        client: Authenticated internal API client.
        task: Server-validated UUID, output UUID, symbols, models and timeframes.

    Returns:
        None; report files remain under the private runtime report directory.
    """
    task_id = task["id"]
    output_id = task["output_id"]
    symbols = task["symbols"]
    output = Path(".runtime/research-runs") / output_id
    base_command = [PYTHON, "run.py", "--out", str(output)]
    try:
        command = base_command + ["--symbols", *symbols, "--models", *task["models"],
                                  "--timeframes", *task["timeframes"]]
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env={**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                 "QUANT_MAIL_MODE": "simulate", "PYTHONUNBUFFERED": "1"},
        )
        log_path = Path(".runtime/research-runs") / f"{output_id}.log"
        with log_path.open("w", encoding="utf-8") as log:
            assert process.stdout is not None
            for line in process.stdout:
                log.write(line)
                log.flush()
                if line.startswith("RESEARCH_PROGRESS "):
                    try:
                        event = json.loads(line[len("RESEARCH_PROGRESS "):])
                        progress_callback(client, task_id, symbols.index(event["symbol"]), len(symbols), event)
                    except (ValueError, KeyError, httpx.HTTPError):
                        pass
        if process.wait():
            raise RuntimeError("整套回测进程异常退出；本地运行日志保留供排查")
        api(client, f"/api/internal/research/{quote(task_id)}/complete")
    except Exception as error:
        message = str(error).replace("\n", " ")[:250] or "研究执行失败"
        try:
            client.post(API_URL + f"/api/internal/research/{quote(task_id)}/fail",
                        params={"message": message}, headers={"X-Research-Token": TOKEN}).raise_for_status()
        except httpx.HTTPError:
            print("Failed to persist research task failure", file=sys.stderr, flush=True)
        print(f"Research task failed: {message}", file=sys.stderr, flush=True)


def main() -> int:
    """Continuously claim GPU research jobs until the container is stopped.

    Returns:
        Zero after a clean process stop; task failures are saved and the worker continues.
    """
    if not TOKEN:
        print("RESEARCH_WORKER_TOKEN is required", file=sys.stderr)
        return 2
    with httpx.Client(timeout=httpx.Timeout(10, connect=3)) as client:
        while True:
            try:
                claim = api(client, "/api/internal/research/claim")
                task = claim.get("task")
                if task:
                    run_task(client, task)
                else:
                    time.sleep(2)
            except httpx.HTTPError:
                time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())

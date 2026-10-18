"""隔离验证多模拟回测配置账号的资金精度与用户边界。"""

from decimal import Decimal
import time

from app.core.db import SessionLocal
from app.domains.models import SimulationAccount, User


def _payload(name="conservative", capital="10000.12345678"):
    """构造含归一化理论权重的回测配置账号请求。

    Args:
        name: 当前用户内唯一的显示名。
        capital: USDT起始资本的十进制字符串，不先转换成二进制浮点。

    Returns:
        可供API测试发送的JSON安全配置。
    """
    return {
        "name": name,
        "initial_equity": capital,
        "base_currency": "USDT",
        "configuration": {
            "asset_ids": ["binance_usdm:BTCUSDT"],
            "intervals": ["15m", "1h", "4h"],
            "weights": {
                "trend": "0.40",
                "dow123": "0.25",
                "wave": "0.10",
                "divergence": "0.15",
                "reversal_patterns": "0.10",
                "deep_model": "0",
            },
            "entry_threshold": "0.70",
            "max_leverage": "3",
            "max_margin_fraction": "0.10",
            "max_holding_hours": 168,
            "exit_mode": "three_bar_trailing",
            "deep_model_version": None,
        },
    }


def test_simulation_accounts_preserve_decimal_capital_and_policy_revision(logged_in):
    """验证多个账号可持久配置、更新产生新版本摘要并隔离名称。

    Args:
        logged_in: 已认证且携带会话CSRF令牌的API测试客户端。

    Returns:
        无返回值；资本精度、配置哈希、修订号或账号列表错误时测试失败。
    """
    first = logged_in.post("/api/simulation/accounts", json=_payload())
    second = logged_in.post("/api/simulation/accounts", json=_payload("aggressive", "25000"))
    assert first.status_code == 201
    assert second.status_code == 201
    created = first.json()
    assert created["initial_equity"] == "10000.12345678"
    assert len(created["configuration_sha256"]) == 64
    assert created["research_only"] is True
    assert created["backtest_implemented"] is False

    changed = _payload("conservative", "9000.25")
    changed["configuration"]["max_leverage"] = "2"
    updated = logged_in.put(f"/api/simulation/accounts/{created['id']}", json=changed)
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert updated.json()["configuration_sha256"] != created["configuration_sha256"]
    assert Decimal(updated.json()["initial_equity"]) == Decimal("9000.25")
    assert len(logged_in.get("/api/simulation/accounts").json()) == 2


def test_simulation_account_rejects_unsafe_policy_and_missing_model_binding(logged_in):
    """验证理论权重和、杠杆上限、周期及深度模型版本必须显式有效。

    Args:
        logged_in: 已认证且携带会话CSRF令牌的API测试客户端。

    Returns:
        无返回值；越界策略被接收时测试失败。
    """
    invalid_weight = _payload("bad-weight")
    invalid_weight["configuration"]["weights"]["trend"] = "0.9"
    assert logged_in.post("/api/simulation/accounts", json=invalid_weight).status_code == 422

    invalid_leverage = _payload("bad-leverage")
    invalid_leverage["configuration"]["max_leverage"] = "21"
    assert logged_in.post("/api/simulation/accounts", json=invalid_leverage).status_code == 422

    unbound_model = _payload("unbound-model")
    unbound_model["configuration"]["weights"]["deep_model"] = "0.1"
    unbound_model["configuration"]["weights"]["trend"] = "0.3"
    assert logged_in.post("/api/simulation/accounts", json=unbound_model).status_code == 422

    unverified_asset = _payload("unverified-asset")
    unverified_asset["configuration"]["asset_ids"] = ["us:NVDA"]
    assert logged_in.post("/api/simulation/accounts", json=unverified_asset).status_code == 422

    assert logged_in.post(
        "/api/simulation/accounts", json=_payload("csrf"), headers={"X-CSRF-Token": "invalid"}
    ).status_code == 403


def test_simulation_account_update_cannot_access_another_owner(logged_in):
    """验证配置账号的更新查询同时要求账号ID和当前所有者。

    Args:
        logged_in: 当前测试用户的已认证API客户端。

    Returns:
        无返回值；跨所有者更新不得暴露存在性或修改内容。
    """
    with SessionLocal() as db:
        owner = User(username="second_test_admin", password_hash="unused", role="admin")
        db.add(owner)
        db.flush()
        other = SimulationAccount(
            id="other-owner-account",
            owner_user_id=owner.id,
            name="private",
            initial_equity="5000",
            base_currency="USDT",
            configuration=_payload()["configuration"],
            configuration_sha256="a" * 64,
            revision=1,
        )
        db.add(other)
        db.commit()

    response = logged_in.put("/api/simulation/accounts/other-owner-account", json=_payload("overwrite"))
    assert response.status_code == 404
    now_ms = int(time.time() * 1000)
    data_request = {"start_ms": now_ms - 2 * 86_400_000, "end_ms": now_ms - 86_400_000}
    assert logged_in.post(
        "/api/simulation/accounts/other-owner-account/datasets", json=data_request
    ).status_code == 404
    assert len(logged_in.get("/api/simulation/accounts").json()) == 0


def test_simulation_account_dataset_request_queues_only_configured_public_data(logged_in):
    """验证显式点击按配置标的/周期排队三年内标记价和资金费率归档。

    Args:
        logged_in: 当前用户已认证且请求带有有效CSRF令牌的API客户端。

    Returns:
        无返回值；无效范围、未配置资产或队列任务数量错误时测试失败。
    """
    created = logged_in.post("/api/simulation/accounts", json=_payload()).json()
    now_ms = int(time.time() * 1000)
    request = {"start_ms": now_ms - 2 * 86_400_000, "end_ms": now_ms - 86_400_000}
    first = logged_in.post(f"/api/simulation/accounts/{created['id']}/datasets", json=request)
    assert first.status_code == 202
    jobs = first.json()["tasks"]
    assert len(jobs) == 3
    assert {job["interval"] for job in jobs} == {"15m", "1h", "4h"}
    assert all(job["asset_id"] == "binance_usdm:BTCUSDT" for job in jobs)
    assert all(job["status"] == "QUEUED" for job in jobs)

    repeated = logged_in.post(f"/api/simulation/accounts/{created['id']}/datasets", json=request)
    assert [job["id"] for job in repeated.json()["tasks"]] == [job["id"] for job in jobs]
    invalid_range = {"start_ms": 0, "end_ms": now_ms}
    assert logged_in.post(
        f"/api/simulation/accounts/{created['id']}/datasets", json=invalid_range
    ).status_code == 422

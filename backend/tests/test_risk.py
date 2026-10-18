"""研究产物与预算接口的认证、边界和缺失状态测试。"""

import json
from app.api import risk


def test_risk_requires_auth_and_csrf(client, logged_in):
    """历史研究需登录，预算POST同样必须携带有效CSRF。"""
    logged_in.headers.pop("X-CSRF-Token")
    assert logged_in.post("/api/risk/budget", json={}).status_code == 403
    logged_in.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": logged_in.get("/api/auth/me").json()["csrf_token"]},
    )
    assert client.get("/api/risk/research").status_code == 401


def test_budget_limits_and_volatility(logged_in):
    """越界杠杆被服务器拒绝；有效输入波动增加会降低杠杆。"""
    body = dict(score=90, daily_volatility=0.01, stop_fraction=0.02, direction=1)
    low = logged_in.post("/api/risk/budget", json=body)
    assert low.status_code == 200
    high = logged_in.post(
        "/api/risk/budget", json={**body, "daily_volatility": 0.04}
    ).json()
    assert high["leverage"] < low.json()["leverage"]
    assert high["estimated_stop_loss_fraction"] <= 0.01
    assert high["real_orders_enabled"] is False
    for override in (
        {"max_leverage": 21},
        {"risk_percent": 3},
        {"direction": 2},
        {"score": 101},
        {"unknown": 1},
    ):
        assert (
            logged_in.post("/api/risk/budget", json={**body, **override}).status_code
            == 422
        )


def test_report_missing_and_valid(logged_in, monkeypatch, tmp_path):
    """无本机权重产物返回503；读取固定路径的真实报告，不允许用户路径。"""
    monkeypatch.setattr(risk, "RESEARCH_ROOT", tmp_path)
    assert logged_in.get("/api/risk/research").status_code == 503
    path = tmp_path / "runs/risk_latest/report.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(dict(schema_version=1, real_orders_enabled=False, results=[]))
    )
    assert logged_in.get("/api/risk/research").json()["results"] == []
    path.write_text("invalid")
    assert logged_in.get("/api/risk/research").status_code == 503

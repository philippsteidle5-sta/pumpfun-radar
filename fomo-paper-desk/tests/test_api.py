"""API auth and the routes this chunk actually serves."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from desk.api import create_app
from desk.clock import ManualClock
from desk.db import Database
from desk.runtime import Desk

TOKEN = "CANARY_APP_TOKEN_7f3a"
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
MINT = "DKtofLM6bhb54GQy5YFSWQjQmGiZqAkjzkQJ1LTopump"


def client(tmp_path):
    desk = Desk(Database(str(tmp_path / "api.sqlite")), clock=ManualClock(NOW), secrets=[])
    desk.open_night()
    desk.db.upsert_coin(mint=MINT, name="Diamond Hands", symbol="HODL", created_at="2026-11-07T12:00:00.000000Z")
    app = create_app(desk, TOKEN)
    return TestClient(app), desk


def test_every_route_requires_bearer_and_hides_the_token(tmp_path):
    http, _desk = client(tmp_path)
    routes = [
        ("GET", "/api/state"),
        ("GET", "/api/chart?range=LIVE"),
        ("GET", "/api/activity"),
        ("GET", f"/api/coin/{MINT}"),
        ("GET", f"/api/audit/{MINT}"),
        ("GET", "/api/agents/scanner/decisions"),
        ("GET", "/api/account"),
        ("GET", "/api/settings"),
        ("GET", "/api/stream"),
    ]
    for method, path in routes:
        bare = http.request(method, path)
        assert bare.status_code == 401, path
        assert TOKEN not in bare.text
        queried = http.request(method, path, params={"token": TOKEN})
        assert queried.status_code == 401
        assert TOKEN not in queried.text
        ok = http.request(method, path.split("?")[0], headers={"Authorization": f"Bearer {TOKEN}"}, params={"range": "LIVE"} if "chart" in path else None)
        assert ok.status_code == 200, path
        assert TOKEN not in ok.text


def test_settings_risk_pause_and_live_block(tmp_path):
    http, desk = client(tmp_path)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    bad = http.post("/api/settings", headers=auth, json={"changes": {"risk.stop_pct": 99}})
    assert bad.status_code == 422
    assert "min" in bad.json() and "max" in bad.json()
    assert TOKEN not in bad.text
    changed = http.post("/api/settings", headers=auth, json={"changes": {"risk.stop_pct": -8}, "who": "owner"})
    assert changed.status_code == 200
    assert desk.settings()["risk.stop_pct"] == __import__("decimal").Decimal("8")
    paused = http.post("/api/agents/scanner/pause", headers=auth)
    assert paused.status_code == 200
    assert http.post("/api/agents/scanner/resume", headers=auth).status_code == 200
    blocked = http.post("/api/mode", headers=auth, json={"mode": "LIVE", "confirm": "LIVE"})
    assert blocked.status_code == 422
    assert blocked.json()["detail"].startswith("LIVE blocked")
    account = http.get("/api/account", headers=auth).json()
    assert account["profile_url"].endswith("Staticroom?source=share_link&r=savipww")
    assert account["effective_fee_discount"] == "0"
    assert "signup_url" in account
    stored = http.post(
        "/api/push",
        headers=auth,
        json={"endpoint": "https://push.example.test/sub", "p256dh": "abc123456789abcd", "auth": "auth1234"},
    )
    assert stored.status_code == 200
    assert stored.json()["sent"] is False

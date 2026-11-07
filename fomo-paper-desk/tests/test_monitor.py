"""Monitor, loopback auth, and a fixture-fed historical probe. No live network."""

import json
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import uvicorn

from desk.api import create_app
from desk.clock import ManualClock
from desk.db import Database
from desk.links import coin_url
from desk.live import subscription_payload
from desk.main import main
from desk.runtime import Desk
from desk.sources import MapSources, rpc_key
from desk.fields import TOKEN_2022_PROGRAM, TOKEN_PROGRAM, program_account_request

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "public-market-sources"
MINT = "DKtofLM6bhb54GQy5YFSWQjQmGiZqAkjzkQJ1LTopump"
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
TOKEN = "loopback-monitor-token"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


def _sources() -> MapSources:
    account = _load("rpc_getAccountInfo.json")
    report = _load("rugcheck_report.json")
    graph = _load("rugcheck_graph.json")
    pair = _load("dex_tokens.json")[0]
    classic = _load("rpc_program_classic.json")
    tiny = {"jsonrpc": "2.0", "result": [], "id": 1}
    rpc = {
        rpc_key("getAccountInfo", [MINT, {"encoding": "jsonParsed"}]): account,
        rpc_key("getProgramAccounts", program_account_request(TOKEN_PROGRAM, MINT)["params"]): classic,
        rpc_key("getProgramAccounts", program_account_request(TOKEN_2022_PROGRAM, MINT)["params"]): tiny,
    }
    return MapSources(dex={MINT: pair}, reports={MINT: report}, graphs={MINT: graph}, rpc=rpc)


def _desk(tmp_path):
    desk = Desk(Database(str(tmp_path / "desk.sqlite")), clock=ManualClock(NOW), sources=_sources())
    desk.market_data = "injected"
    desk.source_label = "injected"
    desk.open_night()
    return desk


def test_subscription_is_migration_only():
    assert subscription_payload() == {"method": "subscribeMigration"}


def test_page_has_no_token_and_keeps_fragment_local(tmp_path):
    html = (ROOT / "desk" / "static" / "monitor.html").read_text()
    script = (ROOT / "desk" / "static" / "monitor.js").read_text()
    assert "APP_TOKEN" not in html
    assert TOKEN not in html + script
    assert "localStorage" in script
    assert "#t=" in script or "t=" in script
    assert "replaceState" in script
    assert "Authorization" in script
    assert "api.telegram.org" not in html + script


def test_existing_database_is_kept(tmp_path):
    path = tmp_path / "kept.sqlite"
    first = Database(str(path))
    first.update_settings({"scanner.poll_s": 15}, "tester", NOW)
    first.close()
    second = Database(str(path))
    assert second.get_setting("scanner.poll_s") == 15
    assert second.get_setting("desk.mode") == "PAPER"
    second.close()


def test_serve_without_token_exits(monkeypatch):
    monkeypatch.delenv("APP_TOKEN", raising=False)
    assert main(["--serve", "--db", "var/should-not-matter.sqlite"]) == 2


def test_historical_probe_shows_real_fixture_rejection(tmp_path):
    desk = _desk(tmp_path)
    result = __import__("asyncio").run(desk.probe_historical(MINT, budget_s=30))
    assert result["sized"] is False
    assert result["on_desk"] is False
    assert desk.db.position_by_mint(MINT) is None
    from desk.monitor import build_monitor

    view = build_monitor(desk)
    assert view["mode"] == "PAPER"
    assert view["telegram"] == "off"
    assert view["notifier_delivery"] == "disabled"
    assert view["source_label"] == "injected"
    assert view["empty"] is False
    check = view["checks"][0]
    assert check["mint"] == MINT
    assert check["coin_url"] == coin_url(MINT)
    assert check["live_migration"] is False
    assert check["note"] == "nicht dimensioniert, nicht auf dem Desk"
    assert check["name"] == "Diamond Hands"
    liquidity = next(row for row in check["scanner"]["measurements"] if row["name"] == "Liquidität")
    assert liquidity["state"] == "pass"
    assert liquidity["value"].startswith("165922.11")
    rejected = [layer for layer in check["layers"] if layer["status"] == "rejected"]
    assert rejected[0]["n"] == 3
    assert rejected[0]["value"] == "no data:dev"
    assert check["layers"][7]["status"] == "not_checked"
    assert check["analyst"]["ran"] is False
    assert "sold 0" not in rejected[0]["value"]


def test_loopback_auth_and_empty_monitor(tmp_path):
    desk = _desk(tmp_path)
    app = create_app(desk, TOKEN)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline and not server.started:
        time.sleep(0.02)
    base = f"http://127.0.0.1:{port}"
    try:
        page = httpx.get(base + "/", timeout=3)
        assert page.status_code == 200
        assert "PAPER" in page.text
        assert TOKEN not in page.text
        blocked = httpx.get(base + "/api/monitor", timeout=3)
        assert blocked.status_code == 401
        assert TOKEN not in blocked.text
        allowed = httpx.get(base + "/api/monitor", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=3)
        assert allowed.status_code == 200
        assert allowed.json()["empty"] is True
        assert allowed.json()["empty_text"].startswith("Warte auf eine Migration")
        probed = httpx.post(
            base + "/api/probe",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"mint": MINT},
            timeout=10,
        )
        assert probed.status_code == 200
        assert probed.json()["on_desk"] is False
        again = httpx.get(base + "/api/monitor", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=3)
        body = again.json()
        assert body["checks"][0]["layers"][2]["value"] == "no data:dev"
        assert TOKEN not in again.text
    finally:
        server.should_exit = True
        thread.join(timeout=3)

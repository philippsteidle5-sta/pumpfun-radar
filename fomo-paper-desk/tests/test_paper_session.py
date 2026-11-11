"""Paper stop-all, session recap, and recorded equity windows. No network."""

import asyncio
from datetime import timedelta

from tests.test_monitor import NOW, _desk


def _open(desk, mint, mode, price="1"):
    opened = desk.db.commit_open(
        {
            "mint": mint,
            "ts": NOW.isoformat(),
            "price": price,
            "entry_price": price,
            "size_usd": "10",
            "stack_pct": "5",
            "link": "https://fomo.family/coin",
            "mode": mode,
            "status": "bought",
            "reason": None,
            "night_id": desk.night(),
            "name": mint[:6],
            "symbol": mint[:4],
            "score": "80",
            "stop_price": "0.94",
            "stop_pct": "6",
            "source": "simulated",
            "gas_model": "assumed_zero_not_verified",
            "testdata": False,
        }
    )
    assert opened == "ok"
    row = desk.db.position_by_mint(mint)
    desk.db.update_position_fields(row["id"], last_price="1.20")
    return row


def test_stop_all_closes_paper_and_leaves_live(tmp_path):
    desk = _desk(tmp_path)
    paper = _open(desk, "PaperMint1111111111111111111111111111111", "PAPER")
    live = _open(desk, "LiveMint11111111111111111111111111111111", "LIVE")
    result = asyncio.run(desk.stop_and_sell_all("STOP ALL"))
    assert result["execution_armed"] is False
    assert paper["mint"] in result["closed"]
    assert live["mint"] in result["live_untouched"]
    assert desk.db.position_by_mint(paper["mint"]) is None
    assert desk.db.position_by_mint(live["mint"])["status"] == "open"
    assert desk.settings()["desk.entries_halted"] is True
    skipped = asyncio.run(desk.execute_mint("AnotherMint11111111111111111111111111"))
    assert skipped["reason"] == "entries halted"
    recap = desk.session_recap()
    assert recap["trade_count"] == 1
    assert recap["mode"] == "PAPER"
    assert recap["execution_armed"] is False
    assert recap["best_trade"]["mint"] == paper["mint"]


def test_equity_window_uses_recorded_snapshots_only(tmp_path):
    desk = _desk(tmp_path)
    desk.db.snapshot_balance(NOW - timedelta(hours=20), "older")
    chart = desk.chart("12H")
    assert chart["range"] == "12H"
    assert chart["points"]
    for point in chart["points"]:
        assert "T" in point["t"]
    assert all(
        (NOW - __import__("desk.runtime", fromlist=["_parse_ts"])._parse_ts(point["t"])).total_seconds() <= 12 * 3600
        for point in chart["points"]
    )

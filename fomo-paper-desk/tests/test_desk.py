"""Fees, audit fail-closed, exposure slot, night chart, LIVE block."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from desk.agents.auditor import AuditEvidence, evaluate_audit
from desk.agents.history import HistoryEvidence, evaluate_history
from desk.clock import ManualClock
from desk.config import SettingsError, defaults
from desk.db import Database
from desk.fees import GAS_MODEL, FeeModelError, close_economics, effective_discount, side_fee, side_fee_raw
from desk.fields import MISSING, MintInfo, TokenAccount, Top10, graph_compatible, parse_migration
from desk.links import coin_url
from desk.runtime import Desk
from desk.telegram import parse_update

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
MINT = "DKtofLM6bhb54GQy5YFSWQjQmGiZqAkjzkQJ1LTopump"


def test_fee_boundaries_and_unverified_discount():
    assert effective_discount("0.10", verified=False) == Decimal("0")
    assert effective_discount("0.10", verified=True) == Decimal("0.10")
    assert side_fee_raw("5") == Decimal("0.10")
    assert side_fee_raw("47.49") == Decimal("0.9498")
    assert side_fee("47.49") == Decimal("0.95")
    assert side_fee_raw("47.50") == Decimal("0.95")
    assert side_fee("47.50") == Decimal("0.95")
    assert side_fee("47.51") == Decimal("0.95")
    assert side_fee("189.99") == Decimal("0.95")
    assert side_fee_raw("190") == Decimal("0.95")
    assert side_fee_raw("190.01") == Decimal("0.95005")
    assert side_fee("190.01") == Decimal("0.95")
    assert side_fee("47.50", nominal_discount="0.10", verified=True) == Decimal("0.86")
    assert side_fee("47.50", nominal_discount="0.10", verified=False) == Decimal("0.95")
    with pytest.raises(FeeModelError):
        side_fee("4.99")
    flat = close_economics("47.50", "1", "1", nominal_discount="0.10", verified=False)
    assert flat.net_pnl == Decimal("-1.90")
    assert flat.gas == Decimal("0")
    assert flat.gas_model == GAS_MODEL
    assert flat.entry_fee == Decimal("0.95")
    assert flat.exit_fee == Decimal("0.95")


def test_freeze_stops_the_tail_and_real_graph_is_not_edges():
    info = MintInfo(True, True, None, True, "FreezeAuthorityPubkey1111111111111111111", 1000, 6)
    evidence = AuditEvidence(
        mint_info=info,
        creator=None,
        creator_key_present=False,
        history=None,
        current_balance_raw=None,
        top10=Top10("incomplete", None, "no data:top10"),
        lp=__import__("desk.fields", fromlist=["LpView"]).LpView(False, None, None, None, None, {}),
        scanner_liquidity=None,
        fresh_liquidity=None,
        migrated_at=None,
        now=NOW,
        graph=[],
        classic_accounts=None,
        token2022_accounts=None,
        provenance="fixture",
    )
    result = evaluate_audit(evidence, defaults())
    assert result.passed is False
    assert result.layers_checked == 2
    assert result.layers[0].status == "pass"
    assert result.layers[1].status == "red"
    assert result.layers[1].value == "freeze not revoked"
    assert all(layer.status == "unchecked" for layer in result.layers[2:])
    assert result.reason == "freeze not revoked"

    missing = MintInfo(True, False, MISSING, True, None, 1000, 6)
    evidence.mint_info = missing
    early = evaluate_audit(evidence, defaults())
    assert early.layers[0].value == "no data:mintAuthority"
    assert early.layers[0].status == "red"

    assert graph_compatible([]) is False
    assert graph_compatible([{"nodes": [], "links": [], "net_id": "x"}]) is False
    assert graph_compatible({"nodes": [], "links": []}) is False
    assert evaluate_history(None).reason == "no data:dev"
    assert evaluate_history(HistoryEvidence("equal_balances_only", "fixture", current_raw=0, baseline_raw=0)).reason == "no data:dev"
    assert "sold 0" not in evaluate_history(HistoryEvidence("equal_balances_only", "fixture")).reason
    migration = parse_migration(
        {
            "signature": "sig",
            "mint": MINT,
            "txType": "migrate",
            "pool": "pump-amm",
        }
    )
    assert migration.creator is None
    assert migration.creator_key_present is False
    assert migration.pool_address is None
    assert migration.pool_label == "pump-amm"


def _open_row(night: str, mint: str = MINT, ts: str = "2026-11-07T12:00:00.000000Z", stop: str = "6") -> dict:
    return {
        "mint": mint,
        "ts": ts,
        "price": "1",
        "entry_price": "1",
        "size_usd": "47.50",
        "stack_pct": "23.75",
        "link": coin_url(mint),
        "mode": "PAPER",
        "status": "bought",
        "night_id": night,
        "name": "Test",
        "symbol": "TEST",
        "score": "80",
        "stop_price": "0.94",
        "stop_pct": stop,
        "source": "simulated",
        "gas_model": GAS_MODEL,
        "testdata": True,
    }


def test_close_blocked_keeps_exposure_and_live_chart_is_the_night(tmp_path):
    db = Database(str(tmp_path / "desk.sqlite"))
    clock = ManualClock(NOW)
    desk = Desk(db, clock=clock)
    night = desk.open_night()
    assert "47.50" in night["warning"]
    assert "23.75%" in night["warning"]
    assert night["blocked"] == 0
    assert db.journal_mode().lower() == "wal"
    assert db.commit_open(_open_row(desk.night())) == "ok"
    desk.retighten_stops(Decimal("8"))
    assert db.position_by_mint(MINT)["stop_pct"] == "6"
    desk.retighten_stops(Decimal("4"))
    tightened = db.position_by_mint(MINT)
    assert tightened["stop_pct"] == "4"
    assert tightened["stop_price"] == "0.96"
    reserved_before = db.equity()[2]
    desk._exit(tightened, "stop", Decimal("0.05"), "polled-price", desk.settings())
    blocked = db.position_by_mint(MINT)
    assert blocked["status"] == "close_blocked"
    assert blocked["pnl_booked"] == 0
    assert db.equity()[2] == reserved_before == Decimal("47.50")
    assert desk.state()["exposure"] == 1
    assert desk.state()["reserved"] == "47.50"
    with pytest.raises(SettingsError, match="cannot pause"):
        desk.pause("risk")
    assert db.commit_open(_open_row("2099-01-01", ts="2099-01-01T00:00:00.000000Z")) == "duplicate position"
    asyncio.run(desk.poll_risk())
    assert db.position_by_mint(MINT)["status"] == "close_blocked"
    assert db.equity()[2] == Decimal("47.50")

    db.con.execute(
        "INSERT INTO balance (ts, value, cash, reserved, label) VALUES (?, ?, ?, ?, ?)",
        ("2026-11-07T11:00:00.000000Z", "200.00", "200.00", "0.00", "before-night"),
    )
    db.con.execute(
        "INSERT INTO balance (ts, value, cash, reserved, label) VALUES (?, ?, ?, ?, ?)",
        ("2026-11-07T12:30:00.000000Z", "200.00", "152.50", "47.50", "inside-night"),
    )
    clock.advance(hours=2)
    live = desk.chart("LIVE")
    stamps = [point["t"] for point in live["points"]]
    assert "2026-11-07T12:30:00.000000Z" in stamps
    assert "2026-11-07T11:00:00.000000Z" not in stamps
    assert all("T11:00" not in point["t"] for point in live["points"])
    assert desk.chart("ALL")["points"]
    with desk.db._lock:
        desk.db.con.execute("UPDATE night_runs SET qualifying = 1, synthetic = 0")
    with pytest.raises(SettingsError, match="LIVE blocked"):
        desk.set_mode("LIVE", "LIVE")


def test_paper_round_trip_subtracts_each_fee_once(tmp_path):
    db = Database(str(tmp_path / "pnl.sqlite"))
    desk = Desk(db, clock=ManualClock(NOW))
    desk.open_night()
    assert db.commit_open(_open_row(desk.night())) == "ok"
    equity, cash, reserved = db.equity()
    assert equity == Decimal("200.00")
    assert cash == Decimal("152.50")
    assert reserved == Decimal("47.50")
    desk._exit(db.position_by_mint(MINT), "stop", Decimal("1"), "polled-price", desk.settings())
    closed = db.latest_position(MINT)
    assert closed["status"] == "closed"
    assert closed["source"] == "simulated"
    assert closed["pnl_usd"] == "-1.90"
    assert closed["fees_usd"] == "1.90"
    assert closed["entry_fee_usd"] == "0.95"
    assert closed["exit_fee_usd"] == "0.95"
    equity, cash, reserved = db.equity()
    assert reserved == Decimal("0.00")
    assert cash == equity == Decimal("198.10")
    assert db.book_close(closed["id"], {"closed_at": "x"}) == "already"
    assert db.equity()[0] == Decimal("198.10")
    assert desk.state()["balance"] == "198.10"
    assert desk.state()["exposure"] == 0


def test_owner_spoof_and_duplicate_callback(tmp_path):
    spoof = parse_update({"message": {"text": "status", "chat": {"id": 99, "type": "private"}}}, "1")
    assert spoof.status == "reject"
    assert spoof.reason == "not owner"
    group = parse_update({"message": {"text": "status", "chat": {"id": 1, "type": "group"}}}, "1")
    assert group.status == "ignore"
    missing_owner = parse_update({"callback_query": {"id": "cb", "data": "bought:x", "message": {"chat": {"id": 1, "type": "private"}}}}, None)
    assert missing_owner.status == "reject"

    db = Database(str(tmp_path / "cb.sqlite"))
    desk = Desk(db, clock=ManualClock(NOW))
    desk.open_night()
    update = {
        "callback_query": {
            "id": "cb-1",
            "data": f"bought:{MINT}",
            "message": {"chat": {"id": "1", "type": "private"}},
        }
    }
    first = desk.handle_update(update, "1")
    second = desk.handle_update(update, "1")
    assert first["status"] == "accept"
    assert second["reason"] == "duplicate callback"
    assert db.position_by_mint(MINT) is None


def test_labeled_testdata_round_trip_is_not_a_paper_night(tmp_path):
    from desk.agents.auditor import AuditEvidence
    from desk.agents.history import HistoryEvidence
    from desk.fields import LpView, TokenAccount, Top10
    from desk.sources import MapSources

    created = int((NOW - timedelta(minutes=30)).timestamp() * 1000)
    row = {
        "dexId": "pumpswap",
        "pairAddress": "ANS69UDTbMCWbW7ZuCYXLZ4HMCQkvzPMtCW9zjPZQGKR",
        "baseToken": {"address": MINT, "name": "Unique Test Coin", "symbol": "UTST"},
        "priceUsd": "1.00",
        "liquidity": {"usd": 100000},
        "volume": {"m5": 1000, "h1": 40000, "h24": 10},
        "priceChange": {"m5": -6, "h1": 10, "h24": 10},
        "txns": {"m5": {"buys": 20, "sells": 10}, "h1": {"buys": 200, "sells": 80}},
        "pairCreatedAt": created,
    }
    sources = MapSources(dex={MINT: row})
    db = Database(str(tmp_path / "pipe.sqlite"))
    clock = ManualClock(NOW)
    desk = Desk(
        db,
        clock=clock,
        sources=sources,
        top10_provider=lambda _mint, _pair: Top10("ok", Decimal("10"), "ok", provenance="testdata"),
        evidence_override={
            MINT: AuditEvidence(
                mint_info=__import__("desk.fields", fromlist=["MintInfo"]).MintInfo(True, True, None, True, None, 1_000_000, 6),
                creator="Creator1111111111111111111111111111111111",
                creator_key_present=True,
                history=HistoryEvidence(
                    "complete",
                    "testdata",
                    True,
                    True,
                    True,
                    True,
                    True,
                    True,
                    True,
                    True,
                    True,
                    current_raw=1000,
                    baseline_raw=1000,
                ),
                current_balance_raw=1000,
                top10=Top10("ok", Decimal("10"), "ok", provenance="testdata"),
                lp=LpView(True, Decimal("100"), 1, 1, None, {}),
                scanner_liquidity=Decimal("100000"),
                fresh_liquidity=Decimal("100000"),
                migrated_at=NOW - timedelta(minutes=30),
                now=NOW,
                graph={
                    "schema": "holder-edges-v1",
                    "holders": ["o1", "o2"],
                    "edges": [{"source": "o1", "target": "o2", "kind": "funded"}],
                    "completeness_proven": True,
                },
                classic_accounts=[
                    TokenAccount("a", "o1", MINT, 1000, False, None, True, 170),
                    TokenAccount("b", "o2", MINT, 1000, False, None, True, 170),
                ],
                token2022_accounts=[],
                provenance="testdata",
            )
        },
    )
    desk.ingest_migration(
        {"signature": "sig", "mint": MINT, "txType": "migrate", "pool": "pump-amm"},
        testdata=True,
    )
    asyncio.run(desk.step())
    position = db.position_by_mint(MINT)
    assert position is not None
    assert position["status"] == "open"
    assert position["source"] == "simulated"
    assert position["size_usd"] == "47.50"
    assert position["testdata"] == 1
    night = db.night(desk.night())
    assert night["qualifying"] == 0
    assert night["synthetic"] == 1
    audits = db.audits_for(MINT)
    assert any("TESTDATA" in (item["value_text"] or "") for item in audits)
    link = coin_url(MINT)
    assert any(item["payload"]["link"] == link for item in db.outbox_all())
    clock.advance(seconds=20)
    row["priceUsd"] = "1.25"
    desk.last_poll["risk"] = None
    asyncio.run(desk.poll_risk())
    assert db.position_by_mint(MINT)["trail_active"] == 1
    assert db.position_by_mint(MINT)["status"] == "open"
    clock.advance(seconds=20)
    row["priceUsd"] = "1.0875"
    desk.last_poll["risk"] = None
    asyncio.run(desk.poll_risk())
    closed = db.latest_position(MINT)
    assert closed["status"] == "closed"
    assert closed["exit_reason"] == "trail"
    assert db.qualifying_nights() == 0

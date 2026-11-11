"""Analysis view for the phone monitor. Numbers come from stored checks, not from logs."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from desk.agents.auditor import LAYER_NAMES
from desk.links import coin_url
from desk.money import D
from desk.runtime import _parse_ts


def _owner(ts: str | None, offset: int) -> str | None:
    if not ts:
        return None
    local = _parse_ts(ts) + timedelta(hours=int(offset))
    sign = "+" if offset >= 0 else ""
    return local.strftime("%Y-%m-%d %H:%M:%S") + f" UTC{sign}{int(offset)}"


def _cmp(value: str | None, rule: str, state: str) -> dict:
    return {"value": value, "rule": rule, "state": state}


def _measurements(scan: dict | None, settings: dict) -> list[dict]:
    if scan is None:
        return []
    rows = []
    liquidity = scan.get("liquidity_usd")
    minimum = settings["scanner.min_liquidity_usd"]
    if liquidity is None:
        rows.append({"name": "Liquidität", **_cmp(None, f"mindestens {minimum} USD", "missing")})
    else:
        state = "pass" if D(liquidity) >= D(minimum) else "fail"
        rows.append({"name": "Liquidität", **_cmp(liquidity, f"mindestens {minimum} USD", state)})
    top = scan.get("top10_pct")
    top_rule = f"Scanner rot über {settings['scanner.max_top10_pct']} %. Ohne Pool-Beweis kein Pass"
    if top is None:
        rows.append({"name": "Top 10", **_cmp(None, top_rule, "missing")})
    else:
        state = "fail" if D(top) > D(settings["scanner.max_top10_pct"]) else "pass"
        rows.append({"name": "Top 10", **_cmp(top, top_rule, state)})
    h1 = scan.get("h1_change_pct")
    h1_rule = f"höchstens {settings['scanner.max_h1_change_pct']} % in 1h"
    if h1 is None:
        rows.append({"name": "1h-Änderung", **_cmp(None, h1_rule, "missing")})
    else:
        state = "fail" if D(h1) > D(settings["scanner.max_h1_change_pct"]) else "pass"
        rows.append({"name": "1h-Änderung", **_cmp(h1, h1_rule, state)})
    age = scan.get("age_min")
    age_rule = f"Pool mindestens {settings['scanner.min_pool_age_min']} min"
    if age is None:
        rows.append({"name": "Pool-Alter", **_cmp(None, age_rule, "missing")})
    else:
        state = "pass" if D(age) >= D(settings["scanner.min_pool_age_min"]) else "fail"
        rows.append({"name": "Pool-Alter", **_cmp(age, age_rule, state)})
    return rows


def _layers(stored: list[dict]) -> list[dict]:
    by_number = {row["layer"]: row for row in stored}
    rendered = []
    for number in range(1, 9):
        row = by_number.get(number)
        if row is None:
            rendered.append(
                {
                    "n": number,
                    "name": LAYER_NAMES[number],
                    "status": "not_checked",
                    "value": "nicht geprüft",
                    "source": None,
                    "ts": None,
                }
            )
            continue
        status = {"pass": "checked", "red": "rejected", "unchecked": "not_checked"}.get(row["status"], "not_checked")
        rendered.append(
            {
                "n": number,
                "name": row["name"] or LAYER_NAMES[number],
                "status": status,
                "value": row["value_text"],
                "source": row["source"],
                "ts": row["ts"],
            }
        )
    return rendered


def _analyst(layers: list[dict], verdict: dict | None, offset: int) -> dict:
    rejected = any(layer["status"] == "rejected" for layer in layers)
    unchecked = any(layer["status"] == "not_checked" for layer in layers)
    if rejected or unchecked or verdict is None:
        return {
            "ran": False,
            "status": "not_checked",
            "reason": "Analyst nur nach Auditor 8/8",
            "ts": None,
            "source": None,
        }
    failed = not verdict.get("pass")
    return {
        "ran": True,
        "status": "rejected" if failed else "checked",
        "model": verdict.get("model"),
        "score": verdict.get("score"),
        "reason": verdict.get("reason"),
        "alive_6h": verdict.get("alive_6h"),
        "organic": verdict.get("organic"),
        "flush": verdict.get("flush"),
        "chase": verdict.get("chase"),
        "ts": _owner(verdict.get("ts"), offset),
        "source": "rules" if verdict.get("model") == "rules" else verdict.get("model"),
    }


def _stage(check: dict) -> dict:
    scanner = check["scanner"]
    verdict = scanner.get("verdict")
    if verdict in {None, "wait", "pending"}:
        return {"name": "scanner", "state": "pending", "reason": scanner.get("reason") or "awaiting dex read"}
    if verdict in {"kill", "reject"}:
        return {"name": "scanner", "state": "reject", "reason": scanner.get("reason") or "scanner rejected"}
    rejected = [layer for layer in check["layers"] if layer["status"] == "rejected"]
    if rejected:
        return {"name": "auditor", "state": "reject", "reason": rejected[0].get("value") or rejected[0].get("name")}
    pending = [layer for layer in check["layers"] if layer["status"] == "not_checked"]
    if pending or verdict == "context":
        reason = check.get("note") or "auditor incomplete"
        return {"name": "auditor", "state": "pending", "reason": reason}
    analyst = check["analyst"]
    if not analyst.get("ran"):
        return {"name": "analyst", "state": "pending", "reason": analyst.get("reason") or "waiting for auditor 8/8"}
    if analyst.get("status") == "rejected":
        return {"name": "analyst", "state": "reject", "reason": analyst.get("reason") or "analyst rejected"}
    if check.get("on_desk"):
        return {
            "name": "executor",
            "state": "unconnected",
            "reason": "paper ledger row only; not a FOMO fill and not a connected bot",
        }
    return {"name": "executor", "state": "unconnected", "reason": "live FOMO orders are unconnected"}


def build_monitor(desk) -> dict:
    settings = desk.settings()
    offset = int(settings["desk.owner_utc_offset_hours"])
    feed = desk.feed
    if feed.get("error") or feed.get("closed"):
        connection = "error"
    elif feed.get("open_seen"):
        connection = "open"
    else:
        connection = "waiting"
    checks = []
    for item in reversed(desk.db.checks(30)):
        mint = item["mint"]
        coin = desk.db.coin(mint) or {}
        scan = desk.db.latest_scan(mint)
        layers = _layers(desk.db.audits_for(mint))
        verdict = desk.db.latest_verdict(mint)
        position = desk.db.position_by_mint(mint)
        checks.append(
            {
                "mint": mint,
                "name": coin.get("name"),
                "symbol": coin.get("symbol"),
                "coin_url": coin_url(mint),
                "ts": _owner(item["ts"], offset),
                "ts_utc": item["ts"],
                "kind": item["kind"],
                "context": item["context"],
                "source_label": item["source_label"],
                "live_migration": item["context"] == "live_migration",
                "sized": bool(item["sized"]) or position is not None,
                "on_desk": bool(item["on_desk"]) or position is not None,
                "note": item.get("note"),
                "error": item.get("error"),
                "scanner": {
                    "verdict": None if scan is None else scan.get("verdict"),
                    "reason": None if scan is None else scan.get("reason"),
                    "ts": None if scan is None else _owner(scan.get("ts"), offset),
                    "source": "dexscreener",
                    "measurements": _measurements(scan, settings),
                },
                "layers": layers,
                "analyst": _analyst(layers, verdict, offset),
                "sources": desk.db.raw_summaries(mint),
                "paper": True,
            }
        )
    observed = desk.observability()
    for item in observed["waiting"]:
        item["since"] = _owner(item.get("since"), offset)
        item["seen_at"] = item["since"]
        if item.get("scan_ts"):
            item["scan_ts"] = _owner(item["scan_ts"], offset)
    for row in observed["recent_scans"]:
        row["ts"] = _owner(row.get("ts"), offset)
    for check in checks:
        check["stage"] = _stage(check)
    return {
        "mode": "PAPER",
        "trading": "PAPER",
        "market_data": desk.market_data,
        "source_label": desk.source_label,
        "notifier": "outbox",
        "notifier_delivery": "disabled",
        "telegram": "off",
        "connection": connection,
        "connection_detail": feed.get("detail"),
        "connection_error": feed.get("error"),
        "waiting": observed["waiting"],
        "search": observed,
        "empty": not checks and not observed["waiting"],
        "empty_text": "Warte auf eine Migration. Noch keine Münze geprüft.",
        "checks": list(reversed(checks)),
        "agents": desk.state()["agents"],
    }

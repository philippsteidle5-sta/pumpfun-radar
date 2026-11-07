"""Read-only owner-day reports. Decimal totals, no inferred audit passes."""

from collections import Counter
import csv
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import io

from desk.money import D, cents, iso_utc


def build_report(desk, day: str | None = None) -> dict:
    selected = date.fromisoformat(day or desk.night())
    if selected.isoformat() != (day or desk.night()):
        raise ValueError("date must be YYYY-MM-DD")
    offset = int(desk.settings()["desk.owner_utc_offset_hours"])
    start = datetime.combine(selected, time.min, tzinfo=timezone.utc) - timedelta(hours=offset)
    end = start + timedelta(days=1)
    rows = desk.db.report_rows(iso_utc(start), iso_utc(end))
    # Only the last scanner outcome per mint counts in the funnel.
    scans = {row["mint"]: row for row in rows["scans"]}
    rejected = Counter(row["reason"] or "Ohne Begründung" for row in scans.values() if row["verdict"] in {"kill", "reject"})
    audit_reasons = Counter(row["value_text"] or "Ohne Begründung" for row in rows["audits"] if row["status"] == "red")
    trades = [row for row in rows["closed"] if row["mode"] == "PAPER" and not row["testdata"]]
    test_trades = [row for row in rows["closed"] if row["testdata"]]
    pnl = sum((D(row["pnl_usd"] or 0) for row in trades), Decimal(0))
    fees = sum((D(row["fees_usd"] or 0) for row in trades), Decimal(0))
    wins = sum(D(row["pnl_usd"] or 0) > 0 for row in trades)
    return {
        "date": selected.isoformat(), "owner_utc_offset_hours": offset,
        "start_utc": iso_utc(start), "end_utc": iso_utc(end),
        "generated_at": iso_utc(desk.now()), "mode": "PAPER",
        "ongoing": desk.now() < end,
        "scanned": len(scans),
        "audit_passed": len({r["mint"] for r in rows["audits"] if r["layer"] == 8 and r["status"] == "pass"}),
        "analyst_passed": len({r["mint"] for r in rows["verdicts"] if r["pass"]}),
        "signals": len(rows["signals"]),
        "closed_trades": len(trades), "testdata_closed_trades": len(test_trades),
        "wins": wins, "losses": sum(D(r["pnl_usd"] or 0) < 0 for r in trades),
        "win_rate_pct": str(cents(Decimal(wins) / len(trades) * 100)) if trades else None,
        "net_pnl_usd": str(cents(pnl)), "fees_usd": str(cents(fees)),
        "scanner_rejections": [{"reason": k, "count": v} for k, v in rejected.most_common()],
        "audit_rejections": [{"reason": k, "count": v} for k, v in audit_reasons.most_common()],
        "trades": trades,
        "open_positions_now": [r for r in rows["open"] if r["mode"] == "PAPER"],
        "note": "Netto-PnL abgeschlossener PAPER-Trades nach Modellgebühren. TESTDATA separat. Offene Positionen: aktueller Stand, nicht historischer Tagesabschluss.",
    }


def report_csv(report: dict) -> str:
    stream = io.StringIO(newline="")
    fields = ["id", "mint", "name", "opened_at", "closed_at", "entry_price", "exit_price", "size_usd", "pnl_usd", "fees_usd", "exit_reason", "mode", "testdata"]
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for trade in report["trades"]:
        # Protect spreadsheet imports from formula cells in public token names.
        clean = {key: value for key, value in trade.items()}
        for key in ("mint", "name", "exit_reason"):
            value = str(clean.get(key) or "")
            if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
                value = "'" + value
            clean[key] = value
        writer.writerow(clean)
    return "\ufeff" + stream.getvalue()

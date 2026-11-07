"""Exit order: stop, then sticky trail, then timeout. The poll price is the exit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from desk.money import D, cents, fmt_money, fmt_pct


@dataclass
class TickDecision:
    action: str | None  # stop | trail | timeout | None
    trail_active: bool
    trail_just_activated: bool
    peak: Decimal
    stop_price: Decimal


def stop_price_for(entry: object, stop_pct: object) -> Decimal:
    return cents(D(entry) * (Decimal(1) - D(stop_pct) / Decimal(100)))


def evaluate_tick(
    *,
    entry: object,
    peak: object,
    stop_price: object,
    trail_active: bool,
    price: object,
    opened_at: datetime,
    now: datetime,
    settings: dict,
) -> TickDecision:
    price_d = D(price)
    peak_d = max(D(peak), price_d)
    stop = D(stop_price)
    active = trail_active
    just = False
    if price_d <= stop:
        return TickDecision("stop", active, False, peak_d, stop)
    start = D(settings["risk.trail_start_pct"])
    if not active and price_d >= D(entry) * (Decimal(1) + start / Decimal(100)):
        active = True
        just = True
    trail_width = D(settings["risk.trail_pct"])
    if active and price_d <= peak_d * (Decimal(1) - trail_width / Decimal(100)):
        return TickDecision("trail", active, just, peak_d, stop)
    age_hours = Decimal(str((now - opened_at).total_seconds())) / Decimal(3600)
    timeout_h = D(settings["risk.timeout_h"])
    gain_line = D(entry) * (Decimal(1) + D(settings["risk.timeout_min_gain_pct"]) / Decimal(100))
    if age_hours >= timeout_h and price_d < gain_line:
        return TickDecision("timeout", active, just, peak_d, stop)
    return TickDecision(None, active, just, peak_d, stop)


def tighten_stop_pct(current: object, proposed: object) -> Decimal:
    """A looser global stop never lowers a position. A tighter one may."""
    current_d = D(current)
    proposed_d = D(proposed)
    if proposed_d < current_d:
        return proposed_d
    return current_d


def gross_pct(entry: object, exit_price: object) -> Decimal:
    entry_d = D(entry)
    return cents((D(exit_price) - entry_d) / entry_d * Decimal(100))


def paper_exit_text(*, mode: str, name: str, reason: str, stop_pct: object, net_pnl: object, gross: object, timeout_h: object) -> str:
    prefix = "PAPER · " if mode == "PAPER" else ""
    if reason == "trail":
        sign = "+" if D(net_pnl) >= 0 else "-"
        body = f"Sold {name} · {sign}${fmt_money(abs(D(net_pnl)))} ({fmt_signed_pct(gross)}%)"
        return prefix + body
    if reason == "stop":
        loss = abs(D(net_pnl))
        return prefix + f"{name} hit the -{fmt_pct(stop_pct)}% stop · -${fmt_money(loss)}"
    if reason == "timeout":
        return prefix + f"Sell {name}, dead money · {fmt_signed_pct(gross)}% in {fmt_pct(timeout_h)}h"
    if reason == "pulled":
        return prefix + f"Sell {name}, liquidity pulled · last price, no executable guarantee"
    raise ValueError(reason)


def live_pending_text(*, name: str, reason: str, stop_pct: object) -> str:
    if reason == "stop":
        return f"{name} hit the -{fmt_pct(stop_pct)}% stop · pending Sold, not sold"
    if reason == "trail":
        return f"{name} trail triggered · pending Sold, not sold"
    if reason == "timeout":
        return f"Sell {name}, dead money · pending Sold, not sold"
    return f"{name} liquidity pulled · pending Sold, not sold"


def fmt_signed_pct(value: object) -> str:
    number = D(value)
    text = fmt_pct(abs(number))
    if number > 0:
        return f"+{text}"
    if number < 0:
        return f"-{text}"
    return "0"

"""Waiting-room evaluation. A missing Dex pair waits. It never passes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from desk.fields import DexPair, Top10, copycat_of, fmt_liq_reason
from desk.money import D


@dataclass
class ScanDecision:
    verdict: str  # pass | kill | wait
    reason: str | None
    liquidity_usd: Decimal | None
    top10_pct: Decimal | None
    h1_change_pct: Decimal | None
    age_min: Decimal | None


def evaluate_waiting(
    *,
    pair: DexPair | None,
    top10: Top10 | None,
    now: datetime,
    seen_at: datetime,
    runners: list[tuple[str, str]],
    settings: dict,
    own_name: str | None,
) -> ScanDecision:
    max_wait = D(settings["scanner.max_wait_min"])
    waited = Decimal(str((now - seen_at).total_seconds())) / Decimal(60)
    if pair is None:
        return _maybe_expire(waited, max_wait, "no dex pair", None, None, None, None)
    liquidity = pair.liquidity_usd
    h1 = pair.price_change_h1
    age = _pool_age_min(pair, now)
    top_pct = top10.pct if top10 and top10.status == "ok" else None
    if liquidity is None:
        return _maybe_expire(waited, max_wait, "no dex pair", None, top_pct, h1, age)
    if liquidity < D(settings["scanner.min_liquidity_usd"]):
        return ScanDecision("kill", fmt_liq_reason(liquidity), liquidity, top_pct, h1, age)
    if top10 is None or top10.status != "ok":
        return _maybe_expire(waited, max_wait, "no data:top10", liquidity, None, h1, age)
    if top10.pct is not None and top10.pct > D(settings["scanner.max_top10_pct"]):
        shown = top10.pct.quantize(Decimal("1"))
        return ScanDecision("kill", f"top 10 hold {shown}%", liquidity, top10.pct, h1, age)
    if h1 is None:
        return _maybe_expire(waited, max_wait, "no h1", liquidity, top10.pct, None, age)
    if h1 > D(settings["scanner.max_h1_change_pct"]):
        shown = h1.quantize(Decimal("1"))
        return ScanDecision("kill", f"+{shown}% in 1h", liquidity, top10.pct, h1, age)
    copy = copycat_of(own_name or pair.name, runners)
    if copy:
        return ScanDecision("kill", f"copycat of {copy}", liquidity, top10.pct, h1, age)
    if age is None:
        return _maybe_expire(waited, max_wait, "no pool age", liquidity, top10.pct, h1, None)
    if age < D(settings["scanner.min_pool_age_min"]):
        return _maybe_expire(waited, max_wait, "pool younger than minimum", liquidity, top10.pct, h1, age)
    return ScanDecision("pass", None, liquidity, top10.pct, h1, age)


def _pool_age_min(pair: DexPair, now: datetime) -> Decimal | None:
    if pair.pair_created_at is None:
        return None
    seconds = Decimal(str((now - pair.pair_created_at).total_seconds()))
    if seconds < 0:
        return Decimal(0)
    return seconds / Decimal(60)


def _maybe_expire(waited, max_wait, reason, liquidity, top, h1, age) -> ScanDecision:
    if waited >= max_wait:
        return ScanDecision("kill", "never qualified", liquidity, top, h1, age)
    return ScanDecision("wait", reason, liquidity, top, h1, age)


def next_backoff_seconds(attempt: int) -> int:
    """attempt 0 -> 1s, then 2, 4, ... capped at 60."""
    if attempt < 0:
        attempt = 0
    return min(60, 2**attempt)


def transport_needs_reconnect(*, closed: bool, silent_seconds: float, open_seen: bool) -> bool:
    """Quiet migrations are not a broken socket. Closure or 120s of silence is."""
    if not open_seen:
        return True
    if closed:
        return True
    return silent_seconds >= 120

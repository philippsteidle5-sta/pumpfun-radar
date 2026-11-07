"""Ticket sizing. The pool cap wins over a bump. No order is ever sent."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from desk.money import D, cents


@dataclass(frozen=True)
class SizePlan:
    action: str  # size | skip
    size: Decimal | None
    raw: Decimal
    cap: Decimal
    stack_pct: Decimal | None
    reason: str | None


def plan_size(*, stack: object, free: object, pool_liquidity: object, settings: dict) -> SizePlan:
    stack_d = cents(stack)
    free_d = cents(free)
    liquidity = D(pool_liquidity)
    raw = cents(stack_d * D(settings["executor.stack_pct"]) / Decimal(100))
    cap = cents(liquidity * D(settings["executor.max_pool_pct"]) / Decimal(100))
    minimum = cents(settings["executor.min_ticket_usd"])
    size = raw if raw <= cap else cap
    if size > free_d and size >= minimum:
        return SizePlan("skip", None, raw, cap, None, "above free")
    if size < minimum:
        if settings["executor.below_min"] == "skip":
            return SizePlan("skip", None, raw, cap, None, "below min ticket")
        if minimum > cap:
            return SizePlan("skip", None, raw, cap, None, "bump exceeds pool cap")
        if minimum > free_d:
            return SizePlan("skip", None, raw, cap, None, "bump exceeds free")
        size = minimum
    if size > cap:
        return SizePlan("skip", None, raw, cap, None, "exceeds pool cap")
    if size > free_d:
        return SizePlan("skip", None, raw, cap, None, "above free")
    if stack_d <= 0:
        return SizePlan("skip", None, raw, cap, None, "stack is zero")
    pct = cents(size / stack_d * Decimal(100))
    return SizePlan("size", size, raw, cap, pct, None)


def recheck(
    *,
    scanner_liquidity: object | None,
    fresh_liquidity: object | None,
    analyst_price: object | None,
    fresh_price: object | None,
    settings: dict,
) -> str | None:
    if fresh_price is None or fresh_liquidity is None:
        return "no price"
    try:
        price = D(fresh_price)
        liquidity = D(fresh_liquidity)
    except Exception:
        return "no price"
    if price <= 0:
        return "no price"
    if scanner_liquidity is not None:
        scanned = D(scanner_liquidity)
        if scanned > 0:
            drop = (scanned - liquidity) / scanned * Decimal(100)
            if drop > D(settings["executor.recheck_liq_drop_pct"]):
                return "liquidity dropping"
    if analyst_price is not None:
        anchored = D(analyst_price)
        if anchored > 0:
            rise = (price - anchored) / anchored * Decimal(100)
            if rise > D(settings["executor.recheck_price_up_pct"]):
                return "ran before push"
    return None

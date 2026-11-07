"""User-specified paper fee model. Not a verified FOMO price list.

Gas is 0 as a model assumption, labeled as such, not as a service fact.
Referral discount applies only after the gross fee and only when verified.
Unverified accounts keep nominal 0.10 in config and an effective discount of 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from desk.money import D, cents

# Piecewise gross fee, each side, before referral:
#   [5, 47.50]  -> 2% of notional
#   (47.50, 190] -> flat 0.95
#   (190, +inf) -> 0.5% of notional
# 47.50 is continuous (both formulas 0.95). 190 is discontinuous in the raw
# formula: 0.95 at 190, 190.01 * 0.005 = 0.95005. Booked amounts then round
# to cents with ROUND_HALF_UP. Below $5 the model refuses. No extrapolation.

TIER_LOW = Decimal("47.50")
TIER_HIGH = Decimal("190")
FLOOR = Decimal("5")
GAS_MODEL = "assumed_zero_not_verified"


class FeeModelError(ValueError):
    pass


def effective_discount(nominal: object, verified: bool) -> Decimal:
    nominal_d = D(nominal)
    if nominal_d < 0 or nominal_d > 1:
        raise FeeModelError("referral discount outside 0..1")
    if not verified:
        return Decimal("0")
    return nominal_d


def side_fee_raw(notional: object) -> Decimal:
    amount = D(notional)
    if amount < FLOOR:
        raise FeeModelError(
            f"notional {amount} is below $5; fee is unspecified and is not extrapolated"
        )
    if amount <= TIER_LOW:
        return amount * Decimal("0.02")
    if amount <= TIER_HIGH:
        return Decimal("0.95")
    return amount * Decimal("0.005")


def side_fee(notional: object, *, nominal_discount: object = "0.10", verified: bool = False) -> Decimal:
    gross = side_fee_raw(notional)
    discount = effective_discount(nominal_discount, verified)
    return cents(gross * (Decimal("1") - discount))


def exit_notional(size: object, entry_price: object, exit_price: object) -> Decimal:
    size_d = D(size)
    entry = D(entry_price)
    exit_px = D(exit_price)
    if entry <= 0 or exit_px < 0 or size_d <= 0:
        raise FeeModelError("entry price and size must be positive")
    return cents(size_d * exit_px / entry)


@dataclass(frozen=True)
class CloseEconomics:
    exit_notional: Decimal
    entry_fee: Decimal
    exit_fee: Decimal
    fees: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    gas: Decimal
    gas_model: str


def close_economics(
    size: object,
    entry_price: object,
    exit_price: object,
    *,
    nominal_discount: object = "0.10",
    verified: bool = False,
) -> CloseEconomics:
    """Net PnL = exit notional - entry ticket - entry fee - exit fee.

    The entry fee is applied once, at close. Reservations hold the ticket
    only, so equity does not subtract the entry fee a second time.
    """
    ticket = cents(size)
    proceeds = exit_notional(ticket, entry_price, exit_price)
    entry_fee = side_fee(ticket, nominal_discount=nominal_discount, verified=verified)
    exit_fee = side_fee(proceeds, nominal_discount=nominal_discount, verified=verified)
    gross = cents(proceeds - ticket)
    net = cents(gross - entry_fee - exit_fee)
    return CloseEconomics(
        exit_notional=proceeds,
        entry_fee=entry_fee,
        exit_fee=exit_fee,
        fees=cents(entry_fee + exit_fee),
        gross_pnl=gross,
        net_pnl=net,
        gas=Decimal("0"),
        gas_model=GAS_MODEL,
    )

"""Finite decimals and display helpers. Floats are not an accounting type."""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP

CENT = Decimal("0.01")
UNIT = Decimal("1")


class BadNumber(ValueError):
    pass


def D(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise BadNumber("missing numeric value")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise BadNumber("non-finite decimal")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BadNumber("non-finite float")
        text = format(value, "f")
    elif isinstance(value, int):
        return Decimal(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise BadNumber(f"not a number: {type(value).__name__}")
    lowered = text.lower()
    if lowered in {"", "nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}:
        raise BadNumber("non-finite")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise BadNumber(text) from exc
    if not number.is_finite():
        raise BadNumber("non-finite")
    return number


def cents(value: object) -> Decimal:
    """Money to the cent. Ties round away from zero (ROUND_HALF_UP)."""
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def half_even_int(value: object) -> int:
    """Analyst score. Python's default half-even rule, made explicit."""
    return int(D(value).quantize(UNIT, rounding=ROUND_HALF_EVEN))


def fmt_money(value: object) -> str:
    return f"{cents(value):.2f}"


def fmt_pct(value: object) -> str:
    quantized = D(value).quantize(CENT, rounding=ROUND_HALF_UP)
    if quantized == quantized.to_integral():
        return str(int(quantized))
    return f"{quantized:.2f}"


def iso_utc(moment) -> str:
    if moment.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware UTC")
    return moment.astimezone(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

"""Validated settings, aliases, ranges, and the ticket-size warning.

Ranges are proposed and documented. Out-of-range values are refused with the
range. Nothing is silently clamped. Stop input of -8 stores magnitude 8.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from desk.money import D, cents, fmt_money, fmt_pct

# Fixed owner offset. Original night timezone was not specified.
# November presentation is UTC+1. This is not DST-aware Europe/Berlin.
DEFAULT_OWNER_OFFSET_HOURS = 1


class SettingsError(ValueError):
    def __init__(self, message: str, *, key: str | None = None, minimum=None, maximum=None):
        super().__init__(message)
        self.key = key
        self.minimum = minimum
        self.maximum = maximum

    def payload(self) -> dict:
        body = {"detail": str(self)}
        if self.key is not None:
            body["key"] = self.key
        if self.minimum is not None:
            body["min"] = _public(self.minimum)
        if self.maximum is not None:
            body["max"] = _public(self.maximum)
        return body


def _public(value):
    if isinstance(value, Decimal):
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    return value


@dataclass(frozen=True)
class Spec:
    key: str
    type: str
    default: Any
    minimum: Any = None
    maximum: Any = None
    choices: tuple[str, ...] | None = None
    unit: str = ""


def _d(value: str) -> Decimal:
    return Decimal(value)


SPECS: dict[str, Spec] = {
    spec.key: spec
    for spec in [
        Spec("scanner.min_liquidity_usd", "decimal", _d("40000"), _d("0"), _d("100000000"), unit="usd"),
        Spec("scanner.max_top10_pct", "decimal", _d("25"), _d("0"), _d("100"), unit="pct"),
        Spec("scanner.max_h1_change_pct", "decimal", _d("80"), _d("0"), _d("1000000"), unit="pct"),
        Spec("scanner.poll_s", "int", 60, 5, 3600, unit="s"),
        Spec("scanner.min_pool_age_min", "decimal", _d("10"), _d("0"), _d("10080"), unit="min"),
        Spec("scanner.max_wait_min", "decimal", _d("60"), _d("1"), _d("1440"), unit="min"),
        Spec("scanner.runner_h24_pct", "decimal", _d("300"), _d("0"), _d("10000000"), unit="pct"),
        Spec("auditor.enabled", "bool", True),
        Spec("auditor.max_dev_pct", "decimal", _d("5"), _d("0"), _d("100"), unit="pct"),
        Spec("auditor.max_top10_pct", "decimal", _d("25"), _d("0"), _d("100"), unit="pct"),
        Spec("auditor.min_lp_locked_pct", "decimal", _d("99"), _d("0"), _d("100"), unit="pct"),
        Spec("auditor.min_pool_age_min", "decimal", _d("10"), _d("0"), _d("10080"), unit="min"),
        Spec("auditor.max_bundled", "int", 5, 1, 10000, unit="wallets"),
        Spec("auditor.max_delegated_accounts", "int", 10, 0, 100000, unit="accounts"),
        Spec("auditor.retries", "int", 3, 1, 10, unit="attempts"),
        Spec("analyst.mode", "enum", "rules", choices=("rules", "jev")),
        Spec("analyst.min_alive_6h", "decimal", _d("0.70"), _d("0"), _d("1")),
        Spec("analyst.min_organic", "decimal", _d("0.65"), _d("0"), _d("1")),
        Spec("analyst.max_chase", "decimal", _d("0.30"), _d("0"), _d("1")),
        Spec("analyst.min_score", "int", 70, 0, 100),
        Spec("analyst.timeout_s", "int", 10, 1, 120, unit="s"),
        Spec("analyst.rules.alive_volume_h1", "decimal", _d("25000"), _d("1"), _d("1000000000000"), unit="usd"),
        Spec("analyst.rules.organic_buys_h1", "decimal", _d("150"), _d("1"), _d("1000000000000"), unit="buys"),
        Spec("analyst.rules.bundled_penalty", "decimal", _d("0.5"), _d("0"), _d("1")),
        Spec("analyst.rules.bundled_penalty_from_wallets", "int", 3, 1, 10000, unit="wallets"),
        Spec("analyst.rules.flush_drop_m5_pct", "decimal", _d("5"), _d("0"), _d("100"), unit="pct"),
        Spec("analyst.rules.chase_h1_pct", "decimal", _d("80"), _d("0.0001"), _d("1000000000"), unit="pct"),
        Spec("executor.stack_pct", "decimal", _d("8"), _d("0.01"), _d("100"), unit="pct"),
        Spec("executor.max_pool_pct", "decimal", _d("2"), _d("0.01"), _d("100"), unit="pct"),
        Spec("executor.min_ticket_usd", "decimal", _d("47.50"), _d("5"), _d("1000000"), unit="usd"),
        Spec("executor.below_min", "enum", "bump", choices=("bump", "skip")),
        Spec("executor.recheck_liq_drop_pct", "decimal", _d("20"), _d("0"), _d("100"), unit="pct"),
        Spec("executor.recheck_price_up_pct", "decimal", _d("15"), _d("0"), _d("100000"), unit="pct"),
        Spec("executor.confirm_window_min", "decimal", _d("5"), _d("0.1"), _d("1440"), unit="min"),
        Spec("risk.poll_s", "int", 15, 1, 3600, unit="s"),
        Spec("risk.stop_pct", "decimal", _d("6"), _d("0.1"), _d("90"), unit="pct"),
        Spec("risk.trail_start_pct", "decimal", _d("20"), _d("0.1"), _d("100000"), unit="pct"),
        Spec("risk.trail_pct", "decimal", _d("12"), _d("0.1"), _d("90"), unit="pct"),
        Spec("risk.timeout_h", "decimal", _d("3"), _d("0.01"), _d("168"), unit="h"),
        Spec("risk.timeout_min_gain_pct", "decimal", _d("5"), _d("-100"), _d("100000"), unit="pct"),
        Spec("risk.confirm_window_min", "decimal", _d("2"), _d("0.1"), _d("1440"), unit="min"),
        Spec("desk.mode", "enum", "PAPER", choices=("PAPER", "LIVE")),
        Spec("desk.paper_start_stack", "decimal", _d("200"), _d("0"), _d("1000000000000"), unit="usd"),
        Spec("desk.declared_stack", "decimal", _d("0"), _d("0"), _d("1000000000000"), unit="usd"),
        Spec("desk.report_time", "time", "08:00"),
        Spec("desk.notifier", "enum", "telegram", choices=("telegram", "none")),
        Spec("desk.notify_transport", "enum", "dry_run", choices=("dry_run", "live")),
        Spec("desk.rpc_url", "url", "https://api.mainnet-beta.solana.com"),
        Spec("desk.push_rejects", "bool", False),
        Spec("desk.fees_ref_discount", "decimal", _d("0.10"), _d("0"), _d("0.50")),
        Spec("desk.referral_discount_verified", "bool", False),
        Spec("desk.fomo_handle", "handle", "Staticroom"),
        Spec("desk.owner_utc_offset_hours", "int", DEFAULT_OWNER_OFFSET_HOURS, -12, 14, unit="h"),
        Spec("desk.empty_night_ack", "bool", False),
    ]
}

ALIASES = {
    "analyst_mode": "analyst.mode",
    "referral_discount_verified": "desk.referral_discount_verified",
    "fomo_handle": "desk.fomo_handle",
    "fees_ref_discount": "desk.fees_ref_discount",
}

# Changing these keys re-evaluates the ticket warning.
TICKET_KEYS = {
    "executor.stack_pct",
    "executor.min_ticket_usd",
    "executor.below_min",
    "desk.paper_start_stack",
    "desk.declared_stack",
    "desk.mode",
}

CONFIRM_AUDITOR_OFF = "DISABLE AUDITOR"
CONFIRM_REFERRAL = "VERIFY REFERRAL"


def canonical_key(key: str) -> str:
    return ALIASES.get(key, key)


def defaults() -> dict[str, Any]:
    return {key: spec.default for key, spec in SPECS.items()}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raise SettingsError("expected a boolean, not a string or number")


def _as_handle(value: Any) -> str:
    if not isinstance(value, str):
        raise SettingsError("handle must be a string")
    text = value.lstrip("@")
    if not text or any(ch in text for ch in "/?#&\\ \t\r\n") or len(text) > 32:
        raise SettingsError("handle contains unsafe characters")
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_")
    if any(ch not in allowed for ch in text):
        raise SettingsError("handle contains unsafe characters")
    return text


def _as_time(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise SettingsError("time must be HH:MM")
    hour, minute = value.split(":")
    if not (hour.isdigit() and minute.isdigit()):
        raise SettingsError("time must be HH:MM")
    if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
        raise SettingsError("time must be HH:MM")
    return f"{int(hour):02d}:{int(minute):02d}"


def _as_url(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("https://"):
        raise SettingsError("rpc_url must be an https URL")
    if "@" in value or " " in value or any(ch in value for ch in "\r\n\t"):
        raise SettingsError("rpc_url must not carry userinfo or whitespace")
    return value


def normalize_value(key: str, value: Any) -> Any:
    canon = canonical_key(key)
    spec = SPECS.get(canon)
    if spec is None:
        raise SettingsError(f"unknown setting {key}", key=key)
    if spec.type == "bool":
        return _as_bool(value)
    if spec.type == "enum":
        if not isinstance(value, str) or value not in (spec.choices or ()):
            raise SettingsError(
                f"{canon} must be one of {', '.join(spec.choices or ())}",
                key=canon,
            )
        return value
    if spec.type == "handle":
        return _as_handle(value)
    if spec.type == "time":
        return _as_time(value)
    if spec.type == "url":
        return _as_url(value)
    if spec.type == "int":
        if isinstance(value, bool) or isinstance(value, float):
            raise SettingsError(f"{canon} must be an integer", key=canon, minimum=spec.minimum, maximum=spec.maximum)
        try:
            number = int(value) if not isinstance(value, str) else int(value.strip())
        except (TypeError, ValueError) as exc:
            raise SettingsError(
                f"{canon} must be an integer",
                key=canon,
                minimum=spec.minimum,
                maximum=spec.maximum,
            ) from exc
        if isinstance(value, str) and any(ch in value for ch in ".eE"):
            raise SettingsError(f"{canon} must be an integer", key=canon, minimum=spec.minimum, maximum=spec.maximum)
        if number < spec.minimum or number > spec.maximum:
            raise SettingsError(
                f"{canon} out of range {spec.minimum}..{spec.maximum}",
                key=canon,
                minimum=spec.minimum,
                maximum=spec.maximum,
            )
        return number
    number = _signed_decimal(canon, value)
    if canon == "risk.stop_pct" and number < 0:
        number = -number
    elif number < 0 and spec.minimum is not None and spec.minimum >= 0:
        raise SettingsError(
            f"{canon} out of range {spec.minimum}..{spec.maximum}",
            key=canon,
            minimum=spec.minimum,
            maximum=spec.maximum,
        )
    if spec.minimum is not None and (number < D(spec.minimum) or number > D(spec.maximum)):
        raise SettingsError(
            f"{canon} out of range {spec.minimum}..{spec.maximum}",
            key=canon,
            minimum=spec.minimum,
            maximum=spec.maximum,
        )
    return number


def _signed_decimal(key: str, value: Any) -> Decimal:
    if isinstance(value, bool):
        raise SettingsError(f"{key} must be a number", key=key)
    try:
        return D(value)
    except Exception as exc:
        raise SettingsError(f"{key} must be a finite number", key=key) from exc


def encode(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def decode(key: str, raw: str) -> Any:
    spec = SPECS[canonical_key(key)]
    if spec.type == "bool":
        if raw == "true":
            return True
        if raw == "false":
            return False
        raise SettingsError(f"stored boolean corrupt for {key}")
    if spec.type == "int":
        return int(raw)
    if spec.type == "decimal":
        return D(raw)
    return raw


def ticket_floor(stack_pct: Decimal, min_ticket: Decimal) -> Decimal:
    if stack_pct <= 0:
        raise SettingsError("executor.stack_pct must be positive")
    return cents(min_ticket / (stack_pct / Decimal("100")))


def ticket_warning(*, stack: Decimal, stack_pct: Decimal, min_ticket: Decimal, below_min: str) -> str | None:
    floor = ticket_floor(stack_pct, min_ticket)
    if stack >= floor:
        return None
    if below_min == "bump":
        if stack <= 0:
            return f"tickets bumped to ${fmt_money(min_ticket)} but stack is zero"
        pct = cents(min_ticket / stack * Decimal("100"))
        return f"tickets bumped to ${fmt_money(min_ticket)} = {fmt_pct(pct)}% of stack"
    return f"every coin will be skipped, stack under ${fmt_money(floor)}"


def assert_known_defaults() -> None:
    for key, spec in SPECS.items():
        normalize_value(key, spec.default)

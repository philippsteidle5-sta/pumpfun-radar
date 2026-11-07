"""Rules gate, plus a Jev adapter that is mock-only in this chunk."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from desk.money import D, BadNumber, half_even_int

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"
JEV_QUESTIONS = (
    {
        "name": "alive_6h",
        "type": "noul",
        "instructions": "This coin is still actively traded 6 hours from now",
    },
    {
        "name": "organic",
        "type": "noul",
        "instructions": "Most buyers in the last 30 minutes are separate real wallets",
    },
    {
        "name": "flush",
        "type": "noul",
        "instructions": "Price just flushed down and buyers are absorbing it",
    },
    {
        "name": "chase",
        "type": "noul",
        "instructions": "This entry is chasing a green candle that already ran",
    },
)
_FORBIDDEN_STATE = re.compile(r"\b(buy|balance|threshold|opinion)\b", re.I)


@dataclass
class GateResult:
    passed: bool
    score: int | None
    answers: dict[str, Decimal] | None
    failed: list[str]
    model: str
    input_tokens: int
    reason: str | None


def clamp01(value: Decimal) -> Decimal:
    if value < 0:
        return Decimal(0)
    if value > 1:
        return Decimal(1)
    return value


def score_of(alive: Decimal, organic: Decimal, flush: Decimal, chase: Decimal) -> int:
    average = (alive + organic + flush + (Decimal(1) - chase)) / Decimal(4)
    return half_even_int(average * Decimal(100))


def judge(
    alive: Decimal,
    organic: Decimal,
    flush: Decimal,
    chase: Decimal,
    settings: dict,
    *,
    model: str = "rules",
    input_tokens: int = 0,
) -> GateResult:
    failed: list[str] = []
    if alive < D(settings["analyst.min_alive_6h"]):
        failed.append(f"alive_6h {alive} < {D(settings['analyst.min_alive_6h'])}")
    if organic < D(settings["analyst.min_organic"]):
        failed.append(f"organic {organic} < {D(settings['analyst.min_organic'])}")
    if chase > D(settings["analyst.max_chase"]):
        failed.append(f"chase {chase} > {D(settings['analyst.max_chase'])}")
    score = score_of(alive, organic, flush, chase)
    if score < int(settings["analyst.min_score"]):
        failed.append(f"score {score} < {int(settings['analyst.min_score'])}")
    return GateResult(
        passed=not failed,
        score=score,
        answers={"alive_6h": alive, "organic": organic, "flush": flush, "chase": chase},
        failed=failed,
        model=model,
        input_tokens=input_tokens,
        reason=failed[0] if failed else None,
    )


def _need(value: Any, label: str, missing: list[str]) -> Decimal | None:
    if value is None:
        missing.append(label)
        return None
    try:
        return D(value)
    except BadNumber:
        missing.append(label)
        return None


def compute_rules(metrics: dict, bundles: int | None, settings: dict) -> GateResult:
    missing: list[str] = []
    volume_h1 = _need(metrics.get("volume_h1"), "volume.h1", missing)
    buys_h1 = _need(metrics.get("buys_h1"), "txns.h1.buys", missing)
    buys_m5 = _need(metrics.get("buys_m5"), "txns.m5.buys", missing)
    sells_m5 = _need(metrics.get("sells_m5"), "txns.m5.sells", missing)
    change_m5 = _need(metrics.get("price_change_m5"), "priceChange.m5", missing)
    change_h1 = _need(metrics.get("price_change_h1"), "priceChange.h1", missing)
    if bundles is None:
        missing.append("bundles")
    if missing:
        return GateResult(
            False,
            None,
            None,
            [f"missing:{name}" for name in missing],
            "rules",
            0,
            f"missing:{missing[0]}",
        )
    assert volume_h1 is not None and buys_h1 is not None and buys_m5 is not None
    assert sells_m5 is not None and change_m5 is not None and change_h1 is not None
    alive = clamp01(volume_h1 / D(settings["analyst.rules.alive_volume_h1"]))
    organic = clamp01(buys_h1 / D(settings["analyst.rules.organic_buys_h1"]))
    if bundles >= int(settings["analyst.rules.bundled_penalty_from_wallets"]):
        organic = organic * D(settings["analyst.rules.bundled_penalty"])
    drop = D(settings["analyst.rules.flush_drop_m5_pct"])
    if change_m5 <= -drop and buys_m5 >= sells_m5:
        flush = Decimal(1)
    elif change_m5 < 0:
        flush = Decimal("0.5")
    else:
        flush = Decimal(0)
    chase = clamp01(change_h1 / D(settings["analyst.rules.chase_h1_pct"]))
    return judge(alive, organic, flush, chase, settings)


def build_state(facts: dict) -> str:
    """Factual numeric state. No thresholds, no balance, no standalone word buy."""
    sold = "proven" if facts.get("sold_proven") else "unproven"
    parts = [
        f"coin {facts.get('name')} {facts.get('symbol')} Solana",
        f"age_min {facts.get('age_min')}",
        f"liq_usd {facts.get('liq_usd')}",
        f"mcap {facts.get('mcap')}",
        f"m5_buys {facts.get('m5_buys')} m5_sells {facts.get('m5_sells')}",
        f"m5_vol {facts.get('m5_vol')} m5_change {facts.get('m5_change')}",
        f"h1_buys {facts.get('h1_buys')} h1_sells {facts.get('h1_sells')}",
        f"h1_vol {facts.get('h1_vol')} h1_change {facts.get('h1_change')}",
        f"top10_pct {facts.get('top10_pct')}",
        f"dev_pct {facts.get('dev_pct')}",
        f"dev_history {sold}",
        f"lp_locked_pct {facts.get('lp_locked_pct')}",
        f"bundles {facts.get('bundles')}",
        f"delegations {facts.get('delegations')}",
    ]
    state = " | ".join(str(part) for part in parts)
    if len(state) > 3000:
        raise ValueError("state longer than 3000 characters")
    if _FORBIDDEN_STATE.search(state):
        raise ValueError("state contains a forbidden word")
    return state


def state_hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def build_jev_payload(state: str) -> dict:
    if len(state) > 3000:
        raise ValueError("state longer than 3000 characters")
    if _FORBIDDEN_STATE.search(state):
        raise ValueError("state contains a forbidden word")
    return {"model": JEV_MODEL, "state": state, "questions": [dict(item) for item in JEV_QUESTIONS]}


class BadJevAnswer(ValueError):
    pass


def parse_jev_response(payload: Any) -> tuple[dict[str, Decimal], str | None, Any]:
    if not isinstance(payload, dict):
        raise BadJevAnswer("response is not an object")
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise BadJevAnswer("answers missing")
    parsed: dict[str, Decimal] = {}
    for name in ("alive_6h", "organic", "flush", "chase"):
        slot = answers.get(name)
        if not isinstance(slot, dict) or "noul" not in slot:
            raise BadJevAnswer(f"{name} missing")
        try:
            number = D(slot["noul"])
        except BadNumber as exc:
            raise BadJevAnswer(f"{name} not finite") from exc
        if number < 0 or number > 1:
            raise BadJevAnswer(f"{name} out of range")
        parsed[name] = number
    return parsed, payload.get("model") if isinstance(payload.get("model"), str) else None, payload.get("usage")


def _no_answer() -> GateResult:
    return GateResult(False, None, None, ["no answer"], "jev", 0, "no answer")


async def ask_jev(transport, state: str, api_key: str, sleeper, settings: dict) -> GateResult:
    """Timeout: one try, one 2s retry, then no answer. 429: one 60s queue, then reject.

    A bad answer rejects. Rules mode is not a silent fallback.
    """
    payload = build_jev_payload(state)
    headers = {"Authorization": f"Bearer {api_key}"}
    timeouts = 0
    queued = False
    while True:
        try:
            response = await transport.post(JEV_URL, headers=headers, json=payload, timeout=10)
        except TimeoutError:
            timeouts += 1
            if timeouts >= 2:
                return _no_answer()
            await sleeper(2)
            continue
        status = getattr(response, "status_code", 0)
        if status == 429:
            if queued:
                return _no_answer()
            queued = True
            await sleeper(60)
            continue
        if status != 200:
            timeouts += 1
            if timeouts >= 2:
                return _no_answer()
            await sleeper(2)
            continue
        try:
            parsed, model, usage = parse_jev_response(response.json())
        except BadJevAnswer:
            return GateResult(False, None, None, ["bad answer"], "jev", 0, "bad answer")
        tokens = 0
        if isinstance(usage, dict) and isinstance(usage.get("input_tokens"), int):
            tokens = usage["input_tokens"]
        return judge(
            parsed["alive_6h"],
            parsed["organic"],
            parsed["flush"],
            parsed["chase"],
            settings,
            model=model or JEV_MODEL,
            input_tokens=tokens,
        )

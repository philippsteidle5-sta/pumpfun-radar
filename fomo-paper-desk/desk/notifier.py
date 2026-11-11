"""Dry-run outbox. This chunk does not send Telegram or web push."""

from __future__ import annotations

from datetime import datetime

from desk.links import coin_url
from desk.money import fmt_money, fmt_pct


def buy_text(*, mode: str, name: str, size, stack_pct, score, stop_pct, trail_pct) -> str:
    prefix = "PAPER · " if mode == "PAPER" else ""
    return (
        f"{prefix}Buy {name} · ${fmt_money(size)} · {fmt_pct(stack_pct)}% of stack\n"
        f"Auditor 8/8 · Analyst {score} · stop -{fmt_pct(stop_pct)}% · trail {fmt_pct(trail_pct)}%"
    )


def message(kind: str, mint: str, text: str, chain: str = "solana") -> dict:
    link = coin_url(mint, chain)
    if kind == "buy":
        buttons = [
            {"label": "Buy on FOMO", "url": link},
            {"label": "Bought", "callback": f"bought:{mint}"},
            {"label": "Skip", "callback": f"skip:{mint}"},
        ]
    else:
        buttons = [
            {"label": "Sell on FOMO", "url": link},
            {"label": "Sold", "callback": f"sold:{mint}"},
        ]
    return {"kind": kind, "mint": mint, "text": text, "link": link, "buttons": buttons}


class DryRunNotifier:
    """Holds messages in the database. fail_times forces the next N sends to fail."""

    def __init__(self, db, clock, fail_times: int = 0):
        self.db = db
        self.clock = clock
        self.fail_times = fail_times
        self.sent: list[dict] = []

    def send(self, payload: dict) -> bool:
        now = self.clock.now()
        if self.fail_times > 0:
            self.fail_times -= 1
            self.db.outbox_add(now, payload["kind"], payload, "failed", "push failed")
            return False
        self.db.outbox_add(now, payload["kind"], payload, "held", None)
        self.sent.append(payload)
        return True


def incident(db, clock: datetime, text: str, mint: str | None = None) -> dict:
    payload = {"kind": "incident", "text": text, "mint": mint, "link": coin_url(mint) if mint else None}
    db.outbox_add(clock, "incident", payload, "held", None)
    return payload

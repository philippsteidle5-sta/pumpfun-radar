"""In-process queues, Dex rate coordination, and the owner-night boundary."""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone

from desk.clock import ManualClock, SystemClock


class RateCoordinator:
    """At most 250 Dex HTTP calls per rolling minute. A 429 pauses everyone 30s."""

    def __init__(self, clock, limit: int = 250, window_s: int = 60, backoff_s: int = 30):
        self.clock = clock
        self.limit = limit
        self.window = timedelta(seconds=window_s)
        self.backoff = timedelta(seconds=backoff_s)
        self.stamps: list[datetime] = []
        self.backoff_until: datetime | None = None
        self.denied = 0
        self.throttled_429 = 0

    def _trim(self, now: datetime) -> None:
        cutoff = now - self.window
        self.stamps = [stamp for stamp in self.stamps if stamp > cutoff]

    def acquire(self) -> bool:
        now = self.clock.now()
        if self.backoff_until is not None and now < self.backoff_until:
            self.denied += 1
            return False
        self._trim(now)
        if len(self.stamps) >= self.limit:
            self.denied += 1
            return False
        self.stamps.append(now)
        return True

    def note_429(self) -> None:
        self.throttled_429 += 1
        self.backoff_until = self.clock.now() + self.backoff

    def block_reason(self) -> str | None:
        """Why acquire would fail now. HTTP 429 cooldown is not the local request budget."""
        now = self.clock.now()
        if self.backoff_until is not None and now < self.backoff_until:
            return "upstream_429"
        self._trim(now)
        if len(self.stamps) >= self.limit:
            return "local_budget"
        return None

    def next_allowed_at(self) -> datetime | None:
        now = self.clock.now()
        waits = []
        if self.backoff_until is not None and self.backoff_until > now:
            waits.append(self.backoff_until)
        self._trim(now)
        if len(self.stamps) >= self.limit and self.stamps:
            waits.append(self.stamps[0] + self.window)
        return max(waits) if waits else None


class Bus:
    def __init__(self):
        self.audit: asyncio.Queue = asyncio.Queue()
        self.analyst: asyncio.Queue = asyncio.Queue()
        self.executor: asyncio.Queue = asyncio.Queue()

    def put_audit(self, mint: str) -> None:
        self.audit.put_nowait(mint)

    def put_analyst(self, mint: str) -> None:
        self.analyst.put_nowait(mint)

    def put_executor(self, mint: str) -> None:
        self.executor.put_nowait(mint)


def night_id(now: datetime, offset_hours: int) -> str:
    local = now.astimezone(timezone.utc) + timedelta(hours=int(offset_hours))
    return local.date().isoformat()


def night_end_utc(now: datetime, offset_hours: int) -> datetime:
    local = now.astimezone(timezone.utc) + timedelta(hours=int(offset_hours))
    next_day = local.date() + timedelta(days=1)
    midnight = datetime.combine(next_day, time.min, tzinfo=timezone.utc)
    return midnight - timedelta(hours=int(offset_hours))


def make_clock(frozen: datetime | None):
    if frozen is None:
        return SystemClock()
    return ManualClock(frozen)

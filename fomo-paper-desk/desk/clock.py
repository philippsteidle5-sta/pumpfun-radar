"""Injectable clocks. Tests never sleep on wall time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class ManualClock:
    def __init__(self, now: datetime):
        if now.tzinfo is None:
            raise ValueError("clock requires an aware UTC datetime")
        self._now = now.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._now

    def set(self, moment: datetime) -> datetime:
        if moment.tzinfo is None:
            raise ValueError("clock requires an aware UTC datetime")
        self._now = moment.astimezone(timezone.utc)
        return self._now

    def advance(self, *, seconds: float = 0, minutes: float = 0, hours: float = 0) -> datetime:
        self._now += timedelta(seconds=seconds, minutes=minutes, hours=hours)
        return self._now

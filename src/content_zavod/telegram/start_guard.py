"""StartGuard: the in-memory brakes on `/start` (ADR-0015, «Злоупотребление пробным Лимитом»).

One `/start` per telegram id per `START_INTERVAL_SECONDS`; the rest are silently ignored, so a
flood of `/start`s is answered once. The bot is one process, so a dict in its memory is the
whole counter. The guard also remembers when the Глобальные администраторы were last told that
sign-ups hit `SIGNUP_DAILY_LIMIT`, so they hear it once a day, not on every refused `/start`.
A restart forgets both - at worst one extra answer or one extra alert.
"""

from __future__ import annotations

import time
from collections.abc import Callable

START_INTERVAL_SECONDS = 3.0
ALERT_INTERVAL_SECONDS = 24 * 60 * 60.0
# Past this many remembered ids, the ones older than the interval are dropped.
_PRUNE_AT = 10_000


class StartGuard:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last_start: dict[int, float] = {}
        self._alerted_at: float | None = None

    def admit(self, telegram_id: int) -> bool:
        """Whether this `/start` is answered: not if the same id's last one was too recent."""
        now = self._clock()
        last = self._last_start.get(telegram_id)
        if last is not None and now - last < START_INTERVAL_SECONDS:
            return False
        if len(self._last_start) >= _PRUNE_AT:
            self._last_start = {
                owner: at
                for owner, at in self._last_start.items()
                if now - at < START_INTERVAL_SECONDS
            }
        self._last_start[telegram_id] = now
        return True

    def signup_alert_due(self) -> bool:
        """Whether to tell the admins that sign-ups are closed: once per 24 hours."""
        return (
            self._alerted_at is None or self._clock() - self._alerted_at >= ALERT_INTERVAL_SECONDS
        )

    def signup_alert_sent(self) -> None:
        """At least one admin got the alert; until then every refusal tries again."""
        self._alerted_at = self._clock()

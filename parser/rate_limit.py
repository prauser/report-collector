"""Global backoff gate for rate-limited API calls.

When one caller hits a rate limit, all callers for that stage pause together
instead of retrying independently and compounding the problem.
"""
from __future__ import annotations

import asyncio

import structlog

log = structlog.get_logger(__name__)


class RateLimitGate:
    """Global backoff gate for rate-limited API calls.

    When one caller hits a rate limit, all callers pause together.
    The gate is backed by an asyncio.Event:
    - set   → gate open  (normal operation)
    - clear → gate closed (backoff in progress)
    """

    def __init__(self, name: str):
        self._name = name
        self._event = asyncio.Event()
        self._event.set()  # initially open
        self._lock = asyncio.Lock()

    async def wait(self):
        """Block if gate is closed (rate limit active)."""
        await self._event.wait()

    async def trigger_backoff(self, retry_after: float = 60.0):
        """Close gate and sleep for retry_after seconds, then reopen.

        Uses a lock so that only the first caller initiates the backoff;
        subsequent callers are already waiting on the event and will
        unblock naturally when the gate reopens.
        """
        async with self._lock:
            if self._event.is_set():  # only if not already backing off
                self._event.clear()
                log.warning(
                    "rate_limit_backoff",
                    gate=self._name,
                    retry_after=retry_after,
                )
                await asyncio.sleep(retry_after)
                self._event.set()
                log.info("rate_limit_resumed", gate=self._name)

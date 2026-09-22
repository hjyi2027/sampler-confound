"""Pace requests from the provider's own rate-limit headers, before the 429.

Backoff after a 429 is recovery. This is prevention: every response carries
the remaining budget for the current window, and a probe that reads it can
slow down before the window empties instead of bursting into the wall, then
having every worker sleep through the refill, then bursting again — which is
what a 20-worker probe did on 2026-09-21 (a full token bucket and zero
throughput). Three mechanisms, all process-local and thread-safe:

    pacing     when the remaining fraction of any tracked limit falls below
               LOW_WATER, callers wait until the window is expected to reset,
               spread out rather than all at once
    breaker    after CONSECUTIVE_429 throttled responses in a row, every
               caller pauses for the reset (or Retry-After) — one sleep for
               the whole process, not one per worker
    retry      a 429's Retry-After is honoured exactly; without one, a short
               capped wait

Headers differ per provider, so the adapter normalises them: `limits(headers)`
returns {name: (remaining, limit)} for whatever the provider reports, and
`reset_seconds(headers)` the seconds to the window's reset when known. A
provider that reports nothing gets no pacing and the plain retry path.

Limits are per account, not per process. Two probes in parallel share one
bucket and each sees the other's consumption in the headers, which is the
point of reading them — the pacing adapts to what the account has left, not
to what this process has spent.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field

LOW_WATER = 0.10               # fraction of a limit below which callers wait
CONSECUTIVE_429 = 3            # throttled responses in a row that trip the breaker
DEFAULT_WINDOW_S = 60.0        # assumed window when the provider does not say
MAX_WAIT_S = 90.0              # never wait longer than this on one decision


@dataclass
class Pacer:
    """One per provider per process."""

    name: str
    window_s: float = DEFAULT_WINDOW_S
    remaining: dict[str, tuple[float, float]] = field(default_factory=dict)   # name -> (remaining, limit)
    seen_at: float = 0.0
    reset_at: float = 0.0          # when the current window is believed to reset
    throttled_in_a_row: int = 0
    paused_until: float = 0.0
    waits: int = 0                 # for reporting
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # -- called by the transport -------------------------------------------

    def before(self) -> float:
        """Sleep if the account is near its limit or the breaker is open.
        Returns the seconds slept, for the caller's accounting."""
        with self._lock:
            now = time.time()
            wait = 0.0
            if self.paused_until > now:
                wait = self.paused_until - now
            elif self.remaining and now < self.reset_at:
                frac = min(rem / lim for rem, lim in self.remaining.values() if lim > 0)
                if frac < LOW_WATER:
                    # spread the waiters across the remainder of the window
                    wait = (self.reset_at - now) * random.uniform(0.5, 1.0)
            wait = min(wait, MAX_WAIT_S)
        if wait > 0:
            with self._lock:
                self.waits += 1
            time.sleep(wait)
        return wait

    def after(self, status: int, headers: dict, limits: dict, reset_s: float | None) -> float | None:
        """Record what the provider said. Returns the wait a 429 asks for, or None."""
        now = time.time()
        with self._lock:
            if limits:
                self.remaining = dict(limits)
                self.seen_at = now
                self.reset_at = now + (reset_s if reset_s is not None else self.window_s)
            if status == 429:
                self.throttled_in_a_row += 1
                retry_after = _retry_after(headers)
                wait = retry_after if retry_after is not None else min(2.0 * self.throttled_in_a_row, 8.0)
                if self.throttled_in_a_row >= CONSECUTIVE_429:
                    # the whole process backs off, not just this worker
                    self.paused_until = max(self.paused_until, now + min(wait if retry_after else self.window_s, MAX_WAIT_S))
                return wait + random.uniform(0, 1)
            self.throttled_in_a_row = 0
            return None

    def status(self) -> str:
        with self._lock:
            if not self.remaining:
                return f"{self.name}: no rate-limit headers seen"
            parts = ", ".join(f"{k} {int(rem)}/{int(lim)}" for k, (rem, lim) in self.remaining.items())
            return f"{self.name}: {parts}; {self.waits} paced waits"


def _retry_after(headers: dict) -> float | None:
    v = headers.get("Retry-After") or headers.get("retry-after")
    if v is None:
        return None
    try:
        return max(0.0, float(v))
    except ValueError:
        return None


_pacers: dict[str, Pacer] = {}
_pacers_lock = threading.Lock()


def pacer(name: str) -> Pacer:
    with _pacers_lock:
        if name not in _pacers:
            _pacers[name] = Pacer(name)
        return _pacers[name]

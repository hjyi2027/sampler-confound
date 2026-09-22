"""Backoff and pacing against a fake rate-limited provider, through the real
transport. Each test is a failure mode that was met in production first."""

from __future__ import annotations

import threading
import time

import pytest

from samplerconfound import provider, ratelimit
from samplerconfound.adapters import ADAPTERS, _duration
from samplerconfound.ratelimit import Pacer


class Resp:
    def __init__(self, status, headers=None, body=None, text=""):
        self.status_code, self.headers, self._body, self.text = status, headers or {}, body, text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


OK = {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]}


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(ratelimit.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(provider.time, "sleep", lambda s: slept.append(s))
    return slept


@pytest.fixture
def fresh_pacer(monkeypatch):
    monkeypatch.setattr(ratelimit, "_pacers", {})


def _post(sequence):
    """A fake requests.post that returns the given responses in order."""
    it = iter(sequence)
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return next(it)
    return post, calls


# --------------------------------------------------------------------------
# header parsing
# --------------------------------------------------------------------------

def test_openai_style_headers_are_normalised_and_the_tightest_limit_is_tracked():
    ad = ADAPTERS["groq"]
    h = {"x-ratelimit-limit-requests": "30", "x-ratelimit-remaining-requests": "29",
         "x-ratelimit-limit-tokens": "6000", "x-ratelimit-remaining-tokens": "120",
         "x-ratelimit-reset-tokens": "7.5s", "x-ratelimit-reset-requests": "1m2s"}
    assert ad.limits(h) == {"requests": (29.0, 30.0), "tokens": (120.0, 6000.0)}
    assert ad.reset_seconds(h) == 7.5


def test_fireworks_headers_parse_the_generated_token_bucket():
    h = {"x-ratelimit-limit-tokens-generated": "72000", "x-ratelimit-remaining-tokens-generated": "69338",
         "x-ratelimit-limit-tokens-prompt": "7200000", "x-ratelimit-remaining-tokens-prompt": "7193993"}
    lim = ADAPTERS["fireworks"].limits(h)
    assert lim["tokens-generated"] == (69338.0, 72000.0)
    assert ADAPTERS["fireworks"].reset_seconds(h) is None      # not reported; the pacer assumes a window


def test_durations():
    assert _duration("2s") == 2.0 and _duration("1m30s") == 90.0
    assert _duration("250ms") == 0.25 and _duration("0.5") == 0.5
    assert _duration("soon") is None


def test_google_reports_no_limits():
    assert ADAPTERS["google"].limits({"x-ratelimit-limit-tokens": "1"}) == {}


# --------------------------------------------------------------------------
# the pacer
# --------------------------------------------------------------------------

def test_pacer_waits_before_the_window_empties(no_sleep):
    p = Pacer("t", window_s=60)
    p.after(200, {}, {"tokens": (900.0, 1000.0)}, None)
    assert p.before() == 0.0, "plenty left: no wait"
    p.after(200, {}, {"tokens": (50.0, 1000.0)}, None)     # 5% left
    w = p.before()
    assert 0 < w <= 60, "near empty: wait toward the reset, not burst"


def test_three_429s_pause_the_whole_process_once(no_sleep):
    p = Pacer("t", window_s=60)
    assert p.after(429, {}, {}, None) is not None
    assert p.paused_until == 0.0
    p.after(429, {}, {}, None)
    p.after(429, {"Retry-After": "12"}, {}, None)
    assert p.paused_until > time.time() + 5, "the breaker is open for everyone"
    assert p.before() > 0
    p.after(200, {}, {}, None)
    assert p.throttled_in_a_row == 0


def test_retry_after_is_honoured_exactly(no_sleep):
    p = Pacer("t")
    w = p.after(429, {"Retry-After": "3"}, {}, None)
    assert 3.0 <= w < 4.0


# --------------------------------------------------------------------------
# the transport, end to end against a fake provider
# --------------------------------------------------------------------------

def test_a_429_burst_is_survived_with_short_waits_not_a_64s_ladder(monkeypatch, no_sleep, fresh_pacer):
    post, calls = _post([Resp(429, {"Retry-After": "2"}), Resp(429, {"Retry-After": "2"}), Resp(200, body=OK)])
    monkeypatch.setattr(provider.requests, "post", post)
    out = provider._http("u", {}, {"m": 1}, 10, adapter=ADAPTERS["groq"])
    assert out == OK and len(calls) == 3
    assert all(s < 10 for s in no_sleep), f"waits were {no_sleep}"


def test_a_hung_request_is_given_up_after_two_retries_not_ten(monkeypatch, no_sleep, fresh_pacer):
    def post(*a, **k):
        raise provider.requests.ReadTimeout("hung")
    monkeypatch.setattr(provider.requests, "post", post)
    with pytest.raises(provider.Transient, match="network"):
        provider._http("u", {}, {}, 1, adapter=ADAPTERS["fireworks"])


def test_a_recurring_500_is_reported_after_four_tries(monkeypatch, no_sleep, fresh_pacer):
    post, calls = _post([Resp(500)] * 10)
    monkeypatch.setattr(provider.requests, "post", post)
    with pytest.raises(provider.Transient, match="500"):
        provider._http("u", {}, {}, 1, adapter=ADAPTERS["fireworks"])
    assert len(calls) == provider.SERVER_ERROR_RETRIES + 1
    assert sum(no_sleep) < 120


def test_low_headroom_paces_the_next_call_before_any_429(monkeypatch, no_sleep, fresh_pacer):
    """Prevention, not recovery: the first response says 3% of the bucket is
    left; the second call must wait for the reset without a 429 ever arriving."""
    h = {"x-ratelimit-limit-tokens-generated": "72000", "x-ratelimit-remaining-tokens-generated": "2000"}
    post, calls = _post([Resp(200, h, OK), Resp(200, h, OK)])
    monkeypatch.setattr(provider.requests, "post", post)
    ad = ADAPTERS["fireworks"]
    provider._http("u", {}, {}, 1, adapter=ad)
    assert not no_sleep
    provider._http("u", {}, {}, 1, adapter=ad)
    assert no_sleep and 0 < no_sleep[0] <= ratelimit.MAX_WAIT_S


def test_pacer_is_shared_across_threads_in_a_process(fresh_pacer):
    a = ratelimit.pacer("fireworks")
    seen = []
    ts = [threading.Thread(target=lambda: seen.append(ratelimit.pacer("fireworks"))) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert all(s is a for s in seen)

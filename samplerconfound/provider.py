"""The one place a request goes to the provider.

Every script used to carry its own copy of the retry loop, and three of them had
drifted apart in what they retried and what they returned. This is the single
transport: cache lookup, then at most one network call with backoff, then store.
The sweep, the pilot and every probe call `complete()` and nothing else.

Returns (response_json, error). Exactly one is non-None. Errors are strings a
caller can classify:

    "rejected: <provider message>"   HTTP 400 — the request was refused. Never
                                     retried and never cached, because it is a
                                     finding, not a transient.
    "http <code>"                    a non-retryable status
    "network" / "exhausted"          could not get an answer after retries

Only successful responses enter the cache, so a 429 storm is retried next time
rather than remembered as a result.
"""

from __future__ import annotations

import os
import random
import time
from pathlib import Path

import requests

from .cache import CacheMiss, ResponseCache

BASE = "https://api.fireworks.ai/inference/v1/chat/completions"
PREFIX = "accounts/fireworks/models/"
MAX_RETRIES = 6
BACKOFF_BASE = 2.0
ROOT = Path(__file__).resolve().parent.parent

_default_cache: ResponseCache | None = None


def default_cache() -> ResponseCache:
    global _default_cache
    if _default_cache is None:
        _default_cache = ResponseCache()
    return _default_cache


def load_key() -> str:
    key = os.environ.get("FIREWORKS_API_KEY")
    if not key and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("FIREWORKS_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        raise SystemExit("no FIREWORKS_API_KEY (env or .env)")
    return key


class Rejected(Exception):
    pass


class Transient(Exception):
    pass


def _send(key: str, body: dict, timeout: float) -> dict:
    """One attempt sequence with backoff. Raises Rejected or Transient."""
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(BASE, headers={"Authorization": f"Bearer {key}"},
                              json=body, timeout=timeout)
        except requests.RequestException as e:
            last = f"network: {e.__class__.__name__}"
            time.sleep(BACKOFF_BASE * 2 ** attempt + random.uniform(0, 1))
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code == 400:
            try:
                msg = r.json()["error"]["message"][:160]
            except Exception:
                msg = r.text[:160]
            raise Rejected(f"rejected: {msg}")
        if r.status_code in (429, 500, 502, 503, 504):
            last = f"http {r.status_code}"
            time.sleep(BACKOFF_BASE * 2 ** attempt + random.uniform(0, 1))
            continue
        raise Transient(f"http {r.status_code}")
    raise Transient(last or "exhausted")


def complete(key: str, body: dict, replicate: int, *,
             cache: ResponseCache | None = None,
             timeout: float = 300.0) -> tuple[dict | None, str | None]:
    """Cached completion. The model in `body` may be bare or fully qualified."""
    if not body["model"].startswith(PREFIX):
        body = {**body, "model": PREFIX + body["model"]}
    cache = cache or default_cache()
    try:
        return cache.fetch(body, replicate, lambda b: _send(key, b, timeout)), None
    except CacheMiss as e:
        return None, f"offline: {e}"
    except Rejected as e:
        return None, str(e)
    except Transient as e:
        return None, str(e)

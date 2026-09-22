"""The one place a request goes to a provider.

Every script used to carry its own copy of the retry loop, and three of them had
drifted apart in what they retried and what they returned. This is the single
transport: adapter encode, cache lookup, then at most one network call with
backoff, then store, then adapter decode. The sweep, the pilot and every probe
call `complete()` and nothing else, and see only the canonical request and a
`Completion` — never a vendor's wire shape (see adapters.py).

Returns (Completion, error). Exactly one is non-None. Errors are strings a
caller can classify:

    "rejected: <provider message>"   HTTP 400/401/403/404/422 — the request was
                                     refused. Never retried and never cached,
                                     because it is a finding, not a transient.
    "offline: ..."                   cache miss with SAMPLERCONFOUND_OFFLINE set
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

from .adapters import ADAPTERS, Completion
from .cache import CacheMiss, ResponseCache

DEFAULT_PROVIDER = "fireworks"
MAX_RETRIES = 10
NETWORK_RETRIES = 2      # attempts after a timeout or dropped connection
SERVER_ERROR_RETRIES = 3 # attempts after a 5xx
BACKOFF_BASE = 2.0
ROOT = Path(__file__).resolve().parent.parent

_default_cache: ResponseCache | None = None


def default_cache() -> ResponseCache:
    global _default_cache
    if _default_cache is None:
        _default_cache = ResponseCache()
    return _default_cache


def _dotenv() -> dict[str, str]:
    out = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def load_key(provider: str = DEFAULT_PROVIDER, required: bool = True) -> str | None:
    """The API key for a provider, from the environment or .env (gitignored)."""
    ad = ADAPTERS[provider]
    key = os.environ.get(ad.env) or _dotenv().get(ad.env)
    if not key and required:
        raise SystemExit(
            f"no {ad.env} (env or .env). Get one at {ad.signup} "
            f"and add `{ad.env}=...` to .env"
        )
    return key or None


class Rejected(Exception):
    pass


class Transient(Exception):
    pass


def _http(url: str, headers: dict, payload: dict, timeout: float,
          retries: int = MAX_RETRIES) -> dict:
    """One attempt sequence with backoff. Raises Rejected or Transient."""
    last = None
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as e:
            # A dropped connection or a read timeout is retried twice, not up
            # to the full retry budget: a request the provider will not finish
            # (repetition_penalty=2.0 hangs mid-generation on Fireworks) would
            # otherwise hold a worker for retries x timeout — observed as ten
            # attempts at 180s each, half an hour per call.
            last = f"network: {e.__class__.__name__}"
            if attempt >= NETWORK_RETRIES:
                break
            time.sleep(BACKOFF_BASE * 2 ** attempt + random.uniform(0, 1))
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (400, 401, 403, 404, 422):
            # 401/403: bad key. 404: no such model on this provider. 422: some
            # providers use it for a refused parameter. None are transient and
            # none are cached — each is a finding about the provider.
            try:
                j = r.json()
                msg = (j.get("error", {}) or {}).get("message") or j.get("message") or r.text
            except Exception:
                msg = r.text
            raise Rejected(f"rejected ({r.status_code}): {str(msg)[:160]}")
        if r.status_code == 429:
            # Rate limits refill per minute. Exponential backoff to 64s here is
            # the wrong shape: every worker sleeps through the refill, then
            # they all burst again — observed as a full token bucket and zero
            # throughput. Honour Retry-After when given; otherwise wait a few
            # jittered seconds and try again, up to the retry budget.
            last = "http 429"
            ra = r.headers.get("Retry-After")
            try:
                wait = float(ra) if ra else min(2.0 * (attempt + 1), 8.0)
            except ValueError:
                wait = 4.0
            time.sleep(wait + random.uniform(0, 1))
            continue
        if r.status_code in (500, 502, 503, 504):
            # A server error that recurs is a finding about the request
            # (ignore_eos on kimi-k3 is a 500 every time); four tries, then
            # report it. Ten exponential tries was seventeen minutes a call.
            last = f"http {r.status_code}"
            if attempt >= SERVER_ERROR_RETRIES:
                break
            time.sleep(min(BACKOFF_BASE * 2 ** attempt, 20.0) + random.uniform(0, 1))
            continue
        raise Transient(f"http {r.status_code}")
    raise Transient(last or "exhausted")


def list_models(key: str, provider: str = DEFAULT_PROVIDER, timeout: float = 30.0) -> list[dict]:
    """What the provider says it serves right now, as [{"id", "chat"}].

    Uncached on purpose: the catalogue is the thing that moves.
    """
    ad = ADAPTERS[provider]
    r = requests.get(ad.catalogue_url(), headers=ad.headers(key), timeout=timeout)
    if r.status_code != 200:
        raise Transient(f"catalogue http {r.status_code}: {r.text[:120]}")
    return ad.catalogue(r.json())


def call(key: str, request: dict, *, provider: str = DEFAULT_PROVIDER,
         timeout: float = 60.0, retries: int = MAX_RETRIES) -> Completion:
    """One uncached completion. Raises Rejected or Transient.

    For key and liveness checks only; everything that is a measurement goes
    through `complete()` so it is cached under its request hash.
    """
    ad = ADAPTERS[provider]
    wire, dropped = ad.encode(request)
    raw = _http(ad.url(wire), ad.headers(key), ad.payload(wire), timeout, retries=retries)
    return ad.decode(raw, dropped)


def complete(key: str, request: dict, replicate: int, *,
             provider: str = DEFAULT_PROVIDER,
             cache: ResponseCache | None = None,
             timeout: float = 300.0) -> tuple[Completion | None, str | None]:
    """Cached completion of one canonical request against one provider.

    The cache is keyed on the WIRE body plus the provider name, so the same
    model name on two providers — "llama-3.3-70b" on Groq and on Cerebras — can
    never collide, and a parameter the adapter could not send is not in the key
    because it did not shape the response.
    """
    ad = ADAPTERS[provider]
    wire, dropped = ad.encode(request)
    keyed = {**wire, "_provider": ad.name}
    cache = cache or default_cache()

    def send(b: dict) -> dict:
        payload = ad.payload({k: v for k, v in b.items() if k != "_provider"})
        return _http(ad.url(wire), ad.headers(key), payload, timeout)

    try:
        raw = cache.fetch(keyed, replicate, send)
    except CacheMiss as e:
        return None, f"offline: {e}"
    except Rejected as e:
        return None, str(e)
    except Transient as e:
        return None, str(e)
    return ad.decode(raw, dropped), None

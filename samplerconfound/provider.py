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

# Every provider here speaks the OpenAI chat-completions shape with a Bearer
# key, so one transport serves all of them. `prefix` is what the provider wants
# prepended to a bare model name (Fireworks is the odd one out); `signup` is
# where a human gets the key, because that is the one step this repo cannot do.
PROVIDERS = {
    "fireworks": {
        "base": "https://api.fireworks.ai/inference/v1/chat/completions",
        "env": "FIREWORKS_API_KEY",
        "prefix": "accounts/fireworks/models/",
        "signup": "https://fireworks.ai/",
    },
    "groq": {
        "base": "https://api.groq.com/openai/v1/chat/completions",
        "env": "GROQ_API_KEY",
        "prefix": "",
        "signup": "https://console.groq.com/keys",
    },
    "cerebras": {
        "base": "https://api.cerebras.ai/v1/chat/completions",
        "env": "CEREBRAS_API_KEY",
        "prefix": "",
        "signup": "https://cloud.cerebras.ai/",
    },
    "google": {
        # AI Studio exposes an OpenAI-compatible surface alongside the native one.
        "base": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "env": "GOOGLE_API_KEY",
        "prefix": "",
        "signup": "https://aistudio.google.com/apikey",
    },
    "openrouter": {
        "base": "https://openrouter.ai/api/v1/chat/completions",
        "env": "OPENROUTER_API_KEY",
        "prefix": "",
        "signup": "https://openrouter.ai/keys",
    },
    "nvidia": {
        "base": "https://integrate.api.nvidia.com/v1/chat/completions",
        "env": "NVIDIA_API_KEY",
        "prefix": "",
        "signup": "https://build.nvidia.com/",
    },
    "mistral": {
        "base": "https://api.mistral.ai/v1/chat/completions",
        "env": "MISTRAL_API_KEY",
        "prefix": "",
        "signup": "https://console.mistral.ai/api-keys",
    },
}
DEFAULT_PROVIDER = "fireworks"
BASE = PROVIDERS[DEFAULT_PROVIDER]["base"]
PREFIX = PROVIDERS[DEFAULT_PROVIDER]["prefix"]
MAX_RETRIES = 6
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
    env = PROVIDERS[provider]["env"]
    key = os.environ.get(env) or _dotenv().get(env)
    if not key and required:
        raise SystemExit(
            f"no {env} (env or .env). Get one at {PROVIDERS[provider]['signup']} "
            f"and add `{env}=...` to .env"
        )
    return key or None


class Rejected(Exception):
    pass


class Transient(Exception):
    pass


def _send(key: str, body: dict, timeout: float, base: str = BASE) -> dict:
    """One attempt sequence with backoff. Raises Rejected or Transient."""
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(base, headers={"Authorization": f"Bearer {key}"},
                              json=body, timeout=timeout)
        except requests.RequestException as e:
            last = f"network: {e.__class__.__name__}"
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
        if r.status_code in (429, 500, 502, 503, 504):
            last = f"http {r.status_code}"
            time.sleep(BACKOFF_BASE * 2 ** attempt + random.uniform(0, 1))
            continue
        raise Transient(f"http {r.status_code}")
    raise Transient(last or "exhausted")


def complete(key: str, body: dict, replicate: int, *,
             provider: str = DEFAULT_PROVIDER,
             cache: ResponseCache | None = None,
             timeout: float = 300.0) -> tuple[dict | None, str | None]:
    """Cached completion against one provider.

    The provider name is folded into the cached body as `_provider`, so the same
    model name on two providers — "llama-3.3-70b" on Groq and on Cerebras — can
    never collide in the cache. That field is stripped before sending.
    """
    spec = PROVIDERS[provider]
    prefix = spec["prefix"]
    if prefix and not body["model"].startswith(prefix):
        body = {**body, "model": prefix + body["model"]}
    keyed = {**body, "_provider": provider}
    cache = cache or default_cache()

    def send(b: dict) -> dict:
        wire = {k: v for k, v in b.items() if k != "_provider"}
        return _send(key, wire, timeout, base=spec["base"])

    try:
        return cache.fetch(keyed, replicate, send), None
    except CacheMiss as e:
        return None, f"offline: {e}"
    except Rejected as e:
        return None, str(e)
    except Transient as e:
        return None, str(e)

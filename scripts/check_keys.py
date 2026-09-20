#!/usr/bin/env python3
"""Which provider keys are present, and do they work?

One cheap call per provider whose key is set — a trivial prompt at max_tokens=8
against a model the provider is known to serve — reported as one line each.
Providers with no key are listed with the URL to get one, since signing up is
the one step this repo cannot do for you.

Nothing here is cached: a key check that hit the cache would tell you the key
worked last week. The cache is bypassed on purpose.

    python3 scripts/check_keys.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.provider import PROVIDERS, Rejected, Transient, _send, load_key

# A model each provider serves on its free or entry tier. If one of these has
# been withdrawn the check reports a 404 with the provider's message, which is
# itself informative — this repo has watched three models vanish in a month.
SMOKE_MODEL = {
    "fireworks": "accounts/fireworks/models/gpt-oss-120b",
    "groq": "llama-3.3-70b-versatile",
    "cerebras": "llama-3.3-70b",
    "google": "gemini-2.0-flash",
    "openrouter": "openai/gpt-oss-20b",
    "nvidia": "meta/llama-3.3-70b-instruct",
    "mistral": "mistral-small-latest",
}


def main() -> int:
    ok = missing = bad = 0
    print(f"{'provider':<12}{'key':<9}{'status':<10}  detail")
    for name, spec in PROVIDERS.items():
        key = load_key(name, required=False)
        if not key:
            missing += 1
            print(f"{name:<12}{'—':<9}{'MISSING':<10}  get one at {spec['signup']}, "
                  f"add {spec['env']}=... to .env")
            continue
        body = {"model": SMOKE_MODEL[name],
                "messages": [{"role": "user", "content": "Reply with the word OK."}],
                "max_tokens": 8, "temperature": 0.0}
        t = time.time()
        try:
            d = _send(key, body, timeout=60, base=spec["base"])
            txt = (d["choices"][0]["message"].get("content") or "").strip()
            ok += 1
            # HTTP 200 is the answer to "does the key work". Empty content on a
            # reasoning model at a tiny max_tokens is expected, not a failure.
            shown = repr(txt[:30]) if txt else "(empty: reasoning model spent the budget)"
            print(f"{name:<12}{'set':<9}{'OK':<10}  {time.time()-t:.1f}s  "
                  f"{SMOKE_MODEL[name]} -> {shown}")
        except Rejected as e:
            bad += 1
            msg = str(e)
            kind = "BAD KEY" if "(401)" in msg or "(403)" in msg else "REJECTED"
            print(f"{name:<12}{'set':<9}{kind:<10}  {msg[:90]}")
        except Transient as e:
            bad += 1
            print(f"{name:<12}{'set':<9}{'UNREACH':<10}  {e}")
    print(f"\n{ok} working, {bad} set but failing, {missing} missing")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())

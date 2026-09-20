#!/usr/bin/env python3
"""What every cached response cost, per provider and model, from measured usage.

Every billed call is in the cache with its usage block, so spend is a sum over
the cache, not an estimate. Prices are the pinned table in pricing.py; a model
with no price is listed with its token counts and no dollar figure rather than
guessed at. Cached prompt tokens are charged at the input rate here, which
slightly OVERSTATES the bill (providers discount them).

    python3 scripts/probe_spend.py            # everything in the cache
    python3 scripts/probe_spend.py --since 2026-09-20
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.cache import DEFAULT_DIR
from samplerconfound.pricing import PRICES, PRICING_DATE


def usage_of(entry: dict) -> tuple[int, int]:
    r = entry["response"]
    u = r.get("usage") or {}
    if u:
        return int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
    u = r.get("usageMetadata") or {}                      # Google
    return (int(u.get("promptTokenCount") or 0),
            int(u.get("candidatesTokenCount") or 0) + int(u.get("thoughtsTokenCount") or 0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="YYYY-MM-DD; count only entries stored on or after")
    ap.add_argument("--cache", type=Path, default=DEFAULT_DIR)
    args = ap.parse_args()
    since = time.mktime(time.strptime(args.since, "%Y-%m-%d")) if args.since else 0

    tot = defaultdict(lambda: [0, 0, 0])           # (provider, model) -> [calls, in, out]
    for f in args.cache.rglob("*.json"):
        try:
            e = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if e.get("stored_at", 0) < since:
            continue
        b = e["body"]
        model = b["model"].split("/")[-1]
        i, o = usage_of(e)
        t = tot[(b.get("_provider", "fireworks"), model)]
        t[0] += 1; t[1] += i; t[2] += o

    print(f"prices as of {PRICING_DATE}; cached prompt tokens charged at the input rate (overstates)")
    print(f"{'provider':<11}{'model':<32}{'calls':>7}{'in_tok':>9}{'out_tok':>9}{'out/call':>9}{'usd':>8}")
    grand = 0.0
    for (prov, model), (n, i, o) in sorted(tot.items(), key=lambda kv: -kv[1][2]):
        if model in PRICES:
            p_in, p_out = PRICES[model]
            usd = (i * p_in + o * p_out) / 1e6
            grand += usd
            usd_s = f"{usd:8.3f}"
        else:
            usd_s = "  unpriced"
        print(f"{prov:<11}{model:<32}{n:>7}{i:>9}{o:>9}{o / n:>9.0f}{usd_s}")
    print(f"{'':<43}{sum(t[0] for t in tot.values()):>7}{'':>27}{grand:8.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

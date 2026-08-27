#!/usr/bin/env python3
"""Does Fireworks actually honour each sampler parameter, and what does a
generation cost in tokens?

The companion to scripts/probe_sampler_support.py, which asks the same question
of the Anthropic API. Same distinction, which is the whole point of probing:

  REJECTED   the parameter errors. Loud, safe, immediately visible.
  IGNORED    the parameter is accepted and does nothing. Silent and corrupting.

An ignored parameter does not break anything visible. The cell is accepted, the
grid stays balanced, the decomposition runs, and the sampler variance component
is quietly halved because two of the six cells were secretly duplicates. So
acceptance is never treated as support: every accepted parameter gets a
behavioural check with a value so extreme that honouring it MUST show up.

    top_p = 0.01  |
    top_k = 1     |  at temperature 1.5, each of these must collapse the output
    min_p = 0.9   |  distribution to (almost) a single value

If diversity stays high under top_k=1, top_k is being dropped on the floor.

Also measures output-token cost per problem, including reasoning tokens, because
the 2026 serverless catalogue is entirely reasoning models and a max_tokens cap
that truncates the chain-of-thought produces an unparseable verdict rather than a
wrong one — a failure that would correlate with temperature and land in exactly
the component the paper reports.

    python3 scripts/probe_fireworks.py --models gpt-oss-20b nemotron-...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = "https://api.fireworks.ai/inference/v1/chat/completions"
PREFIX = "accounts/fireworks/models/"

# High-entropy prompt with a one-token-ish answer: cheap to sample many times and
# exactly comparable. A prompt with one obvious answer would look "deterministic"
# under every setting and prove nothing.
DIVERSITY_PROMPT = "Name one random common English noun. Reply with the word only, nothing else."
N_SAMPLES = 8

# Each probe is (label, params, expectation). `collapse` means: if the provider
# honours this, diversity must fall to ~1. `expand` means diversity must be high.
PROBES = [
    ("temperature=0",        {"temperature": 0.0},                      "collapse"),
    ("temperature=1.5",      {"temperature": 1.5},                      "expand"),
    ("top_p=0.01",           {"temperature": 1.5, "top_p": 0.01},       "collapse"),
    ("top_k=1",              {"temperature": 1.5, "top_k": 1},          "collapse"),
    ("min_p=0.9",            {"temperature": 1.5, "min_p": 0.9},        "collapse"),
]


def call(key: str, model: str, messages: list[dict], max_tokens: int, **params):
    body = {"model": PREFIX + model, "messages": messages,
            "max_tokens": max_tokens, **params}
    r = requests.post(BASE, headers={"Authorization": f"Bearer {key}",
                                     "Content-Type": "application/json"},
                      json=body, timeout=180)
    return r


def diversity(key: str, model: str, params: dict) -> tuple[str, Counter | None, str]:
    """Return (status, outputs, detail). status in REJECTED / OK / ERROR."""
    outs: Counter = Counter()
    for _ in range(N_SAMPLES):
        r = call(key, model, [{"role": "user", "content": DIVERSITY_PROMPT}],
                 max_tokens=2048, **params)
        if r.status_code != 200:
            try:
                msg = r.json()["error"]["message"]
            except Exception:
                msg = r.text[:200]
            return ("REJECTED" if r.status_code == 400 else "ERROR", None,
                    f"HTTP {r.status_code}: {msg}")
        msg = r.json()["choices"][0]["message"]
        outs[(msg.get("content") or "").strip().lower().rstrip(".")] += 1
    return "OK", outs, ""


def probe_params(key: str, model: str) -> dict:
    print(f"\n=== {model} — sampler support ===")
    results = {}
    for label, params, expect in PROBES:
        status, outs, detail = diversity(key, model, params)
        if status != "OK":
            print(f"  {label:<18} {status:<9} {detail}")
            results[label] = {"status": status, "detail": detail}
            continue
        distinct = len(outs)
        # A collapse probe that stays diverse means the parameter was accepted
        # and thrown away. That is the failure this script exists to catch.
        if expect == "collapse":
            verdict = "HONOURED" if distinct <= 2 else "IGNORED"
        else:
            verdict = "HONOURED" if distinct >= 3 else "SUSPECT (low diversity)"
        print(f"  {label:<18} {verdict:<9} {distinct}/{N_SAMPLES} distinct  "
              f"{list(outs)[:4]}")
        results[label] = {"status": "OK", "distinct": distinct,
                          "verdict": verdict, "outputs": dict(outs)}
    return results


def probe_cost(key: str, model: str, n_problems: int, effort: str | None) -> dict:
    from samplerconfound.benchmarks import sweep_split
    from samplerconfound.config import FIXED

    print(f"\n=== {model} — token cost (reasoning_effort={effort}) ===")
    stats = []
    for benchmark in ("math500", "aime"):
        problems = sweep_split(benchmark)[:n_problems]
        for p in problems:
            params: dict = {"temperature": 0.7, "top_p": 0.95}
            if effort:
                params["reasoning_effort"] = effort
            r = call(key, model,
                     [{"role": "user",
                       "content": f"{FIXED['prompt_template']}\n\n{p.problem}"}],
                     max_tokens=16384, **params)
            if r.status_code != 200:
                print(f"  {benchmark} {p.id}: HTTP {r.status_code} {r.text[:160]}")
                continue
            d = r.json()
            u = d["usage"]
            msg = d["choices"][0]["message"]
            reasoning = (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
            finish = d["choices"][0]["finish_reason"]
            content = (msg.get("content") or "").strip()
            ok = "Answer:" in content
            stats.append({"benchmark": benchmark, "id": p.id, "in": u["prompt_tokens"],
                          "out": u["completion_tokens"], "reasoning": reasoning,
                          "finish": finish, "has_answer": ok})
            print(f"  {benchmark:<8} {p.id:<28} in={u['prompt_tokens']:>5} "
                  f"out={u['completion_tokens']:>6} (reasoning {reasoning:>6}) "
                  f"{finish:<10} answer={'yes' if ok else 'NO'}")

    for benchmark in ("math500", "aime"):
        rows = [s for s in stats if s["benchmark"] == benchmark]
        if rows:
            avg = sum(s["out"] for s in rows) / len(rows)
            mx = max(s["out"] for s in rows)
            print(f"  -> {benchmark}: mean {avg:,.0f} output tokens, max {mx:,}, "
                  f"{sum(s['has_answer'] for s in rows)}/{len(rows)} parseable")
    return {"stats": stats}


def main() -> int:
    key = os.environ.get("FIREWORKS_API_KEY")
    if not key:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("FIREWORKS_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    if not key:
        print("no FIREWORKS_API_KEY (env or .env)", file=sys.stderr)
        return 1

    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True, help="bare model names")
    ap.add_argument("--cost-problems", type=int, default=2)
    ap.add_argument("--effort", default=None, help="reasoning_effort, e.g. low")
    ap.add_argument("--skip-params", action="store_true")
    ap.add_argument("--skip-cost", action="store_true")
    ap.add_argument("--out", type=Path, default=ROOT / "runs" / "probe_fireworks.json")
    args = ap.parse_args()

    report: dict = {}
    for model in args.models:
        report[model] = {}
        if not args.skip_params:
            report[model]["params"] = probe_params(key, model)
        if not args.skip_cost:
            report[model]["cost"] = probe_cost(key, model, args.cost_problems, args.effort)

    out = args.out if args.out.is_absolute() else ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nwrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

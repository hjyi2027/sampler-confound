#!/usr/bin/env python3
"""Is temperature-0 nondeterminism a property of the model, or of concurrent load?

The determinism probe issued its ten identical requests five at a time. Batch
composition is a known mechanism for nondeterminism in LLM inference (requests
batched together change the reduction order of the kernels), so concurrency
was part of the treatment, and "this model is nondeterministic" could not be
separated from "this model is nondeterministic under concurrent load".

This runs both arms on the same day, per (model, prompt):

    sequential   ten identical T=0 requests, each sent only after the previous
                 one returned (replicates 1000-1009)
    concurrent   the same ten requests, all in flight at once (2000-2009)

and reports two agreement measures for each, because the original report used
one and a reader can take it for the other:

    modal share      fraction of the ten equal to the most common response
    pairwise match   fraction of the 45 pairs that are byte-identical

Arms alternate which goes first by prompt, so a time trend within the session
does not load onto one arm. "Sequential" is sequential from this client: the
provider still batches our request with other customers' traffic, which no
client can control. What the contrast isolates is the load this probe itself
adds.

    python3 scripts/probe_sequential.py --all
    SAMPLERCONFOUND_OFFLINE=1 python3 scripts/probe_sequential.py --all   # re-report
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.paths import iso, resolve_out, show, window
from samplerconfound.provider import DEFAULT_PROVIDER, complete, load_key
from scripts.probe_determinism import prompts

N = 10
SEQ_BASE, CONC_BASE = 1000, 2000
OUT = ROOT / "runs" / "matrix" / "fireworks" / "determinism_sequential.json"


def call(key, provider, model, text, rep, max_tokens, effort):
    body = {"model": model, "messages": [{"role": "user", "content": text}],
            "max_tokens": max_tokens, "temperature": 0.0}
    if effort:
        body["reasoning_effort"] = effort
    c, err = complete(key, body, rep, provider=provider, timeout=120)
    return (None, err, None) if err else (c.text, None, c.collected_at)


def agreement(texts: list[str]) -> dict:
    if not texts:
        return {"n": 0, "modal": float("nan"), "pairwise": float("nan"), "distinct": 0}
    modal = Counter(texts).most_common(1)[0][1] / len(texts)
    pairs = list(combinations(texts, 2))
    pairwise = sum(a == b for a, b in pairs) / len(pairs) if pairs else float("nan")
    return {"n": len(texts), "modal": modal, "pairwise": pairwise, "distinct": len(set(texts))}


def run_arm(key, provider, model, text, base, concurrent, max_tokens, effort):
    reps = range(base, base + N)
    if concurrent:
        with ThreadPoolExecutor(max_workers=N) as pool:
            outs = list(pool.map(lambda r: call(key, provider, model, text, r, max_tokens, effort), reps))
    else:
        outs = [call(key, provider, model, text, r, max_tokens, effort) for r in reps]
    texts = [t for t, e, _ in outs if e is None]
    errs = [e for _, e, _ in outs if e]
    return {**agreement(texts), "errors": errs[:2], "collected": window(w for _, _, w in outs)}


def one_model(key, provider, model, P, max_tokens, effort):
    rows = []
    for i, (pid, text) in enumerate(P.items()):
        arms = [("sequential", SEQ_BASE, False), ("concurrent", CONC_BASE, True)]
        if i % 2:
            arms.reverse()
        res = {name: run_arm(key, provider, model, text, base, conc, max_tokens, effort)
               for name, base, conc in arms}
        rows.append({"provider": provider, "model": model, "prompt": pid, **res})
        s, c = res["sequential"], res["concurrent"]
        print(f"  {model[:28]:<28} {pid:<10} seq modal {s['modal']:.0%} pair {s['pairwise']:.0%}"
              f"   conc modal {c['modal']:.0%} pair {c['pairwise']:.0%}", flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+")
    ap.add_argument("--all", action="store_true", help="every model in the coverage table")
    ap.add_argument("--provider", default=DEFAULT_PROVIDER)
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--effort", default="low")
    ap.add_argument("--parallel-models", type=int, default=6,
                    help="models probed at once; within a model every arm is as described")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    if args.all:
        from scripts.probe_matrix import collect
        models = sorted({r["model"] for r in collect() if r["provider"] == args.provider})
    else:
        models = args.models or []
    if not models:
        raise SystemExit("give --models or --all")
    key = load_key(args.provider)
    P = prompts()
    t0 = time.time()
    rows = []
    with ThreadPoolExecutor(max_workers=args.parallel_models) as pool:
        for rs in pool.map(lambda m: one_model(key, args.provider, m, P, args.max_tokens, args.effort),
                           models):
            rows.extend(rs)

    # ---- summary per model --------------------------------------------------
    print(f"\n{'model':<32}{'seq modal':>10}{'conc modal':>11}{'seq pair':>10}{'conc pair':>10}"
          f"{'  seq==10/10':>12}{'  conc==10/10':>13}")
    summary = {}
    for m in models:
        rs = [r for r in rows if r["model"] == m and r["sequential"]["n"] and r["concurrent"]["n"]]
        if not rs:
            continue
        mean = lambda arm, k: sum(r[arm][k] for r in rs) / len(rs)
        full = lambda arm: sum(r[arm]["modal"] == 1.0 for r in rs)
        summary[m] = {"sequential_modal": mean("sequential", "modal"),
                      "concurrent_modal": mean("concurrent", "modal"),
                      "sequential_pairwise": mean("sequential", "pairwise"),
                      "concurrent_pairwise": mean("concurrent", "pairwise"),
                      "sequential_full_prompts": full("sequential"),
                      "concurrent_full_prompts": full("concurrent"), "prompts": len(rs)}
        s = summary[m]
        print(f"{m:<32}{s['sequential_modal']:>10.0%}{s['concurrent_modal']:>11.0%}"
              f"{s['sequential_pairwise']:>10.0%}{s['concurrent_pairwise']:>10.0%}"
              f"{s['sequential_full_prompts']:>9}/{len(rs)}{s['concurrent_full_prompts']:>10}/{len(rs)}")
    det_seq = sum(s["sequential_full_prompts"] == s["prompts"] for s in summary.values())
    det_conc = sum(s["concurrent_full_prompts"] == s["prompts"] for s in summary.values())
    print(f"\nmodels reproducing on every prompt: sequential {det_seq}/{len(summary)}, "
          f"concurrent {det_conc}/{len(summary)}   ({(time.time() - t0) / 60:.1f} min)")

    froms = [r[a]["collected"]["from"] for r in rows for a in ("sequential", "concurrent") if r[a]["collected"]["from"]]
    tos = [r[a]["collected"]["to"] for r in rows for a in ("sequential", "concurrent") if r[a]["collected"]["to"]]
    dest = resolve_out(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({
        "provider": args.provider, "n_per_arm": N, "max_tokens": args.max_tokens,
        "reasoning_effort": args.effort, "written": iso(time.time()),
        "collected": {"from": min(froms) if froms else "", "to": max(tos) if tos else "", "source": "cache"},
        "deterministic_models": {"sequential": det_seq, "concurrent": det_conc, "of": len(summary)},
        "summary": summary, "rows": rows}, indent=2) + "\n")
    print(f"wrote {show(dest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

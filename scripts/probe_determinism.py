#!/usr/bin/env python3
"""Is greedy decoding reproducible? Identical request, ten times, exact match.

Every paper that reports a single greedy number treats it as a property of the
model. This asks whether it is: send the same request at temperature 0 ten times
and count how many responses are byte-identical to the modal one.

Three conditions per (model, prompt), because "does temperature 0 reproduce" and
"does the seed parameter do anything" are different questions:

    no_seed   the request as most harnesses send it
    seed_0    seed=0 on every call
    seed_1    seed=1 on every call

The test for "seed honoured" is whether ten calls with the SAME seed agree — a
honoured seed makes a seeded run 100% reproducible on a prompt that was not
reproducible without one. It is NOT whether seed_0 and seed_1 differ: on a model
whose seeded runs are only 40% self-consistent they differ because everything
differs, and that is noise. The first version of this script reported that and
got the column backwards.

The exact-match rate is the share of the ten that equal the mode. Reported for
the content (what a grader sees) and separately for the reasoning trace, since a
provider can be deterministic in one and not the other.

Prompts: the four from the distinguishability set, plus one real MATH-500
problem from the sweep split — the greedy cell the study actually runs.

    python3 scripts/probe_determinism.py --all --out runs/determinism.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


import functools
print = functools.partial(print, flush=True)   # noqa: A001 — progress lands in logs under nohup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.benchmarks import sweep_split
from samplerconfound.config import FIXED
from samplerconfound.paths import resolve_out, show
from samplerconfound.adapters import ADAPTERS
from samplerconfound.provider import DEFAULT_PROVIDER, complete, default_cache, load_key
from scripts.probe_distinguishability import PROMPTS, resolve_models

CONDITIONS = {
    "no_seed": {},
    "seed_0": {"seed": 0},
    "seed_1": {"seed": 1},
}
N = 10


def prompts() -> dict[str, str]:
    p = dict(PROMPTS)
    # One real problem from the grid, so this speaks to the sweep's greedy cell
    # and not only to a probe prompt.
    prob = sweep_split("math500")[0]
    p["math500"] = f"{FIXED['prompt_template']}\n\n{prob.problem}"
    return p


def one(key, model, prompt, extra, max_tokens, effort, replicate, provider):
    body = {"model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": 0.0, **extra}
    if effort:
        body["reasoning_effort"] = effort
    c, err = complete(key, body, replicate, provider=provider)
    if err:
        return None, None, err
    # A condition whose parameter the adapter could not put on the wire was not
    # tested; reporting its output as "seed_0" would measure nothing.
    missing = set(extra) & set(c.dropped)
    if missing:
        return None, None, f"unsupported: {sorted(missing)} has no wire form on {provider}"
    return c.text, c.reasoning, None


def match_rate(xs: list[str]) -> tuple[float, int, str]:
    """(share equal to the mode, distinct count, the mode)."""
    if not xs:
        return float("nan"), 0, ""
    mode, k = Counter(xs).most_common(1)[0]
    return k / len(xs), len(set(xs)), mode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--effort", default="low")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--provider", default=DEFAULT_PROVIDER, choices=sorted(ADAPTERS),
                    help="which provider serves --models; the adapter handles the rest")
    args = ap.parse_args()

    models = resolve_models(args)
    key = load_key(args.provider)
    P = prompts()
    rows = []
    t0 = time.time()

    for model in models:
        print(f"\n=== {model} ===")
        print(f"  {'prompt':<10}{'no_seed':>10}{'seed_0':>10}{'seed_1':>10}"
              f"{'s0==s1':>8}  {'reasoning no_seed':>18}")
        for pid, text in P.items():
            res = {}
            for cond, extra in CONDITIONS.items():
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    outs = list(pool.map(
                        lambda i: one(key, model, text, extra, args.max_tokens,
                                      args.effort, i, args.provider),
                        range(N)))
                errs = [e for _, _, e in outs if e]
                content = [c for c, _, e in outs if not e]
                reasoning = [r for _, r, e in outs if not e]
                res[cond] = {
                    "n": len(content), "errors": errs[:2],
                    "content": match_rate(content),
                    "reasoning": match_rate(reasoning),
                    "modal_content": match_rate(content)[2][:200],
                }
            # Is the seed honoured? Only answerable where the model is not
            # already deterministic without one.
            s0, s1 = res["seed_0"]["modal_content"], res["seed_1"]["modal_content"]
            same = (s0 == s1) if (res["seed_0"]["n"] and res["seed_1"]["n"]) else None
            rej = any(e.startswith("rejected") for c in ("seed_0", "seed_1")
                      for e in res[c]["errors"])
            row = {"provider": args.provider, "model": model, "prompt": pid,
                   "conditions": res,
                   "seed_rejected": rej, "seed0_eq_seed1": same}
            rows.append(row)

            def fmt(c):
                r = res[c]
                if r["errors"] and not r["n"]:
                    return f"{'REJ' if rej else 'ERR':>10}"
                return f"{r['content'][0]:>9.0%} "
            print(f"  {pid:<10}{fmt('no_seed')}{fmt('seed_0')}{fmt('seed_1')}"
                  f"{'same' if same else ('diff' if same is False else '—'):>8}  "
                  f"{res['no_seed']['reasoning'][0]:>17.0%}")

    # ---- summary ---------------------------------------------------------
    print(f"\n{'model':<32}{'greedy exact-match':>19}{'w/ seed=0':>11}  seed honoured?")
    by = {}
    for r in rows:
        by.setdefault(r["model"], []).append(r)
    summary = {}
    for model, rs in by.items():
        ns = [r["conditions"]["no_seed"]["content"][0] for r in rs if r["conditions"]["no_seed"]["n"]]
        s0 = [r["conditions"]["seed_0"]["content"][0] for r in rs if r["conditions"]["seed_0"]["n"]]
        rej = any(r["seed_rejected"] for r in rs)
        # The test for "seed honoured" is NOT whether seed_0 and seed_1 differ.
        # The first version of this script reported that and got it backwards:
        # on a model whose seeded runs are only 40% self-consistent, seed_0 and
        # seed_1 differ because everything differs, and that is noise, not a
        # seed doing its job. The test is whether ten calls with the SAME seed
        # agree. A seed that is honoured makes a seeded run 100% reproducible on
        # a prompt that was not reproducible without one.
        informative = [r for r in rs
                       if r["conditions"]["no_seed"]["n"]
                       and r["conditions"]["no_seed"]["content"][0] < 1.0]
        if rej:
            honoured = "rejected"
        elif not informative:
            honoured = "n/a (deterministic without one)"
        else:
            repro = sum(max(r["conditions"]["seed_0"]["content"][0],
                            r["conditions"]["seed_1"]["content"][0]) >= 1.0
                        for r in informative)
            honoured = (f"{'yes' if repro == len(informative) else 'NO'} "
                        f"(fixed seed reproduces on {repro}/{len(informative)} "
                        "non-deterministic prompts)")
        mean_ns = sum(ns) / len(ns) if ns else float("nan")
        mean_s0 = sum(s0) / len(s0) if s0 else float("nan")
        summary[model] = {"greedy_match": mean_ns, "seeded_match": mean_s0, "seed": honoured}
        print(f"{model:<32}{mean_ns:>18.0%}{mean_s0:>11.0%}  {honoured}")
    print(f"\n{len(rows) * len(CONDITIONS) * N} calls, {(time.time() - t0) / 60:.1f} min"
          f"   [{default_cache().stats}]")

    if args.out:
        dest = resolve_out(args.out)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps({"provider": args.provider,
                                    "n_per_condition": N, "conditions": CONDITIONS,
                                    "rows": rows, "summary": summary},
                                   indent=2, default=str) + "\n")
        print(f"wrote {show(dest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

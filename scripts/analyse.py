#!/usr/bin/env python3
"""Turn a finished sweep into the numbers the paper reports.

Runs every analysis the design calls for, in the order the paper states them:

  1. accuracy-level two-way decomposition  — the headline, sampler:model ratio
  2. comparison-inversion rate             — how often the ranking flips
  3. item-level three-way decomposition    — supplement, where variance lives
  4. solve-rate three-way decomposition    — continuous, binomial-corrected

Refuses to run on an unbalanced grid. The decomposition assumes equal cell
counts; on an unbalanced one it still returns numbers, and they are wrong in a
way nothing in the output reveals.

Everything is reported twice, strict and parsed. `accuracy_strict` counts an
unparseable response as wrong, which is what standard harnesses do.
`accuracy_parsed` conditions on a successful parse. If the headline moves between
them, the finding depends on how unparseable responses are treated and the paper
has to say so rather than pick the flattering one.

    python3 scripts/analyse.py runs/smoke/math500.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.inversion import inversion_rate
from samplerconfound.paths import resolve_out, show
from samplerconfound.variance import (
    decompose_accuracy,
    decompose_items,
    decompose_solve_rate,
    solve_rates,
)


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def check_balance(records: list[dict]) -> tuple[list[str], list[str], list[str], int]:
    models = sorted({r["model"] for r in records})
    samplers = sorted({r["sampler"] for r in records})
    problems = sorted({r["problem_id"] for r in records})
    reps = sorted({r["replicate"] for r in records})
    counts = defaultdict(int)
    for r in records:
        counts[(r["model"], r["sampler"], r["replicate"], r["problem_id"])] += 1

    expected = len(models) * len(samplers) * len(reps) * len(problems)
    if len(counts) != expected or set(counts.values()) != {1}:
        dupes = sum(v - 1 for v in counts.values() if v > 1)
        raise SystemExit(
            f"unbalanced grid: {len(counts)} distinct cells, expected {expected} "
            f"({len(models)} models x {len(samplers)} samplers x {len(reps)} "
            f"replicates x {len(problems)} problems), {dupes} duplicate cells.\n"
            "The decomposition assumes equal cell counts and would return numbers "
            "that look fine and are wrong. Run run_sweep.py --verify."
        )
    return models, samplers, problems, len(reps)


def cell_accuracy(records, strict: bool):
    """One accuracy per (model, sampler, replicate) — the number a paper reports."""
    acc, mids, sids = [], [], []
    by = defaultdict(list)
    for r in records:
        by[(r["model"], r["sampler"], r["replicate"])].append(r["verdict"]["status"])
    for (m, s, _rep), statuses in sorted(by.items()):
        if strict:
            a = sum(x == "correct" for x in statuses) / len(statuses)
        else:
            parsed = [x for x in statuses if x != "unparseable"]
            if not parsed:
                a = float("nan")
            else:
                a = sum(x == "correct" for x in parsed) / len(parsed)
        acc.append(a)
        mids.append(m)
        sids.append(s)
    return acc, mids, sids


def short(m: str) -> str:
    return m.split("/")[-1]


# What each source is, in the paper's language. "seed" is the roadmap's word for
# the replicate dimension; the design deliberately does NOT call it seed, because
# this provider ignores the seed parameter on text. What the replicates actually
# measure is resampling variance at a FIXED configuration, which is the quantity
# a seed would have controlled had it worked.
SOURCE_LABEL = {
    "model": "model",
    "sampler": "sampler (decoding config)",
    "problem": "problem",
    "model:sampler": "model x sampler",
    "model:problem": "model x problem",
    "sampler:problem": "sampler x problem",
    "model:sampler:problem": "model x sampler x problem",
    "resampling": "seed / replicate (resampling at fixed config)",
    "residual": "residual (after binomial correction)",
}


def level_scatter(k: int) -> float:
    """Relative scatter of a realised level variance estimated from k levels.

    A variance component built from k factor levels has a chi-square(k-1)
    sampling distribution, so its relative standard deviation is about
    sqrt(2/(k-1)) — 82% at four levels, 58% at seven. This is not noise the
    bootstrap covers: that interval resamples replicates within cell, not levels.
    Printing it next to the share is what keeps a point estimate from being read
    as precise.
    """
    return float(np.sqrt(2.0 / (k - 1))) if k > 1 else float("inf")


def report_attribution(three: dict, n_levels: dict) -> None:
    """One table answering: what share of total variance goes where."""
    print("\n=== variance attribution (item level, all sources) ===")
    print(f"{'source':<46}{'share':>8}{'levels':>8}{'+/- rel':>9}")
    factors = {"model": "model", "sampler": "sampler", "problem": "problem"}
    for row in three["table"]:
        src = row["source"]
        parts = src.split(":")
        # A component's precision is limited by the SMALLEST factor entering it.
        ks = [n_levels[factors[p]] for p in parts if p in factors]
        scat = f"{max(level_scatter(k) for k in ks):.0%}" if ks else "-"
        lvl = "x".join(str(n_levels[factors[p]]) for p in parts if p in factors) or "-"
        print(f"{SOURCE_LABEL.get(src, src):<46}{row['var_share']:>7.1%}{lvl:>8}{scat:>9}")
    total = sum(r["var_share"] for r in three["table"])
    print(f"{'total':<46}{total:>7.1%}")
    print("\n  'levels' is how many levels of each factor the component is built "
          "from, and\n  '+/- rel' the resulting relative scatter, sqrt(2/(k-1)). "
          "A share estimated\n  from four model levels carries ~82% relative "
          "uncertainty on its own, which\n  the within-cell bootstrap does not "
          "cover. Report shares with this attached.")


def report_two_way(name: str, acc, mids, sids) -> dict:
    d = decompose_accuracy(acc, mids, sids).to_dict()
    print(f"\n--- accuracy-level decomposition ({name}) ---")
    print(f"{'source':<16}{'var comp':>12}{'share':>9}{'sd (acc pts)':>14}")
    for row in d["table"]:
        print(f"{row['source']:<16}{row['var_component']:>12.6f}"
              f"{row['var_share']:>8.1%}{row['sd'] * 100:>13.2f}")
    r = d["sampler_to_model"]
    lo, hi = d["sampler_to_model_ci"]
    print(f"\nsampler:model ratio = {r:.3f}   95% CI [{lo:.3f}, {hi:.3f}]")
    print("  >1 means decoding configuration moves the reported number more than "
          "swapping the model does")
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--n-boot", type=int, default=2000)
    args = ap.parse_args()

    records = load(args.results)
    models, samplers, problems, n_reps = check_balance(records)

    print(f"{args.results.name}: {len(records):,} generations")
    print(f"  {len(models)} models x {len(samplers)} samplers x {n_reps} replicates "
          f"x {len(problems)} problems  — balanced")

    # ---- cell summary -----------------------------------------------------
    print(f"\n--- accuracy by cell (strict / parsed / unparseable) ---")
    print(f"{'model':<32}" + "".join(f"{s:>22}" for s in samplers))
    by = defaultdict(list)
    for r in records:
        by[(r["model"], r["sampler"])].append(r["verdict"]["status"])
    for m in models:
        row = f"{short(m):<32}"
        for s in samplers:
            st = by[(m, s)]
            c = sum(x == "correct" for x in st)
            u = sum(x == "unparseable" for x in st)
            p = len(st) - u
            row += f"{c/len(st):>7.1%}{(c/p if p else 0):>7.1%}{u/len(st):>8.1%}"
        print(row)

    out: dict = {"n_records": len(records), "models": models, "samplers": samplers,
                 "n_problems": len(problems), "n_replicates": n_reps}

    # ---- 1. headline ------------------------------------------------------
    for name, strict in (("strict", True), ("parsed", False)):
        acc, mids, sids = cell_accuracy(records, strict=strict)
        out[f"two_way_{name}"] = report_two_way(name, acc, mids, sids)

    # ---- 2. inversion -----------------------------------------------------
    acc, mids, sids = cell_accuracy(records, strict=True)
    inv = inversion_rate(acc, mids, sids).to_dict()
    print(f"\n--- comparison inversions (strict) ---")
    print(f"  {inv['n_comparisons']} model-pair comparisons across sampler configs")
    print(f"  raw inversions      {inv['n_raw']:>4}  = {inv['raw_rate']:.1%}")
    print(f"  decisive inversions {inv['n_decisive']:>4}  = {inv['decisive_rate']:.1%}"
          "   (both directions outside sampling noise)")
    if inv["pairs_ever_inverted"]:
        print(f"  pairs that ever flip: {inv['pairs_ever_inverted']}")
    for e in inv["sampler_range"][:6]:
        print(f"    {e}")
    out["inversions"] = inv

    # ---- 3. item-level ----------------------------------------------------
    correct, mids2, sids2, pids2 = [], [], [], []
    for r in records:
        correct.append(1.0 if r["verdict"]["status"] == "correct" else 0.0)
        mids2.append(r["model"]); sids2.append(r["sampler"]); pids2.append(r["problem_id"])
    three = decompose_items(correct, mids2, sids2, pids2).to_dict()
    print(f"\n--- item-level three-way decomposition (supplement) ---")
    print(f"{'source':<20}{'var comp':>12}{'share':>9}")
    for row in three["table"]:
        print(f"{row['source']:<20}{row['var_component']:>12.6f}{row['var_share']:>8.1%}")
    out["three_way_items"] = three
    report_attribution(three, {"model": len(models), "sampler": len(samplers),
                               "problem": len(problems)})
    out["level_counts"] = {"model": len(models), "sampler": len(samplers),
                           "problem": len(problems), "replicates": n_reps}

    # ---- 4. solve rate ----------------------------------------------------
    rates, rm, rs, rp, nrep = solve_rates(correct, mids2, sids2, pids2)
    sr = decompose_solve_rate(rates, rm, rs, rp, n_replicates=nrep).to_dict()
    print(f"\n--- per-problem solve rate, binomial-corrected ---")
    print(f"{'source':<20}{'var comp':>12}{'share':>9}")
    for row in sr["table"]:
        print(f"{row['source']:<20}{row['var_component']:>12.6f}{row['var_share']:>8.1%}")
    out["solve_rate"] = sr

    if args.out:
        dest = resolve_out(args.out)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(out, indent=2, default=str) + "\n")
        print(f"\nwrote {show(dest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run the model-selection pilot: every eligible candidate, one sampler, one pass.

Feeds `scripts/select_models.py`. Uses `PILOT_SAMPLER` on `pilot_split()`, which
is drawn from held-out MATH-500 levels 4-5 — disjoint from both sweeps, and hard
enough to separate models that full MATH-500 would leave at ceiling.

Candidates that cannot run the full sampler grid are skipped here rather than
being piloted and then discarded: measuring a model that will never be a level is
just spending money to produce a number nobody may look at.

    python3 scripts/run_pilot.py --scale 4          # smoke scale
    python3 scripts/run_pilot.py                    # the real pilot
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.benchmarks import pilot_split
from itertools import combinations

from samplerconfound.config import (
    FIXED,
    MODEL_CANDIDATES,
    N_MODEL_LEVELS,
    PILOT_SAMPLER,
    SAMPLER_CONFIGS,
    affordable,
    grid_cost_usd,
    supports_grid,
)
from samplerconfound.grade import grade
from scripts.run_sweep import load_key, generate  # same request path as the sweep


class _PilotDesign:
    """Minimal stand-in so the pilot reuses the sweep's exact request code."""

    def __init__(self):
        self.fixed = dict(FIXED)
        self.benchmark = "math500"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=int, default=1, help="run 1/N of pilot problems")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", type=Path, default=ROOT / "runs" / "pilot" / "accuracy.json")
    ap.add_argument("--raw", type=Path, default=ROOT / "runs" / "pilot" / "raw.jsonl")
    args = ap.parse_args()

    sampler = next(s for s in SAMPLER_CONFIGS if s["id"] == PILOT_SAMPLER)
    runnable = [c for c in MODEL_CANDIDATES if supports_grid(c)]
    ids = [c["id"] for c in runnable]
    # Also drop anything no affordable set can contain. Piloting a model that
    # can never be a level is spending money to produce a number nobody will
    # look at, and the pilot is the one place where that is easy to miss —
    # select_models filters it out silently afterwards.
    in_some_set = {
        m for combo in combinations(ids, N_MODEL_LEVELS) if affordable(combo)
        for m in combo
    }
    candidates = [c for c in runnable if c["id"] in in_some_set]
    skipped = [c["id"].split("/")[-1] for c in MODEL_CANDIDATES if not supports_grid(c)]
    priced_out = [f"{c['id'].split('/')[-1]} (${grid_cost_usd(c):.2f})"
                  for c in runnable if c["id"] not in in_some_set]
    problems = pilot_split()[:: args.scale]

    print(f"pilot: {len(candidates)} candidates x {len(problems)} problems "
          f"on sampler '{PILOT_SAMPLER}'")
    if skipped:
        print(f"  skipped (cannot run the full sampler grid): {skipped}")
    if priced_out:
        print(f"  skipped (in no affordable set): {priced_out}")
    if len(candidates) == N_MODEL_LEVELS:
        print(f"  NOTE: exactly {N_MODEL_LEVELS} candidates remain, so the budget "
              "rather than the near-peer rule determines the grid. The pilot's job "
              "here is to confirm all four sit inside the band, not to choose.")

    key = load_key()
    design = _PilotDesign()
    jobs = [(c["id"], p) for p in problems for c in candidates]  # interleave by model

    args.raw.parent.mkdir(parents=True, exist_ok=True)
    results = defaultdict(list)
    t0 = time.time()
    with args.raw.open("w") as fh, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(generate, key, design, m, sampler, 0, p): (m, p)
                for m, p in jobs}
        for i, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            if rec:
                fh.write(json.dumps(rec) + "\n")
                results[rec["model"]].append(rec["verdict"]["status"])
            if i % 25 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)}  {(time.time()-t0)/60:.1f}m")

    accuracy = {}
    print(f"\n{'model':<34} {'n':>4} {'acc_strict':>11} {'unparseable':>12}")
    for c in candidates:
        st = results.get(c["id"], [])
        if not st:
            print(f"{c['id'].split('/')[-1]:<34} {'0':>4}   no results — excluded")
            continue
        # Scored strict: unparseable counts as wrong. Selection needs the number a
        # paper would publish, and that is the strict one.
        acc = sum(s == "correct" for s in st) / len(st)
        unp = sum(s == "unparseable" for s in st) / len(st)
        accuracy[c["id"]] = acc
        print(f"{c['id'].split('/')[-1]:<34} {len(st):>4} {acc:>10.1%} {unp:>11.1%}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(accuracy, indent=2) + "\n")
    print(f"\nwrote {args.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

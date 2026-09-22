#!/usr/bin/env python3
"""Unparseable is not uniform across models and it rises with temperature.

A response the grader cannot read is a third outcome, and a harness that folds
it into "incorrect" makes a decision with consequences that fall unevenly. On
the smoke corpus one model produces unparseable responses at 10–18% (MATH-500)
and 27–33% (AIME) while the others sit at 0%, the rate climbs monotonically
with temperature, and nearly every one of them is a response that hit the
token budget mid-reasoning. Scoring those as wrong gives that model alone a
temperature effect, moves it from first to last among parseable responses,
and flips 12 of 30 pairwise model comparisons.

    python3 scripts/unparseable_report.py
"""

from __future__ import annotations

import itertools
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.paths import iso, show
from samplerconfound.variance import decompose_accuracy

SAMPLERS = ["greedy", "lowtemp", "standard", "topk", "hightemp"]     # in temperature order
TEMPERATURE = {"greedy": 0.0, "lowtemp": 0.3, "standard": 0.7, "topk": 0.7, "hightemp": 1.0}
OUT = ROOT / "runs" / "smoke" / "unparseable.json"


def load(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def accuracy(statuses, rule: str) -> float:
    if rule == "strict":                       # unparseable counts as incorrect
        return sum(s == "correct" for s in statuses) / len(statuses)
    ok = [s for s in statuses if s != "unparseable"]
    return sum(s == "correct" for s in ok) / len(ok) if ok else float("nan")


def report(rows: list[dict], label: str) -> dict:
    by = defaultdict(list)
    tokens = defaultdict(list)
    finish = defaultdict(int)
    for r in rows:
        m = r["model"].split("/")[-1]
        by[(m, r["sampler"], r["replicate"])].append(r["verdict"]["status"])
        tokens[(m, r["sampler"], r["verdict"]["status"] == "unparseable")].append(r["output_tokens"])
        if r["verdict"]["status"] == "unparseable":
            finish[r["finish_reason"]] += 1
    models = sorted({k[0] for k in by})
    reps = sorted({k[2] for k in by})
    out = {"label": label, "models": models, "samplers": SAMPLERS, "unparseable_finish_reason": dict(finish),
           "cells": {}, "rank_flips": {}, "var_share": {}}

    print(f"\n== {label}: {len(rows)} records; unparseable by finish_reason {dict(finish)}")
    print(f"{'model':<26}{'':<12}" + "".join(f"{s:>10}" for s in SAMPLERS) + f"{'greedy-hightemp':>17}")
    for m in models:
        cell = {}
        for rule in ("strict", "parseable"):
            vals = [float(np.mean([accuracy(by[(m, s, r)], rule) for r in reps])) for s in SAMPLERS]
            cell[rule] = dict(zip(SAMPLERS, vals))
            print(f"{m:<26}{rule:<12}" + "".join(f"{v:>10.2f}" for v in vals) + f"{vals[0] - vals[-1]:>+17.2f}")
        un = [float(np.mean([sum(x == "unparseable" for x in by[(m, s, r)]) / len(by[(m, s, r)]) for r in reps]))
              for s in SAMPLERS]
        cell["unparseable"] = dict(zip(SAMPLERS, un))
        cell["median_tokens_parseable"] = {s: float(np.median(tokens[(m, s, False)])) if tokens[(m, s, False)] else None for s in SAMPLERS}
        cell["median_tokens_unparseable"] = {s: float(np.median(tokens[(m, s, True)])) if tokens[(m, s, True)] else None for s in SAMPLERS}
        print(f"{'':<26}{'unparseable':<12}" + "".join(f"{u:>10.0%}" for u in un))
        out["cells"][m] = cell

    # rankings under each rule, per sampler
    flips_total = 0
    for s in SAMPLERS:
        a = {m: np.mean([accuracy(by[(m, s, r)], "strict") for r in reps]) for m in models}
        b = {m: np.mean([accuracy(by[(m, s, r)], "parseable") for r in reps]) for m in models}
        fl = [(x, y) for x, y in itertools.combinations(models, 2) if (a[x] - a[y]) * (b[x] - b[y]) < 0]
        flips_total += len(fl)
        out["rank_flips"][s] = {"strict": sorted(models, key=lambda m: -a[m]),
                                "parseable": sorted(models, key=lambda m: -b[m]), "flipped_pairs": fl}
    n_pairs = len(SAMPLERS) * len(list(itertools.combinations(models, 2)))
    out["rank_flips_total"] = [flips_total, n_pairs]
    print(f"pairwise model comparisons that invert with the scoring rule: {flips_total}/{n_pairs}")

    if len(models) >= 2 and len(reps) >= 2:
        keys = sorted(by)
        for rule in ("strict", "parseable"):
            tw = decompose_accuracy([accuracy(by[k], rule) for k in keys], [k[0] for k in keys],
                                    [k[1] for k in keys], n_boot=300)
            out["var_share"][rule] = tw.var_share
            print(f"{rule:<10} variance share: " + ", ".join(f"{k} {v:.1%}" for k, v in tw.var_share.items()))
    return out


def main() -> int:
    res = {"written": iso(time.time()),
           "math500": report(load(ROOT / "runs" / "smoke" / "math500.jsonl"), "smoke MATH-500"),
           "aime": report(load(ROOT / "runs" / "smoke" / "aime.jsonl"), "smoke AIME")}
    pilot = load(ROOT / "runs" / "pilot" / "raw.jsonl")
    by = defaultdict(list)
    for r in pilot:
        by[r["model"].split("/")[-1]].append(r["verdict"]["status"])
    res["pilot_standard"] = {m: sum(s == "unparseable" for s in st) / len(st) for m, st in by.items()}
    print("\n== pilot, standard sampler, 100 problems: unparseable " +
          ", ".join(f"{m} {v:.0%}" for m, v in sorted(res["pilot_standard"].items())))
    OUT.write_text(json.dumps(res, indent=2, default=str) + "\n")
    print(f"wrote {show(OUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

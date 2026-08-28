#!/usr/bin/env python3
"""Can this grid support the headline claim? Run before spending on the sweep.

Core metric 1 is the sampler:model variance ratio, and the claim is that sampler
variance is within an ORDER OF MAGNITUDE of model variance — ratio >= 0.1.

The question this answers is not "what will the ratio be" but "if the truth were
X, how often would this design's interval let us say so". It matters because the
uncertainty dominating the ratio comes from the number of factor LEVELS, and
level uncertainty does not shrink with more problems or more replicates. Buying
either changes nothing here; only more models or more samplers would.

    python3 scripts/power_check.py --models 3 --samplers 7
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.variance import ORDER_OF_MAGNITUDE, decompose_accuracy

# Calibrated to the real sweep, not the smoke run. Pilot accuracies of
# 0.74/0.80/0.84 give a realised model sd near 0.05; 200 problems give a cell
# accuracy SE near 0.028.
S2_MODEL, S2_INTERACTION, S2_RESAMPLING = 0.0025, 0.0004, 0.0008


def detection_rate(a, b, n, true_ratio, draws, n_boot, seed0):
    point = interval = 0
    for sd in range(draws):
        rng = np.random.default_rng(seed0 + sd)
        A = rng.normal(0, np.sqrt(S2_MODEL), a)
        B = rng.normal(0, np.sqrt(S2_MODEL * true_ratio), b)
        AB = rng.normal(0, np.sqrt(S2_INTERACTION), (a, b))
        acc, ml, sl = [], [], []
        for i in range(a):
            for j in range(b):
                for _ in range(n):
                    acc.append(0.8 + A[i] + B[j] + AB[i, j]
                               + rng.normal(0, np.sqrt(S2_RESAMPLING)))
                    ml.append(f"m{i}")
                    sl.append(f"s{j}")
        d = decompose_accuracy(acc, ml, sl, n_boot=n_boot)
        point += d.sampler_to_model >= ORDER_OF_MAGNITUDE
        interval += d.sampler_to_model_ci_levels[0] >= ORDER_OF_MAGNITUDE
    return point / draws, interval / draws


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", type=int, nargs="+", default=[3, 4])
    ap.add_argument("--samplers", type=int, default=7)
    ap.add_argument("--replicates", type=int, default=5)
    ap.add_argument("--draws", type=int, default=40)
    ap.add_argument("--n-boot", type=int, default=300)
    ap.add_argument("--ratios", type=float, nargs="+",
                    default=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0])
    args = ap.parse_args()

    print(f"threshold: ratio >= {ORDER_OF_MAGNITUDE}   "
          f"{args.samplers} samplers x {args.replicates} replicates, "
          f"{args.draws} draws each")
    print("  'point'    = point estimate clears the threshold")
    print("  'interval' = level-aware CI lower bound clears it, which is what a "
          "claim needs\n")
    head = f"{'true ratio':>11} |" + "".join(
        f"{f'{a}mod point':>12}{f'{a}mod CI':>10} |" for a in args.models)
    print(head)
    for tr in args.ratios:
        row = f"{tr:>11.2f} |"
        for i, a in enumerate(args.models):
            p, c = detection_rate(a, args.samplers, args.replicates, tr,
                                  args.draws, args.n_boot, seed0=1000 * i)
            row += f"{p:>11.0%}{c:>10.0%} |"
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

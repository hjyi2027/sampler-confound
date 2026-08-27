#!/usr/bin/env python3
"""Fill the model slot from measured pilot accuracy, then emit the runnable configs.

Reads a pilot result file: {"<model id>": <accuracy in [0,1]>, ...}, produced by
running every candidate in `MODEL_CANDIDATES` once through the `standard` sampler
on PILOT_N_PROBLEMS MATH-500 problems drawn at PILOT_PROBLEM_SEED. Those pilot
problems are excluded from the main sweep's draw, so the models are not selected
on the items they are then scored on.

Selection uses published leaderboard numbers for NOTHING. Those numbers were each
measured under their own undisclosed decoding config, which is the exact defect
this paper documents; selecting the grid on them would refute the paper with the
grid.

    python3 scripts/select_models.py runs/pilot/accuracy.json

Writes configs/main.json and configs/aime.json. Refuses to overwrite unless
--force, because rewriting a frozen grid mid-sweep silently invalidates the cells
already generated.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from itertools import combinations

from samplerconfound.config import (
    BENCHMARKS,
    N_MODEL_LEVELS,
    MODEL_CANDIDATES,
    PILOT_BAND,
    Design,
    affordable,
    grid_cost_usd,
    select_models,
    supports_grid,
)

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ROOT / "configs"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pilot", type=Path, help="JSON mapping model id -> pilot accuracy")
    ap.add_argument("--force", action="store_true", help="overwrite a frozen config")
    ap.add_argument("--tag", default="",
                    help="write configs/<name><tag>.json instead of <name>.json. "
                         "Used by the smoke run so a low-power pilot can never "
                         "overwrite the real frozen grid")
    args = ap.parse_args()

    pilot: dict[str, float] = json.loads(args.pilot.read_text())

    known = {c["id"] for c in MODEL_CANDIDATES}
    unknown = sorted(set(pilot) - known)
    missing = sorted(known - set(pilot))
    if unknown:
        print(f"error: pilot has models not on the shortlist: {unknown}", file=sys.stderr)
        return 1
    if missing:
        # Loud, not fatal: a candidate can legitimately be absent from the
        # provider's catalog. But it must be a noticed absence, because a
        # candidate that silently 404s and drops out has changed the design.
        print(f"warning: no pilot result for {missing} — excluded from selection")

    chosen = select_models(pilot, k=N_MODEL_LEVELS, band=PILOT_BAND)
    accs = [pilot[m] for m in chosen]
    families = {c["id"]: c["family"] for c in MODEL_CANDIDATES}

    print(f"\nselected {N_MODEL_LEVELS} levels, spread {max(accs) - min(accs):.3f}:")
    for m in chosen:
        print(f"  {pilot[m]:.3f}  {families[m]:9s} {m}")
    print("\nnot selected:")
    by_id = {c["id"]: c for c in MODEL_CANDIDATES}
    for m, a in sorted(pilot.items(), key=lambda kv: -kv[1]):
        if m in chosen:
            continue
        # Give the actual reason. Reporting "widens spread" for a model excluded
        # on cost is misleading in the one place a reader looks to check that the
        # grid was not hand-picked — and cost exclusions are invisible otherwise,
        # because the budget applies to whole subsets rather than to candidates.
        if not (PILOT_BAND[0] <= a <= PILOT_BAND[1]):
            why = "outside band"
        elif m in by_id and not supports_grid(by_id[m]):
            why = "cannot run the full sampler grid"
        elif not any(
            affordable(c) for c in combinations(sorted(pilot), N_MODEL_LEVELS) if m in c
        ):
            why = f"no affordable set contains it (its grid share is "\
                  f"${grid_cost_usd(by_id[m]):.2f})"
        else:
            why = "widens spread"
        print(f"  {a:.3f}  {m}  ({why})")

    for benchmark in BENCHMARKS:
        name = "main" if benchmark == "math500" else benchmark
        tmpl = json.loads((CONFIGS / f"{name}.template.json").read_text())
        tmpl["models"] = chosen
        out = CONFIGS / f"{name}{args.tag}.json"
        if out.exists() and not args.force:
            print(f"\nrefusing to overwrite {out.relative_to(ROOT)} (use --force)",
                  file=sys.stderr)
            return 1
        design = Design(**tmpl)
        design.save(out)   # save() validates; an unbalanced or empty grid raises here
        print(f"wrote {out.relative_to(ROOT)}: {design.n_generations:,} generations")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

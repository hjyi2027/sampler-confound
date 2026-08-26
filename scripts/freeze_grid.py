#!/usr/bin/env python3
"""Write the frozen grid templates.

Everything the study fixes except the model levels, which are left empty on
purpose: `Design.validate()` rejects an empty model list, so a template cannot be
run by accident. `scripts/select_models.py` fills the slot from measured pilot
accuracy and emits the runnable `configs/main.json` / `configs/aime.json`.

Run once. Re-running after the grid is frozen and the sweep has started is a
protocol change, not a convenience.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from samplerconfound.config import BENCHMARKS, FIXED, SAMPLER_CONFIGS

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ROOT / "configs"

N_REPLICATES = 5
PROVIDER = "fireworks"


def template(benchmark: str) -> dict:
    spec = BENCHMARKS[benchmark]
    return {
        "models": [],  # filled by scripts/select_models.py; empty fails validate()
        "samplers": [dict(s) for s in SAMPLER_CONFIGS],
        "n_replicates": N_REPLICATES,
        "benchmark": benchmark,
        "n_problems": spec["n_problems"],
        "problem_seed": spec["problem_seed"],
        "provider": PROVIDER,
        "fixed": dict(FIXED),
    }


def main() -> None:
    CONFIGS.mkdir(exist_ok=True)
    for benchmark in BENCHMARKS:
        name = "main" if benchmark == "math500" else benchmark
        path = CONFIGS / f"{name}.template.json"
        path.write_text(json.dumps(template(benchmark), indent=2) + "\n")
        spec = BENCHMARKS[benchmark]
        n = 4 * len(SAMPLER_CONFIGS) * N_REPLICATES * spec["n_problems"]
        print(f"{path.relative_to(ROOT)}: {benchmark}, {spec['n_problems']} problems, "
              f"{n:,} generations at 4 models")


if __name__ == "__main__":
    main()

"""The grid. What is swept, what is held fixed, and what must be verified first.

Balance is not a style preference here: `variance.py` assumes every
(model, sampler) cell holds the same number of replicates, which is what makes
the sums of squares orthogonal and the components exactly estimable. A config
that produces an unbalanced grid is a config that produces wrong numbers, so the
loader checks it rather than trusting it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# sampler configurations
# --------------------------------------------------------------------------
# Six configs chosen to span the space practitioners actually use, not to be
# exhaustive. `standard` is the de facto default that goes unreported in most
# papers; `greedy` is what evaluation harnesses usually claim to use. The gap
# between those two alone is the paper's motivating case.
#
# WARNING, and it must be checked before the sweep: not every provider honours
# `top_k` and `min_p` on an OpenAI-compatible endpoint, and none of them document
# dropping it. If they are silently ignored, `topk` and `minp` collapse into
# duplicates of `standard`/`hightemp` — the grid stays balanced, the decomposition
# still runs, and the sampler component is quietly halved. That failure is
# invisible in the output. `scripts/probe_sampler_support.py` exists to catch it.

SAMPLER_CONFIGS = [
    {"id": "greedy", "temperature": 0.0},
    {"id": "lowtemp", "temperature": 0.3, "top_p": 1.0},
    {"id": "standard", "temperature": 0.7, "top_p": 0.95},
    {"id": "hightemp", "temperature": 1.0, "top_p": 1.0},
    {"id": "topk", "temperature": 0.7, "top_k": 40},
    {"id": "minp", "temperature": 1.0, "min_p": 0.05},
]

# --------------------------------------------------------------------------
# model factor
# --------------------------------------------------------------------------
# Still deliberately empty. A plausible-looking default here would get run by
# accident and silently define the study, and the model factor is the one place
# where a bad grid choice kills the headline rather than degrading it: if the
# levels span a wide capability range, model variance is enormous and the
# sampler/model ratio collapses toward zero. The paper would then be reporting a
# design decision as a fact.
DEFAULT_MODELS: list[str] = []

# The shortlist the four levels are drawn FROM. These are candidates, not the
# design. Every one is a non-reasoning instruct model: reasoning models expose an
# effort / thinking-budget knob that moves accuracy on its own, which would sit
# inside the "model" factor and confound it with the decoding factor this paper
# is about.
#
# `math500_prior` is a remembered published figure, accurate to maybe five points.
# It is here to make the shortlist reviewable, NOT to select on — selection runs
# against measured pilot accuracy (`scripts/select_models.py`). Reported numbers
# are measured under each paper's own undisclosed decoding config, which is the
# very thing this paper says makes them incomparable; selecting on them would be
# self-refuting.
MODEL_CANDIDATES = [
    {"id": "accounts/fireworks/models/llama-v3p3-70b-instruct",     "family": "meta",     "math500_prior": 0.77},
    {"id": "accounts/fireworks/models/llama4-scout-instruct-basic", "family": "meta",     "math500_prior": 0.83},
    {"id": "accounts/fireworks/models/qwen2p5-72b-instruct",        "family": "alibaba",  "math500_prior": 0.83},
    {"id": "accounts/fireworks/models/qwen2p5-14b-instruct",        "family": "alibaba",  "math500_prior": 0.80},
    {"id": "accounts/fireworks/models/mistral-small-24b-instruct-2501", "family": "mistral", "math500_prior": 0.70},
    {"id": "accounts/fireworks/models/gemma-3-27b-it",              "family": "google",   "math500_prior": 0.87},
    {"id": "accounts/fireworks/models/deepseek-v3",                 "family": "deepseek", "math500_prior": 0.90},
]

# Pre-registered selection rule, fixed BEFORE the pilot is run so the model set
# cannot be tuned until the headline looks good.
N_MODEL_LEVELS = 4
PILOT_BAND = (0.55, 0.90)   # measured pilot accuracy must fall inside this
PILOT_N_PROBLEMS = 100
PILOT_SAMPLER = "standard"

# The pilot draws its problems with a DIFFERENT seed from the main sweep and the
# two draws are made disjoint, so the models are not chosen on the same items
# they are then scored on.
PILOT_PROBLEM_SEED = 1729

# Benchmarks. Each is decomposed separately; benchmark is never a factor inside a
# single ANOVA, because problem difficulty is not commensurable across the two and
# the cell counts differ. AIME is all 60 problems of 2024+2025, so its "selection"
# is the whole set.
BENCHMARKS = {
    "math500": {"n_problems": 200, "problem_seed": 0},
    "aime":    {"n_problems": 60,  "problem_seed": 0},
}

# Held fixed across every cell. Each of these is a variable this paper is
# accusing the field of leaving free, so leaving one free here would be fatal.
FIXED = {
    "prompt_template": (
        "Solve the problem. Reason step by step, then give the final answer on "
        "its own last line in the form: Answer: <answer>"
    ),
    "max_tokens": 2048,
    "stop": None,
    "system_prompt": None,
}


@dataclass
class Design:
    models: list[str] = field(default_factory=lambda: list(DEFAULT_MODELS))
    samplers: list[dict] = field(default_factory=lambda: [dict(s) for s in SAMPLER_CONFIGS])
    n_replicates: int = 5
    benchmark: str = "math500"
    n_problems: int = 200
    problem_seed: int = 0          # selects WHICH problems; unrelated to decoding
    provider: str = "fireworks"
    fixed: dict = field(default_factory=lambda: dict(FIXED))

    @property
    def n_generations(self) -> int:
        return len(self.models) * len(self.samplers) * self.n_replicates * self.n_problems

    def validate(self) -> None:
        if len(self.models) < 2:
            raise ValueError(
                "need >= 2 models; the headline compares sampler variance against "
                "model variance and there is no model variance with one model"
            )
        if len(self.samplers) < 2:
            raise ValueError("need >= 2 sampler configs")
        if self.n_replicates < 2:
            raise ValueError(
                "need >= 2 replicates per cell; with one, resampling variance is "
                "not separable from the cell mean"
            )
        ids = [s["id"] for s in self.samplers]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate sampler ids: {ids}")
        if len(set(self.models)) != len(self.models):
            raise ValueError(f"duplicate models: {self.models}")
        if self.benchmark not in BENCHMARKS:
            raise ValueError(
                f"unknown benchmark {self.benchmark!r}; known: {sorted(BENCHMARKS)}"
            )
        spec = BENCHMARKS[self.benchmark]
        if self.n_problems != spec["n_problems"]:
            raise ValueError(
                f"benchmark {self.benchmark!r} is frozen at {spec['n_problems']} "
                f"problems, config says {self.n_problems}; the decomposition is "
                "reported per benchmark and the frozen size is what the budget "
                "and the CIs were computed against"
            )
        for s in self.samplers:
            if "temperature" not in s:
                raise ValueError(f"sampler {s.get('id')!r} has no temperature")
            if s["temperature"] == 0 and ("top_p" in s or "top_k" in s or "min_p" in s):
                raise ValueError(
                    f"sampler {s['id']!r} sets a truncation parameter alongside "
                    "temperature 0, which is meaningless and reads as a mistake"
                )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def load(cls, path: str | Path) -> "Design":
        d = cls(**json.loads(Path(path).read_text()))
        d.validate()
        return d

    def save(self, path: str | Path) -> None:
        self.validate()
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n")


# --------------------------------------------------------------------------
# model selection
# --------------------------------------------------------------------------
def select_models(
    pilot: dict[str, float],
    k: int = N_MODEL_LEVELS,
    band: tuple[float, float] = PILOT_BAND,
    families: dict[str, str] | None = None,
) -> list[str]:
    """Pick the k model levels from measured pilot accuracies.

    Pre-registered, deterministic, and fixed before any pilot data exists. The
    rule is: keep every candidate whose measured accuracy falls inside `band`,
    then take the k-subset with the SMALLEST spread (max - min accuracy).

    Minimising spread is the whole point. The near-peer requirement is not a
    nicety: model variance enters the headline ratio's denominator, so a set that
    happens to straddle a 30-point capability gap reports a small sampler share
    for a reason that has nothing to do with samplers. The band excludes floor and
    ceiling separately, because both flatten variance in the numerator instead.

    Ties break toward more distinct families, then lexicographically, so the rule
    is reproducible and cannot be quietly re-run until it gives a nicer answer.
    """
    if k < 2:
        raise ValueError("need >= 2 model levels")
    lo, hi = band
    eligible = sorted(m for m, a in pilot.items() if lo <= a <= hi)
    if len(eligible) < k:
        raise ValueError(
            f"only {len(eligible)} of {len(pilot)} candidates landed inside the "
            f"pre-registered band {band}: {eligible}. Do NOT widen the band to "
            "make this pass — that is selecting on the outcome. Extend the "
            "shortlist and re-pilot instead."
        )
    families = families or {c["id"]: c["family"] for c in MODEL_CANDIDATES}

    from itertools import combinations

    def key(subset: tuple[str, ...]) -> tuple:
        accs = [pilot[m] for m in subset]
        return (
            round(max(accs) - min(accs), 12),        # tightest band first
            -len({families.get(m, m) for m in subset}),  # then most families
            subset,                                   # then deterministic
        )

    return list(min(combinations(eligible, k), key=key))

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
# design.
#
# Re-registered 2026-08-27, before any pilot data existed, because the original
# shortlist turned out not to exist: every Qwen2.5, Llama-3.x, Mistral-Small and
# Gemma entry returned 404. The Fireworks serverless catalogue is now eighteen
# chat models and all of them are reasoning models. The "non-reasoning instruct"
# criterion was not a preference that could be traded away — it was chosen so
# that decoding parameters would be the only thing moving accuracy — but it is
# no longer satisfiable on this provider at any price.
#
# What that costs the study is stated plainly rather than hidden: `reasoning_effort`
# is a decoding-adjacent knob that moves accuracy on its own, so it is pinned in
# FIXED and reported. It is, uncomfortably, exactly the kind of unreported
# configuration variable this paper is about — which makes it a Discussion point,
# not a defect to paper over.
#
# `usd_per_1m` is (input, output) at the serverless standard tier; see
# `pricing.py`. Cost is a real constraint at a $25 budget, and a candidate that
# cannot be afforded across the full grid is not a candidate.
#
# `sampler_support` records what `scripts/probe_fireworks.py` MEASURED on
# 2026-08-27, never what the docs claim. The probe found that `min_p` is honoured
# by some models on this provider and silently ignored by others, which is a far
# worse failure than a uniform one: the `minp` cell would be a genuine condition
# for two model levels and a duplicate of `hightemp` for the other two. That is
# not noise. It manufactures a model x sampler interaction out of nothing, and
# the interaction term is one of the quantities the two-way decomposition
# reports — the artifact would be indistinguishable from the finding.
#
# `deterministic_at_t0` is recorded separately. deepseek-v4-flash returned two
# distinct outputs at temperature 0, so its greedy cells would carry provider
# non-determinism that the design attributes to sampling. That is reportable in
# its own right, but it is not something to average over silently.
MODEL_CANDIDATES = [
    {"id": "accounts/fireworks/models/nemotron-lightning-3p5-30b-a3b", "family": "nvidia",
     "usd_per_1m": (0.05, 0.20),
     "sampler_support": {"temperature": True, "top_p": True, "top_k": True, "min_p": False},
     "deterministic_at_t0": True,
     "note": "only 2/8 distinct at T=1.5 — unusually narrow output distribution"},
    {"id": "accounts/fireworks/models/gpt-oss-20b", "family": "openai",
     "usd_per_1m": (0.07, 0.30),
     "sampler_support": {"temperature": True, "top_p": True, "top_k": True, "min_p": True},
     "deterministic_at_t0": True},
    {"id": "accounts/fireworks/models/gpt-oss-120b", "family": "openai",
     "usd_per_1m": (0.15, 0.60),
     "sampler_support": {"temperature": True, "top_p": True, "top_k": True, "min_p": True},
     "deterministic_at_t0": True},
    {"id": "accounts/fireworks/models/deepseek-v4-flash-0731", "family": "deepseek",
     "usd_per_1m": (0.22, 0.66),
     "sampler_support": {"temperature": True, "top_p": True, "top_k": True, "min_p": False},
     "deterministic_at_t0": False},
    {"id": "accounts/fireworks/models/minimax-m3", "family": "minimax",
     "usd_per_1m": (0.30, 1.20),
     "sampler_support": {"temperature": True, "top_p": True, "top_k": True, "min_p": True},
     "deterministic_at_t0": True},
    {"id": "accounts/fireworks/models/muse-glimmer-30b", "family": "muse",
     "usd_per_1m": (0.35, 1.50),
     "sampler_support": None,          # not yet probed
     "deterministic_at_t0": None},
]


def required_params(samplers: list[dict] | None = None) -> set[str]:
    """Every decoding parameter the grid actually varies."""
    samplers = samplers if samplers is not None else SAMPLER_CONFIGS
    return {k for s in samplers for k in s if k != "id"}


def supports_grid(candidate: dict, samplers: list[dict] | None = None) -> bool:
    """Does this model honour every parameter the grid needs?

    Unprobed counts as unsupported. The whole point of the probe is that an
    unverified parameter is indistinguishable from a working one until the
    numbers are already wrong.
    """
    support = candidate.get("sampler_support")
    if not support:
        return False
    return all(support.get(p) for p in required_params(samplers))

# Pre-registered selection rule, fixed BEFORE the pilot is run so the model set
# cannot be tuned until the headline looks good.
N_MODEL_LEVELS = 4
PILOT_BAND = (0.40, 0.97)
PILOT_N_PROBLEMS = 100
PILOT_SAMPLER = "standard"

# The pilot draws from held-out MATH-500 at levels 4-5 only, and this needs its
# reason on the record.
#
# The band belongs on the benchmark that carries the headline, which is now AIME:
# every model in the catalogue ceilings full MATH-500. But AIME has 60 problems
# and the sweep uses all 60, so there is no disjoint AIME material to pilot on,
# and piloting on the scored items would push selection noise straight into the
# model component — the denominator of the headline ratio. The hardest held-out
# MATH-500 stratum is the best available proxy: 157 problems the sweep never
# sees, difficult enough to separate models that full MATH-500 would not.
#
# The proxy is a real limitation and is reported as one. Near-peerness is
# established on hard MATH-500 and assumed to carry to AIME; it is not measured
# on AIME directly, and it cannot be without corrupting the thing it protects.
PILOT_BENCHMARK = "math500"
PILOT_STRATA = (4, 5)
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
    "max_tokens": 8192,
    "stop": None,
    "system_prompt": None,
    # Pinned, not left free. Every model on the provider is a reasoning model and
    # effort moves accuracy on its own; leaving it unset would let the provider
    # default drift and would put an unreported knob inside the model factor.
    # "low" is also the only setting that finishes: at "medium" a single AIME
    # generation exceeded a 180s read timeout, which at 31,200 generations is not
    # a budget problem but an impossibility.
    "reasoning_effort": "low",
}

# max_tokens 8192 is headroom, not a target. Measured worst case at low effort is
# ~2,100 output tokens on AIME. Truncation is not a neutral failure here: a
# response cut off mid-chain grades as unparseable rather than wrong, and
# truncation frequency rises with temperature, so a tight cap would manufacture
# sampler-correlated unparseability and report it as the finding. Billing is by
# tokens emitted, so the headroom is free.


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
    by_id = {c["id"]: c for c in MODEL_CANDIDATES}
    unsupported = sorted(
        m for m in pilot
        if m in by_id and not supports_grid(by_id[m])
    )
    if unsupported:
        # Excluded before the band is even consulted. A model that silently drops
        # a parameter turns that cell into a duplicate for that model only, which
        # fabricates a model x sampler interaction the decomposition would report
        # as a finding.
        print(f"excluded, cannot run the full sampler grid: {unsupported}")
    eligible = sorted(
        m for m, a in pilot.items()
        if lo <= a <= hi and (m not in by_id or supports_grid(by_id[m]))
    )
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

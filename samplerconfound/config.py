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
    # Added 2026-08-27, after the smoke run and before the sweep, to buy back
    # statistical power in the numerator.
    #
    # Trap #4 is the study's weakest link: with k factor levels, the realised
    # level variance scatters by roughly sqrt(2/(k-1)) — 71% at five levels, 63%
    # at seven. The model factor is stuck at four levels because the provider's
    # catalogue offers no fifth affordable near-peer candidate, so no budget buys
    # precision there. Sampler levels are purchasable, and the sampler component
    # is the headline's numerator.
    #
    # Both use only parameters measured as honoured by all four model levels
    # (temperature 6/8, top_p 7/8, top_k 8/8 across the probed catalogue), and
    # both are configurations practitioners actually ship, not padding chosen to
    # inflate a count: 0.5/0.9 is a common general-purpose default, and a high
    # temperature paired with a tight nucleus is the standard "varied but safe"
    # setting. Chosen before any sweep data existed.
    {"id": "midtemp", "temperature": 0.5, "top_p": 0.9},
    {"id": "tightnucleus", "temperature": 1.0, "top_p": 0.8},
]

# A sixth cell, {"id": "minp", "temperature": 1.0, "min_p": 0.05}, was dropped on
# 2026-08-27 after probing. min_p is honoured by three of the eight models
# measured on this provider and silently ignored by the other five. Support is
# per-model, not per-provider, which is the worst arrangement: the minp cell
# would have been a genuine condition for some model levels and an exact
# duplicate of `hightemp` for others, fabricating a model x sampler interaction
# out of nothing. The interaction term is one of the quantities the two-way
# decomposition reports, so the artifact would have been indistinguishable from
# the finding.
#
# The cell is not lost, it is relocated. "One of the six decoding configurations
# we set out to study cannot be studied, because a widely-used parameter is
# accepted and discarded by most models on a major provider, silently and
# undocumented" is an instance of this paper's own thesis, and belongs in the
# Discussion. See runs/probe_params_*.json for the evidence.
#
# Measured support, 2026-08-27, eight models:
#   temperature   6/8   (minimax-m2p7 caps at 1.0; four are non-deterministic at 0)
#   top_p         7/8   (muse-glimmer-30b ignores it)
#   top_k         8/8
#   min_p         3/8

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
     "sampler_support": {"temperature": True, "top_p": False, "top_k": True, "min_p": False},
     "deterministic_at_t0": False,
     "note": "ignores top_p, so it cannot run `standard` — the most consequential cell"},
    {"id": "accounts/fireworks/models/minimax-m2p7", "family": "minimax",
     "usd_per_1m": (0.30, 1.20),
     "sampler_support": {"temperature": False, "top_p": None, "top_k": None, "min_p": None},
     "deterministic_at_t0": False,
     "note": "rejects temperature > 1.0 outright, so the grid's range is unreachable"},
    {"id": "accounts/fireworks/models/kimi-k2p6", "family": "moonshot",
     "usd_per_1m": (0.95, 4.00),
     "sampler_support": {"temperature": True, "top_p": True, "top_k": True, "min_p": False},
     "deterministic_at_t0": False,
     "note": "degenerates into multilingual token soup at T=1.5; also unaffordable"},
]

# The credit on the account, plus the headroom its owner explicitly allowed on
# 2026-08-27. Still not a soft target: the failure mode is not overspending but
# running out mid-sweep, and an unbalanced grid gives variance.py no headline at
# all rather than a noisier one.
BUDGET_USD = 30.0

# Lowered from 1.5 once the smoke run replaced guesses with measurements. The
# blunt factor existed because token counts came from three problems at one
# temperature; they now come from 1,300 generations spanning every sampler and
# every model level, recorded per model in MODEL_TOKENS. Real uncertainty
# remains — the sweep's problems are not the smoke run's — so the factor is
# reduced rather than removed.
BUDGET_SAFETY = 1.25

# Per-generation (input, output) token counts, MEASURED by the 1/20 smoke run on
# 2026-08-27 rather than guessed. The first estimate assumed 300 output tokens on
# MATH-500; the true mean is 1,021, and 2,252 on AIME. That alone put the grid at
# $30.05 against a $25 budget.
TOKENS_PER_GENERATION = {
    "math500": (165, 1021),
    "aime": (245, 2252),
}

# One global profile is not enough, and this is the more important lesson from
# the smoke run. Output length varies by a factor of four ACROSS MODELS, and it
# correlates with price: minimax-m3 emits 2,057 tokens where gpt-oss-120b emits
# 519, and also costs four times as much per token. Costed on the global mean it
# looked affordable; measured, its share of the grid is $20.26 of a $25 budget
# while the other three levels together come to $9.79. A per-benchmark average
# hides exactly the model that breaks the budget.
MODEL_TOKENS = {
    "accounts/fireworks/models/gpt-oss-20b":            {"math500": (165, 598),  "aime": (245, 1482)},
    "accounts/fireworks/models/gpt-oss-120b":           {"math500": (165, 519),  "aime": (245, 1424)},
    "accounts/fireworks/models/deepseek-v4-flash-0731": {"math500": (165, 911),  "aime": (245, 1962)},
    "accounts/fireworks/models/minimax-m3":             {"math500": (165, 2057), "aime": (245, 4142)},
    # nemotron measured on the grader-check corpus rather than the smoke sweep,
    # so its MATH-500 figure comes from the harder pilot split (levels 4-5) and
    # is an overestimate for the sweep's stratified draw. Erring high is the
    # right direction for a budget check.
    "accounts/fireworks/models/nemotron-lightning-3p5-30b-a3b":
                                                        {"math500": (165, 1576), "aime": (245, 5980)},
}


def required_params(samplers: list[dict] | None = None) -> set[str]:
    """Every decoding parameter the grid actually varies."""
    samplers = samplers if samplers is not None else SAMPLER_CONFIGS
    return {k for s in samplers for k in s if k != "id"}


def grid_cost_usd(candidate: dict, samplers: list[dict] | None = None,
                  n_replicates: int = 5) -> float:
    """What this one model's share of the full grid costs, across both benchmarks."""
    samplers = samplers if samplers is not None else SAMPLER_CONFIGS
    p_in, p_out = candidate["usd_per_1m"]
    measured = MODEL_TOKENS.get(candidate["id"], {})
    total = 0.0
    for benchmark, spec in BENCHMARKS.items():
        # Prefer this model's measured profile; fall back to the global mean for
        # a candidate the smoke run never touched. An unmeasured model is costed
        # optimistically, so anything new should be smoke-run before it is trusted.
        t_in, t_out = measured.get(benchmark, TOKENS_PER_GENERATION[benchmark])
        n = len(samplers) * n_replicates * spec["n_problems"]
        total += n * (t_in * p_in + t_out * p_out) / 1_000_000
    return total


def set_cost_usd(models: list[str] | tuple[str, ...],
                 samplers: list[dict] | None = None) -> float:
    """Budgeted cost of running the full grid over exactly these model levels."""
    by_id = {c["id"]: c for c in MODEL_CANDIDATES}
    return BUDGET_SAFETY * sum(
        grid_cost_usd(by_id[m], samplers) for m in models if m in by_id
    )


def affordable(models: list[str] | tuple[str, ...],
               samplers: list[dict] | None = None) -> bool:
    """Does this whole set of levels fit the credit on the account?

    Applied to the SET, not to each candidate. An even per-model division looks
    tidier and is wrong: it rejects a model that costs slightly more than its
    share even when the other three are cheap enough to cover it, which throws
    away sets the account can plainly afford. With only a handful of eligible
    candidates, discarding a viable set on a rounding rule leaves the selection
    rule no room to do the near-peer job it exists for.
    """
    return set_cost_usd(models, samplers) <= BUDGET_USD


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
    def usable(m: str) -> bool:
        return m not in by_id or supports_grid(by_id[m])

    # Excluded before the band is consulted, and loudly. A model that silently
    # drops a parameter turns that cell into a duplicate for that model only,
    # fabricating a model x sampler interaction the decomposition would report as
    # a finding.
    for m in sorted(pilot):
        if not usable(m):
            print(f"  excluded (cannot run the full sampler grid): {m}")
    eligible = sorted(m for m, a in pilot.items() if lo <= a <= hi and usable(m))
    if len(eligible) < k:
        raise ValueError(
            f"only {len(eligible)} of {len(pilot)} candidates landed inside the "
            f"pre-registered band {band}: {eligible}. Do NOT widen the band to "
            "make this pass — that is selecting on the outcome. Extend the "
            "shortlist and re-pilot instead."
        )
    families = families or {c["id"]: c["family"] for c in MODEL_CANDIDATES}

    from itertools import combinations

    # Budget is a constraint on the SET, so it is applied to whole subsets rather
    # than filtered per candidate. Running out of credit part way through leaves
    # an unbalanced grid, and variance.py needs equal cell counts for the sums of
    # squares to be orthogonal — a half-finished sweep yields no headline.
    subsets = [c for c in combinations(eligible, k) if affordable(c)]
    if not subsets:
        cheapest = min(
            (set_cost_usd(c) for c in combinations(eligible, k)), default=float("inf")
        )
        raise ValueError(
            f"no affordable set of {k} levels: the cheapest is ${cheapest:.2f} "
            f"against a ${BUDGET_USD:.2f} budget at {BUDGET_SAFETY}x safety. "
            "Drop a sampler cell, cut replicates, or reduce the model levels — "
            "do not raise the budget past the credit that actually exists."
        )

    def key(subset: tuple[str, ...]) -> tuple:
        accs = [pilot[m] for m in subset]
        return (
            round(max(accs) - min(accs), 12),        # tightest band first
            -len({families.get(m, m) for m in subset}),  # then most families
            subset,                                   # then deterministic
        )

    return list(min(subsets, key=key))

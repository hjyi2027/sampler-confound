"""Does the ranking flip when only the sampler changes? The paper's headline.

Written as "core metric 2", promoted on 2026-08-27 to the claim the paper leads
with. The variance decomposition answers "how big is the sampler effect", which
is the statistically correct question and not the one that persuades. A reader
grants that evaluation is noisy; what they will not have granted is that the
noise is large enough to reverse a published conclusion.

It also leads because it is the one the design can support. The sampler:model
variance ratio is estimated from three model levels, and a variance from k levels
has relative scatter sqrt(2/(k-1)) — 100% at three. Simulation at this grid's
exact shape put the ratio's interval clearing its 0.1 threshold only 57% of the
time even when sampler and model variance were truly EQUAL (scripts/power_check.py).
The inversion rate needs no few-level extrapolation: it counts outcomes over
observed cells, with a binomial standard error near 6% at 63 comparisons.

So this module asks the concrete version. Take two models. Rank them under
sampler S1. Rank them under sampler S2. Nothing else changed — same problems,
same prompt, same grader. How often does the ranking reverse?

Two rates are reported and the distinction matters:

  raw        the sign of the accuracy difference flips. Includes flips between
             two models that are, in truth, tied — where a flip is meaningless.
  decisive   the difference is separated from zero in BOTH sampler conditions,
             in opposite directions. This is the version that constitutes a harm:
             under one undocumented decoding choice you would confidently report
             A > B, and under another you would confidently report B > A.

Only the decisive rate should be quoted in the abstract. The raw rate is reported
alongside it because suppressing it would look like the decisive rate was chosen
after seeing which was larger.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Inversions:
    n_models: int
    n_samplers: int
    n_reps: int
    z: float
    n_comparisons: int = 0
    n_raw: int = 0
    n_decisive: int = 0
    raw_rate: float = float("nan")
    decisive_rate: float = float("nan")
    pairs_ever_inverted: list[str] = field(default_factory=list)
    examples: list[dict] = field(default_factory=list)
    sampler_range: list[dict] = field(default_factory=list)
    model_gaps: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "n_models": self.n_models,
            "n_samplers": self.n_samplers,
            "n_reps": self.n_reps,
            "z": self.z,
            "n_comparisons": self.n_comparisons,
            "n_raw": self.n_raw,
            "n_decisive": self.n_decisive,
            "raw_rate": self.raw_rate,
            "decisive_rate": self.decisive_rate,
            "pairs_ever_inverted": self.pairs_ever_inverted,
            "examples": self.examples,
            "sampler_range": self.sampler_range,
            "model_gaps": self.model_gaps,
        }


def _cells(accuracy, model_ids, sampler_ids):
    """Group replicate accuracies into a {(model, sampler): array} table."""
    y = np.asarray(accuracy, dtype=np.float64).ravel()
    table: dict[tuple[str, str], list[float]] = {}
    for v, m, s in zip(y, model_ids, sampler_ids):
        table.setdefault((m, s), []).append(float(v))
    return {k: np.asarray(v, dtype=np.float64) for k, v in table.items()}


def inversion_rate(
    accuracy,
    model_ids: list[str],
    sampler_ids: list[str],
    z: float = 1.96,
) -> Inversions:
    """Rate at which a model-pair ranking reverses across sampler configurations.

    The denominator is every (model pair, sampler pair) comparison: with 3 models
    and 6 samplers that is 3 * 15 = 45 comparisons. Reporting the count alongside
    the rate is not optional — a rate over an unstated denominator is the kind of
    number reviewers correctly refuse to accept.

    `z` sets the decisiveness threshold on the difference of two cell means, using
    the replicate standard error within each cell. z=1.96 is a nominal 95% cut.
    No multiplicity correction is applied and the paper must say so: these
    comparisons are not independent, and the decisive rate is descriptive, not a
    family of hypothesis tests.
    """
    cells = _cells(accuracy, model_ids, sampler_ids)
    models = sorted({m for m, _ in cells})
    samplers = sorted({s for _, s in cells})
    if len(models) < 2:
        raise ValueError("need >= 2 models to have a ranking to invert")
    if len(samplers) < 2:
        raise ValueError("need >= 2 sampler configs to change the sampler")

    missing = [
        (m, s) for m in models for s in samplers if (m, s) not in cells
    ]
    if missing:
        raise ValueError(f"incomplete model x sampler grid; missing cells: {missing}")

    reps = {len(v) for v in cells.values()}
    if len(reps) != 1:
        raise ValueError(f"unbalanced replicates per cell: {sorted(reps)}")
    n = reps.pop()
    if n < 2:
        raise ValueError("need >= 2 replicates per cell to estimate the standard error")

    mean = {k: float(v.mean()) for k, v in cells.items()}
    # ddof=1: these replicates estimate the spread of a rerun, not describe itself.
    sem = {k: float(v.std(ddof=1) / np.sqrt(n)) for k, v in cells.items()}

    n_comp = n_raw = n_dec = 0
    inverted_pairs: set[str] = set()
    examples: list[dict] = []

    for m1, m2 in itertools.combinations(models, 2):
        label = f"{m1} vs {m2}"
        for s1, s2 in itertools.combinations(samplers, 2):
            d1 = mean[(m1, s1)] - mean[(m2, s1)]
            d2 = mean[(m1, s2)] - mean[(m2, s2)]
            n_comp += 1
            if d1 == 0.0 or d2 == 0.0 or np.sign(d1) == np.sign(d2):
                continue
            n_raw += 1
            inverted_pairs.add(label)

            se1 = np.hypot(sem[(m1, s1)], sem[(m2, s1)])
            se2 = np.hypot(sem[(m1, s2)], sem[(m2, s2)])
            decisive = abs(d1) > z * se1 and abs(d2) > z * se2
            if decisive:
                n_dec += 1
            examples.append(
                {
                    "models": [m1, m2],
                    "samplers": [s1, s2],
                    "diff_under_first": d1,
                    "diff_under_second": d2,
                    "se_first": float(se1),
                    "se_second": float(se2),
                    "decisive": bool(decisive),
                }
            )

    # Context the ratio alone does not give: how far a single model's reported
    # number travels under nothing but a decoding change, next to the gaps
    # between models that the literature reads as progress.
    ranges = []
    for m in models:
        vals = np.array([mean[(m, s)] for s in samplers])
        lo_s = samplers[int(np.argmin(vals))]
        hi_s = samplers[int(np.argmax(vals))]
        ranges.append(
            {
                "model": m,
                "min_accuracy": float(vals.min()),
                "max_accuracy": float(vals.max()),
                "range": float(vals.max() - vals.min()),
                "argmin_sampler": lo_s,
                "argmax_sampler": hi_s,
            }
        )

    gaps = []
    for m1, m2 in itertools.combinations(models, 2):
        a1 = float(np.mean([mean[(m1, s)] for s in samplers]))
        a2 = float(np.mean([mean[(m2, s)] for s in samplers]))
        gaps.append(
            {
                "models": [m1, m2],
                "mean_accuracy_gap": abs(a1 - a2),
            }
        )

    return Inversions(
        n_models=len(models),
        n_samplers=len(samplers),
        n_reps=n,
        z=z,
        n_comparisons=n_comp,
        n_raw=n_raw,
        n_decisive=n_dec,
        raw_rate=n_raw / n_comp if n_comp else float("nan"),
        decisive_rate=n_dec / n_comp if n_comp else float("nan"),
        pairs_ever_inverted=sorted(inverted_pairs),
        examples=examples,
        sampler_range=ranges,
        model_gaps=gaps,
    )


# --------------------------------------------------------------------------
# paired, problem-level version — the one to report
# --------------------------------------------------------------------------
def inversion_rate_paired(
    correct,
    model_ids: list[str],
    sampler_ids: list[str],
    problem_ids: list[str],
    z: float = 1.96,
) -> Inversions:
    """Inversion rate with a decisiveness test that uses the right standard error.

    `inversion_rate` above judges decisiveness with the REPLICATE standard error
    inside each cell. That has a failure mode this study walks straight into: at
    temperature 0 a deterministic provider returns identical replicates, the
    within-cell SEM is exactly zero, and `abs(d) > z * 0` holds for any nonzero
    difference. Every greedy comparison is then "decisive" by construction — and
    greedy is the configuration evaluation harnesses claim to use, so the defect
    lands precisely on the paper's motivating case. Measured on the smoke run,
    two of four greedy cells had an SEM of exactly zero.

    The deeper issue is that the replicate SEM answers "would a rerun flip this?"
    when the claim being made is "would a published comparison have come out the
    other way?". That claim is about benchmark noise, which is dominated by which
    problems are in the set, not by resampling at fixed configuration.

    So the standard error here is computed over PROBLEMS, paired. For models A and
    B under one sampler, each problem contributes

        delta_p = (A's solve rate on p) - (B's solve rate on p)

    averaged over replicates, and SE(d) = sd(delta_p) / sqrt(P). Pairing matters:
    both models face the same problems, so the shared component of problem
    difficulty cancels instead of inflating the error. The result stays finite
    when a cell is deterministic, because problem-to-problem variation does not
    disappear just because decoding is greedy.
    """
    y = np.asarray(correct, dtype=np.float64).ravel()
    per: dict[tuple[str, str, str], list[float]] = {}
    for v, m, s, p in zip(y, model_ids, sampler_ids, problem_ids):
        per.setdefault((m, s, p), []).append(float(v))

    models = sorted({m for m, _, _ in per})
    samplers = sorted({s for _, s, _ in per})
    problems = sorted({p for _, _, p in per})
    if len(models) < 2:
        raise ValueError("need >= 2 models to have a ranking to invert")
    if len(samplers) < 2:
        raise ValueError("need >= 2 sampler configs to change the sampler")

    missing = [(m, s, p) for m in models for s in samplers for p in problems
               if (m, s, p) not in per]
    if missing:
        raise ValueError(
            f"incomplete grid; {len(missing)} missing (model, sampler, problem) "
            f"cells, e.g. {missing[:3]}"
        )
    reps = {len(v) for v in per.values()}
    if len(reps) != 1:
        raise ValueError(f"unbalanced replicates per cell: {sorted(reps)}")
    n_reps = reps.pop()

    # Solve rate per (model, sampler, problem), then cell accuracy over problems.
    rate = {k: float(np.mean(v)) for k, v in per.items()}
    mean = {
        (m, s): float(np.mean([rate[(m, s, p)] for p in problems]))
        for m in models for s in samplers
    }

    def paired_se(m1: str, m2: str, s: str) -> float:
        d = np.array([rate[(m1, s, p)] - rate[(m2, s, p)] for p in problems])
        if d.size < 2:
            return float("inf")
        return float(d.std(ddof=1) / np.sqrt(d.size))

    n_comp = n_raw = n_dec = 0
    inverted_pairs: set[str] = set()
    examples: list[dict] = []
    for m1, m2 in itertools.combinations(models, 2):
        label = f"{m1} vs {m2}"
        for s1, s2 in itertools.combinations(samplers, 2):
            d1 = mean[(m1, s1)] - mean[(m2, s1)]
            d2 = mean[(m1, s2)] - mean[(m2, s2)]
            n_comp += 1
            if d1 == 0.0 or d2 == 0.0 or np.sign(d1) == np.sign(d2):
                continue
            n_raw += 1
            inverted_pairs.add(label)
            se1, se2 = paired_se(m1, m2, s1), paired_se(m1, m2, s2)
            decisive = abs(d1) > z * se1 and abs(d2) > z * se2
            if decisive:
                n_dec += 1
            examples.append({
                "models": [m1, m2], "samplers": [s1, s2],
                "diff_under_first": d1, "diff_under_second": d2,
                "se_first": se1, "se_second": se2, "decisive": bool(decisive),
            })

    ranges = []
    for m in models:
        vals = np.array([mean[(m, s)] for s in samplers])
        ranges.append({
            "model": m,
            "min_accuracy": float(vals.min()), "max_accuracy": float(vals.max()),
            "range": float(vals.max() - vals.min()),
            "argmin_sampler": samplers[int(np.argmin(vals))],
            "argmax_sampler": samplers[int(np.argmax(vals))],
        })
    gaps = [
        {"models": [m1, m2],
         "mean_gap": float(np.mean([mean[(m1, s)] - mean[(m2, s)] for s in samplers]))}
        for m1, m2 in itertools.combinations(models, 2)
    ]

    return Inversions(
        n_models=len(models), n_samplers=len(samplers), n_reps=n_reps, z=z,
        n_comparisons=n_comp, n_raw=n_raw, n_decisive=n_dec,
        raw_rate=(n_raw / n_comp if n_comp else float("nan")),
        decisive_rate=(n_dec / n_comp if n_comp else float("nan")),
        pairs_ever_inverted=sorted(inverted_pairs),
        examples=examples, sampler_range=ranges, model_gaps=gaps,
    )

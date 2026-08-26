"""Decompose correctness variance into model, sampler, problem, and resampling.

Ported from *Unauthored by Design* (`seed-study-creative/seedstudy/variance.py`).
Three things changed, and each is deliberate.

1. **The response is a scalar, not a feature vector.** Correctness collapses to
   one number, so the sums of squares are ordinary scalar sums of squares rather
   than sums over feature dimensions.

2. **No standardisation.** The original standardised features so no dimension
   dominated. Here standardising would throw away the only interpretable unit we
   have. Components come out in accuracy-squared, so `sqrt` of a component is
   readable as *accuracy points* — "changing only the sampler moves the reported
   number by +/- 2.1 points" is the sentence the paper needs, and a scale-free
   share cannot produce it. Shares are reported too; they are scale-free anyway.

3. **The replicate dimension is resampling, not seed.** Seed was never a main
   effect in the original design — its levels carry no meaning across cells — so
   it was already the replicate stratum. That survives intact here, and it is why
   the design does not care whether the provider honours `seed` (Fireworks does
   not, for text). Within-cell variance is the variance a practitioner eats on a
   rerun, which is the quantity that matters regardless.

Three entry points, one per dependent variable:

    decompose_accuracy     two-way model x sampler on benchmark accuracy.
                           The headline. Stated in the units papers publish in.
    decompose_items        three-way model x sampler x problem on BINARY
                           correctness. Shows where the variance lives, at the
                           cost of a response whose variance is pinned to its
                           own mean.
    decompose_solve_rate   three-way on CONTINUOUS per-problem solve rate, the
                           fraction of replicates that solved each problem.
                           Breaks the Bernoulli mean-variance coupling; spends
                           the replicate stratum to do it.

The binary and solve-rate views are not redundant. Reporting both is what shows
that the result is a fact about the data rather than about the response scale —
if they disagree, the disagreement is the finding.

Both report eta-squared (share of variance in THIS sample, inflated for the
between-factors because their mean squares carry replicate noise) and EMS-corrected
variance components (the honest apples-to-apples estimate). The headline claim
rests on the components.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------


def _codes(labels) -> tuple[np.ndarray, list]:
    levels = sorted(set(labels))
    index = {lv: i for i, lv in enumerate(levels)}
    return np.array([index[x] for x in labels]), levels


def _grid(y: np.ndarray, codes: list[np.ndarray], shape: tuple[int, ...]) -> np.ndarray:
    """Reshape a balanced fully crossed design into an (levels..., n) array.

    Raises rather than guessing if the design is unbalanced or has holes. A silent
    imbalance would make the sums of squares non-orthogonal and every component
    estimate quietly wrong, which is exactly the class of bug that kills a
    benchmark paper.
    """
    n_cells = int(np.prod(shape))
    flat_cell = np.zeros(len(y), dtype=np.int64)
    stride = 1
    for c, size in zip(reversed(codes), reversed(shape)):
        flat_cell += c * stride
        stride *= size

    counts = np.bincount(flat_cell, minlength=n_cells)
    sizes = set(counts.tolist())
    if len(sizes) != 1 or 0 in sizes:
        lo, hi = min(counts), max(counts)
        raise ValueError(
            f"unbalanced design: cell sizes range {lo}..{hi} over {n_cells} cells. "
            "Rerun `generate` to fill missing cells, or drop the incomplete ones. "
            "The decomposition assumes balance and would be wrong without it."
        )
    n = sizes.pop()
    if n < 2:
        raise ValueError(
            "need >= 2 replicates per cell to separate resampling variance from "
            "the cell mean"
        )

    out = np.empty((n_cells, n), dtype=np.float64)
    order = np.argsort(flat_cell, kind="stable")
    out[:] = y[order].reshape(n_cells, n)
    return out.reshape(*shape, n)


def _rows(names: list[str], df: dict, ss: dict, ms: dict, comps: dict, total_ss: float) -> list[dict]:
    comp_total = sum(comps.values()) or 1.0
    table = []
    for s in names:
        table.append(
            {
                "source": s,
                "df": df[s],
                "ss": ss[s],
                "ms": ms[s],
                "eta2": ss[s] / total_ss if total_ss else 0.0,
                "var_component": comps[s],
                "var_share": comps[s] / comp_total,
                # sqrt of a component is in the response's own units. For accuracy
                # that is accuracy points, which is the quotable form.
                "sd": float(np.sqrt(comps[s])),
            }
        )
    return table


# --------------------------------------------------------------------------
# primary: two-way, model x sampler, on benchmark accuracy
# --------------------------------------------------------------------------


@dataclass
class TwoWay:
    n_models: int
    n_samplers: int
    n_reps: int
    table: list[dict] = field(default_factory=list)
    var_share: dict = field(default_factory=dict)
    sampler_to_model: float = float("nan")
    sampler_to_model_ci: tuple[float, float] = (float("nan"), float("nan"))
    grand_mean: float = float("nan")

    def get(self, source: str) -> dict:
        for row in self.table:
            if row["source"] == source:
                return row
        raise KeyError(source)

    def to_dict(self) -> dict:
        d = {
            "n_models": self.n_models,
            "n_samplers": self.n_samplers,
            "n_reps": self.n_reps,
            "table": self.table,
            "var_share": self.var_share,
            "sampler_to_model": self.sampler_to_model,
            "sampler_to_model_ci": list(self.sampler_to_model_ci),
            "grand_mean": self.grand_mean,
        }
        return d


def _two_way_ss(G: np.ndarray) -> tuple[dict, dict]:
    """Balanced two-way sums of squares from an (a, b, n) array."""
    a, b, n = G.shape
    grand = G.mean()
    cell = G.mean(axis=2)
    am = cell.mean(axis=1)
    bm = cell.mean(axis=0)

    ss = {
        "model": float(n * b * ((am - grand) ** 2).sum()),
        "sampler": float(n * a * ((bm - grand) ** 2).sum()),
        "model:sampler": float(
            n * ((cell - am[:, None] - bm[None, :] + grand) ** 2).sum()
        ),
        "resampling": float(((G - cell[:, :, None]) ** 2).sum()),
    }
    ss["total"] = float(((G - grand) ** 2).sum())
    df = {
        "model": a - 1,
        "sampler": b - 1,
        "model:sampler": (a - 1) * (b - 1),
        "resampling": a * b * (n - 1),
    }
    return ss, df


def _two_way_components(ms: dict, a: int, b: int, n: int) -> dict:
    """EMS-corrected components, both factors random.

        E[MS_A]  = s2_e + n*s2_AB + n*b*s2_A
        E[MS_B]  = s2_e + n*s2_AB + n*a*s2_B
        E[MS_AB] = s2_e + n*s2_AB
        E[MS_E]  = s2_e

    Both factors are treated as random on purpose. The claim is about the variance
    a *typical* sampler or a *typical* model induces, not about these six configs
    and these three checkpoints. Components clamp at zero: the ANOVA estimator can
    go negative when the true component is near zero.
    """
    s2_e = ms["resampling"]
    s2_ab = max(0.0, (ms["model:sampler"] - s2_e) / n)
    s2_a = max(0.0, (ms["model"] - ms["model:sampler"]) / (n * b))
    s2_b = max(0.0, (ms["sampler"] - ms["model:sampler"]) / (n * a))
    return {"model": s2_a, "sampler": s2_b, "model:sampler": s2_ab, "resampling": s2_e}


def decompose_accuracy(
    accuracy,
    model_ids: list[str],
    sampler_ids: list[str],
    n_boot: int = 2000,
    random_state: int = 0,
) -> TwoWay:
    """Two-way random-effects decomposition of benchmark accuracy.

    `accuracy` is one number per (model, sampler, replicate) — the number a paper
    would report. The same problem set appears in every cell, so problem is a
    within-subject constant and correctly drops out of this analysis.

    The headline is `sampler_to_model`: the ratio of the sampler variance
    component to the model variance component. Near or above 1 means decoding
    configuration moves the reported number as much as swapping the model does.
    """
    y = np.asarray(accuracy, dtype=np.float64).ravel()
    a_code, models = _codes(model_ids)
    b_code, samplers = _codes(sampler_ids)
    a, b = len(models), len(samplers)
    if a < 2 or b < 2:
        raise ValueError("need >= 2 models and >= 2 sampler configs")

    G = _grid(y, [a_code, b_code], (a, b))
    n = G.shape[2]

    ss, df = _two_way_ss(G)
    names = ["model", "sampler", "model:sampler", "resampling"]
    ms = {k: (ss[k] / df[k] if df[k] > 0 else 0.0) for k in names}
    comps = _two_way_components(ms, a, b, n)

    ratio = (
        comps["sampler"] / comps["model"]
        if comps["model"] > 0
        else (float("inf") if comps["sampler"] > 0 else float("nan"))
    )
    ci = (
        _boot_ratio_two_way(G, n_boot, random_state, ratio)
        if n_boot
        else (float("nan"), float("nan"))
    )

    table = _rows(names, df, ss, ms, comps, ss["total"])
    return TwoWay(
        n_models=a,
        n_samplers=b,
        n_reps=n,
        table=table,
        var_share={r["source"]: r["var_share"] for r in table},
        sampler_to_model=ratio,
        sampler_to_model_ci=ci,
        grand_mean=float(G.mean()),
    )


def _boot_ratio_two_way(G, n_boot, random_state, observed) -> tuple[float, float]:
    """Bootstrap over replicates within cell — the replication the claim uses.

    Deliberately NOT a bootstrap over model levels. With three or four models,
    resampling models with replacement produces duplicated levels that collapse
    the between-model sum of squares and would report an interval that is mostly
    an artifact of the resampling scheme. The honest consequence is that this
    interval covers resampling uncertainty only, and the model component is
    estimated from very few levels. That belongs in Limitations, stated plainly,
    not hidden inside a reassuring interval.

    Basic (reverse-percentile) interval on the log scale: a ratio is bounded below
    by zero, and the log keeps the reflection from crossing it.
    """
    rng = np.random.default_rng(random_state)
    a, b, n = G.shape
    ratios = []
    for _ in range(n_boot):
        take = rng.integers(0, n, size=(a, b, n))
        Gb = np.take_along_axis(G, take, axis=2)
        ss, df = _two_way_ss(Gb)
        ms = {
            k: (ss[k] / df[k] if df[k] > 0 else 0.0)
            for k in ("model", "sampler", "model:sampler", "resampling")
        }
        c = _two_way_components(ms, a, b, n)
        if c["model"] > 0:
            ratios.append(c["sampler"] / c["model"])
        elif c["sampler"] > 0:
            ratios.append(np.inf)

    arr = np.asarray(ratios, dtype=np.float64)
    usable = arr[np.isfinite(arr) & (arr > 0)]
    if usable.size < 20:
        return (float("nan"), float("nan"))
    inf_frac = 1.0 - usable.size / max(arr.size, 1)

    if not np.isfinite(observed) or observed <= 0:
        lo = float(np.percentile(usable, 2.5))
        hi = float("inf") if inf_frac > 0.025 else float(np.percentile(usable, 97.5))
        return (lo, hi)

    logs = np.log(usable)
    q_lo, q_hi = np.percentile(logs, [2.5, 97.5])
    centre = np.log(observed)
    lo = float(np.exp(2 * centre - q_hi))
    hi = float("inf") if inf_frac > 0.025 else float(np.exp(2 * centre - q_lo))
    return (lo, hi)


# --------------------------------------------------------------------------
# secondary: three-way, model x sampler x problem, on binary outcomes
# --------------------------------------------------------------------------


@dataclass
class ThreeWay:
    n_models: int
    n_samplers: int
    n_problems: int
    n_reps: int
    table: list[dict] = field(default_factory=list)
    var_share: dict = field(default_factory=dict)
    grand_mean: float = float("nan")

    def get(self, source: str) -> dict:
        for row in self.table:
            if row["source"] == source:
                return row
        raise KeyError(source)

    def to_dict(self) -> dict:
        return {
            "n_models": self.n_models,
            "n_samplers": self.n_samplers,
            "n_problems": self.n_problems,
            "n_reps": self.n_reps,
            "table": self.table,
            "var_share": self.var_share,
            "grand_mean": self.grand_mean,
        }


SOURCES3 = [
    "model",
    "sampler",
    "problem",
    "model:sampler",
    "model:problem",
    "sampler:problem",
    "model:sampler:problem",
    "resampling",
]


def _three_way_ss(G: np.ndarray) -> tuple[dict, dict]:
    a, b, c, n = G.shape
    grand = G.mean()
    cell = G.mean(axis=3)                       # (a, b, c)
    A = cell.mean(axis=(1, 2))                  # (a,)
    B = cell.mean(axis=(0, 2))                  # (b,)
    C = cell.mean(axis=(0, 1))                  # (c,)
    AB = cell.mean(axis=2)                      # (a, b)
    AC = cell.mean(axis=1)                      # (a, c)
    BC = cell.mean(axis=0)                      # (b, c)

    ss = {}
    ss["model"] = float(n * b * c * ((A - grand) ** 2).sum())
    ss["sampler"] = float(n * a * c * ((B - grand) ** 2).sum())
    ss["problem"] = float(n * a * b * ((C - grand) ** 2).sum())
    ss["model:sampler"] = float(
        n * c * ((AB - A[:, None] - B[None, :] + grand) ** 2).sum()
    )
    ss["model:problem"] = float(
        n * b * ((AC - A[:, None] - C[None, :] + grand) ** 2).sum()
    )
    ss["sampler:problem"] = float(
        n * a * ((BC - B[:, None] - C[None, :] + grand) ** 2).sum()
    )
    resid3 = (
        cell
        - AB[:, :, None]
        - AC[:, None, :]
        - BC[None, :, :]
        + A[:, None, None]
        + B[None, :, None]
        + C[None, None, :]
        - grand
    )
    ss["model:sampler:problem"] = float(n * (resid3 ** 2).sum())
    ss["resampling"] = float(((G - cell[:, :, :, None]) ** 2).sum())
    ss["total"] = float(((G - grand) ** 2).sum())

    df = {
        "model": a - 1,
        "sampler": b - 1,
        "problem": c - 1,
        "model:sampler": (a - 1) * (b - 1),
        "model:problem": (a - 1) * (c - 1),
        "sampler:problem": (b - 1) * (c - 1),
        "model:sampler:problem": (a - 1) * (b - 1) * (c - 1),
        "resampling": a * b * c * (n - 1),
    }
    return ss, df


def _three_way_components(ms: dict, a: int, b: int, c: int, n: int) -> dict:
    """EMS-corrected components for a balanced three-factor random model.

        E[MS_A]   = s2_e + n*s2_ABC + n*c*s2_AB + n*b*s2_AC + n*b*c*s2_A
        E[MS_AB]  = s2_e + n*s2_ABC + n*c*s2_AB
        E[MS_ABC] = s2_e + n*s2_ABC
        E[MS_E]   = s2_e
        (and the symmetric cases)

    Solved top-down. Clamped at zero for the same reason as the two-way.
    """
    s2_e = ms["resampling"]
    s2_abc = max(0.0, (ms["model:sampler:problem"] - s2_e) / n)
    ms_abc = ms["model:sampler:problem"]
    s2_ab = max(0.0, (ms["model:sampler"] - ms_abc) / (n * c))
    s2_ac = max(0.0, (ms["model:problem"] - ms_abc) / (n * b))
    s2_bc = max(0.0, (ms["sampler:problem"] - ms_abc) / (n * a))
    s2_a = max(
        0.0,
        (ms["model"] - ms["model:sampler"] - ms["model:problem"] + ms_abc) / (n * b * c),
    )
    s2_b = max(
        0.0,
        (ms["sampler"] - ms["model:sampler"] - ms["sampler:problem"] + ms_abc)
        / (n * a * c),
    )
    s2_c = max(
        0.0,
        (ms["problem"] - ms["model:problem"] - ms["sampler:problem"] + ms_abc)
        / (n * a * b),
    )
    return {
        "model": s2_a,
        "sampler": s2_b,
        "problem": s2_c,
        "model:sampler": s2_ab,
        "model:problem": s2_ac,
        "sampler:problem": s2_bc,
        "model:sampler:problem": s2_abc,
        "resampling": s2_e,
    }


def decompose_items(
    correct,
    model_ids: list[str],
    sampler_ids: list[str],
    problem_ids: list[str],
) -> ThreeWay:
    """Three-way decomposition on per-problem binary correctness.

    Problem enters as a crossed random factor rather than being averaged away.
    Expect it to dominate — some problems are simply hard — which is exactly why
    it must be in the model: left out, problem variance would land in the residual
    and every other share would be deflated.

    Caveat to carry into the paper: binary outcomes have mean-dependent variance
    (Bernoulli), so these components are less defensible than the accuracy-level
    ones. This is the supplement that shows where variance lives, not the headline.
    """
    y = np.asarray(correct, dtype=np.float64).ravel()
    a_code, models = _codes(model_ids)
    b_code, samplers = _codes(sampler_ids)
    c_code, problems = _codes(problem_ids)
    a, b, c = len(models), len(samplers), len(problems)
    if min(a, b, c) < 2:
        raise ValueError("need >= 2 levels of model, sampler and problem")

    G = _grid(y, [a_code, b_code, c_code], (a, b, c))
    n = G.shape[3]

    ss, df = _three_way_ss(G)
    ms = {k: (ss[k] / df[k] if df[k] > 0 else 0.0) for k in SOURCES3}
    comps = _three_way_components(ms, a, b, c, n)
    table = _rows(SOURCES3, df, ss, ms, comps, ss["total"])

    return ThreeWay(
        n_models=a,
        n_samplers=b,
        n_problems=c,
        n_reps=n,
        table=table,
        var_share={r["source"]: r["var_share"] for r in table},
        grand_mean=float(G.mean()),
    )


# --------------------------------------------------------------------------
# third response: continuous per-problem solve rate
# --------------------------------------------------------------------------
#
# Solve rate is the fraction of replicates that solved a given problem under a
# given (model, sampler): one continuous number in [0, 1] per cell of a
# model x sampler x problem grid.
#
# It is not a convenience view of the binary analysis. Binary outcomes have
# mean-dependent variance — a Bernoulli's variance is p(1-p), so it is pinned to
# zero at both ends and maximal at 0.5 — which means a component estimated from
# 0/1 data partly reflects where the cell means happen to sit rather than how much
# the factor moves them. Averaging over replicates removes that coupling and gives
# a response an ANOVA's constant-variance assumption is not actively fighting.
#
# The cost is that aggregating spends the replicate dimension: there is exactly
# one solve rate per (model, sampler, problem), so the design has no replication
# and the three-way interaction becomes the error stratum. Nothing is lost that
# the binary analysis does not already report — but the residual here is not the
# same quantity as `resampling` there, and conflating the two would overstate the
# case. It is named `residual` for that reason.
#
# One correction is applied and reported. A solve rate measured from R replicates
# carries binomial sampling noise of p(1-p)/R even if the underlying rate is
# fixed, and that noise lands entirely in the residual. Left uncorrected it
# inflates the error stratum, which deflates every variance share — including the
# sampler share this paper is arguing about. So it is estimated and subtracted.


@dataclass
class SolveRate:
    n_models: int
    n_samplers: int
    n_problems: int
    n_replicates: int
    table: list[dict] = field(default_factory=list)
    var_share: dict = field(default_factory=dict)
    sampler_to_model: float = float("nan")
    grand_mean: float = float("nan")
    binomial_noise: float = float("nan")     # estimated p(1-p)/R, in rate^2 units
    residual_raw: float = float("nan")       # before subtracting that noise
    residual_corrected: float = float("nan")

    def get(self, source: str) -> dict:
        for row in self.table:
            if row["source"] == source:
                return row
        raise KeyError(source)

    def to_dict(self) -> dict:
        return {
            "n_models": self.n_models,
            "n_samplers": self.n_samplers,
            "n_problems": self.n_problems,
            "n_replicates": self.n_replicates,
            "table": self.table,
            "var_share": self.var_share,
            "sampler_to_model": self.sampler_to_model,
            "grand_mean": self.grand_mean,
            "binomial_noise": self.binomial_noise,
            "residual_raw": self.residual_raw,
            "residual_corrected": self.residual_corrected,
        }


SOURCES_RATE = [
    "model",
    "sampler",
    "problem",
    "model:sampler",
    "model:problem",
    "sampler:problem",
    "residual",
]


def solve_rates(
    correct,
    model_ids: list[str],
    sampler_ids: list[str],
    problem_ids: list[str],
) -> tuple[np.ndarray, list[str], list[str], list[str], int]:
    """Aggregate binary outcomes into per-problem solve rates.

    Returns (rates, model_ids, sampler_ids, problem_ids, n_replicates) with one
    row per (model, sampler, problem). Balance is enforced here rather than
    assumed: an unbalanced grid would give some cells a solve rate measured from
    more replicates than others, so their binomial noise would differ and the
    single correction applied downstream would be wrong for both.
    """
    y = np.asarray(correct, dtype=np.float64).ravel()
    a_code, models = _codes(model_ids)
    b_code, samplers = _codes(sampler_ids)
    c_code, problems = _codes(problem_ids)
    a, b, c = len(models), len(samplers), len(problems)

    G = _grid(y, [a_code, b_code, c_code], (a, b, c))   # (a, b, c, n)
    n = G.shape[3]
    rates = G.mean(axis=3)                              # (a, b, c)

    ml, sl, pl = [], [], []
    for i in range(a):
        for j in range(b):
            for k in range(c):
                ml.append(models[i])
                sl.append(samplers[j])
                pl.append(problems[k])
    return rates.reshape(-1), ml, sl, pl, n


def _no_rep_components(ms: dict, a: int, b: int, c: int) -> dict:
    """EMS-corrected components for a three-factor random model with n = 1.

        E[MS_A]   = s2_r + c*s2_AB + b*s2_AC + b*c*s2_A
        E[MS_AB]  = s2_r + c*s2_AB
        E[MS_ABC] = s2_r
        (and the symmetric cases)

    With one observation per cell the three-way interaction is inseparable from
    any true residual, so `s2_r` is both — which is why the source is called
    `residual` and not `resampling`.
    """
    s2_r = ms["residual"]
    s2_ab = max(0.0, (ms["model:sampler"] - s2_r) / c)
    s2_ac = max(0.0, (ms["model:problem"] - s2_r) / b)
    s2_bc = max(0.0, (ms["sampler:problem"] - s2_r) / a)
    s2_a = max(
        0.0,
        (ms["model"] - ms["model:sampler"] - ms["model:problem"] + s2_r) / (b * c),
    )
    s2_b = max(
        0.0,
        (ms["sampler"] - ms["model:sampler"] - ms["sampler:problem"] + s2_r) / (a * c),
    )
    s2_c = max(
        0.0,
        (ms["problem"] - ms["model:problem"] - ms["sampler:problem"] + s2_r) / (a * b),
    )
    return {
        "model": s2_a,
        "sampler": s2_b,
        "problem": s2_c,
        "model:sampler": s2_ab,
        "model:problem": s2_ac,
        "sampler:problem": s2_bc,
        "residual": s2_r,
    }


def decompose_solve_rate(
    rate,
    model_ids: list[str],
    sampler_ids: list[str],
    problem_ids: list[str],
    n_replicates: int | None = None,
    correct_binomial_noise: bool = True,
) -> SolveRate:
    """Three-way decomposition of continuous per-problem solve rate.

    `rate` is one value in [0, 1] per (model, sampler, problem). Build it with
    `solve_rates()` from raw binary outcomes, or pass it directly.

    `n_replicates` enables the binomial-noise correction and should be given
    whenever the rates were measured rather than simulated. Without it the
    residual carries the noise of having run R replicates instead of infinitely
    many, and every share is deflated by a factor that depends on R — which would
    make the headline ratio a function of how many times the sweep was rerun.
    """
    y = np.asarray(rate, dtype=np.float64).ravel()
    if y.size and (y.min() < -1e-9 or y.max() > 1 + 1e-9):
        raise ValueError(
            f"solve rates must lie in [0, 1]; got range "
            f"[{y.min():.4f}, {y.max():.4f}]. Passing raw counts instead of rates "
            "produces a decomposition that runs and is meaningless."
        )

    a_code, models = _codes(model_ids)
    b_code, samplers = _codes(sampler_ids)
    c_code, problems = _codes(problem_ids)
    a, b, c = len(models), len(samplers), len(problems)
    if min(a, b, c) < 2:
        raise ValueError("need >= 2 levels of model, sampler and problem")

    n_cells = a * b * c
    if y.size != n_cells:
        raise ValueError(
            f"expected exactly one solve rate per cell ({n_cells} rows for "
            f"{a} models x {b} samplers x {c} problems), got {y.size}"
        )

    # One observation per cell: build the (a, b, c, 1) array directly, since the
    # replicated path in `_grid` deliberately refuses n < 2.
    G = np.empty((a, b, c, 1), dtype=np.float64)
    seen = np.zeros((a, b, c), dtype=bool)
    for v, i, j, k in zip(y, a_code, b_code, c_code):
        if seen[i, j, k]:
            raise ValueError(
                f"duplicate solve rate for cell "
                f"({models[i]}, {samplers[j]}, {problems[k]})"
            )
        G[i, j, k, 0] = v
        seen[i, j, k] = True
    if not seen.all():
        missing = int((~seen).sum())
        raise ValueError(f"incomplete grid: {missing} of {n_cells} cells have no rate")

    ss, df = _three_way_ss(G)
    ss["residual"] = ss.pop("model:sampler:problem")
    df["residual"] = df.pop("model:sampler:problem")
    ss.pop("resampling", None)
    df.pop("resampling", None)

    ms = {k: (ss[k] / df[k] if df[k] > 0 else 0.0) for k in SOURCES_RATE}
    comps = _no_rep_components(ms, a, b, c)

    residual_raw = comps["residual"]
    noise = float("nan")
    if correct_binomial_noise and n_replicates and n_replicates > 1:
        # Unbiased estimate of Var(rate) contributed by finite sampling:
        # E[p_hat(1-p_hat)] = ((R-1)/R) p(1-p), so p_hat(1-p_hat)/(R-1) is an
        # unbiased estimate of p(1-p)/R, the sampling variance of each rate.
        p = G.reshape(-1)
        noise = float(np.mean(p * (1.0 - p)) / (n_replicates - 1))
        comps["residual"] = max(0.0, residual_raw - noise)

    table = _rows(SOURCES_RATE, df, ss, ms, comps, ss["total"])
    comp_total = sum(comps.values()) or 1.0
    ratio = (
        comps["sampler"] / comps["model"]
        if comps["model"] > 0
        else (float("inf") if comps["sampler"] > 0 else float("nan"))
    )

    return SolveRate(
        n_models=a,
        n_samplers=b,
        n_problems=c,
        n_replicates=int(n_replicates or 1),
        table=table,
        var_share={r["source"]: r["var_component"] / comp_total for r in table},
        sampler_to_model=ratio,
        grand_mean=float(G.mean()),
        binomial_noise=noise,
        residual_raw=residual_raw,
        residual_corrected=comps["residual"],
    )

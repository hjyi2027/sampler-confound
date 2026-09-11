"""Is a decoding parameter honoured? A two-sample test, not a heuristic.

The earlier probe set a parameter to an extreme value, sampled eight completions,
and called it honoured if fewer than three were distinct. That is a yes/no with an
arbitrary cutoff, no null, and no way to tell a real effect from eight lucky
draws. It also cannot express "we could not tell", which at n=8 is the honest
answer most of the time.

The question restated properly: hold everything fixed, vary ONE parameter between
two settings far apart in its range, draw N completions at each, and ask whether
the two output distributions are distinguishable.

**The null is the failure mode.** If the provider ignores the parameter, both
settings are the same configuration, so the 2N completions are iid from one
distribution and the two labelled samples are exchangeable. That makes a
permutation test exact: no asymptotics, no normality, valid at any N, and the
null distribution is generated from the observed data rather than assumed.

**Primary statistic: entropy drop, one-sided.** Every parameter here has a
direction. `top_p`, `top_k` and `min_p` truncate the sampling distribution and
`temperature` sharpens it, so if honoured, the tight setting must produce LESS
diverse output than the open one:

    dH = H(open) - H(tight)     one-sided, expected > 0 if honoured

where H is the empirical Shannon entropy over completions. A one-sided test that
matches the mechanism is more powerful than a two-sided distance, and dH does not
saturate — measured on a model that honours top_p, dH was +4.19 against a null
mean of +0.012.

**Secondary: total variation distance**, TV = 1/2 sum_w |p_A(w) - p_B(w)|,
two-sided. Reported alongside because it detects a distributional change in any
direction, including one that alters the distribution without narrowing it, which
dH would miss. TV has a failure mode dH does not: when completions are nearly all
unique both arms are near-disjoint and TV approaches 1 under the null as well as
the alternative, leaving no headroom. Both statistics are reported so the reader
can see when one is uninformative.

Both are biased at finite N — two samples from one distribution do not coincide —
and that bias is exactly what the permutation null absorbs, which is why observed
values are reported against the null's own mean rather than against zero.

What this does and does not measure. It answers "does this parameter change the
output distribution at all", which is the honoured/ignored question. It is
deliberately run on a high-entropy prompt, because power to detect an effect is
highest where the distribution has entropy to lose. It is NOT a measure of how
much the parameter moves task accuracy: nemotron-lightning returned 2/8 distinct
completions on a one-word prompt and still showed an 8-point accuracy range
across sampler configs on real problems. Distinguishable is not the same as
consequential, and this module only claims the first.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Distinguishability:
    """Result of one (model, parameter) two-sample test."""

    model: str
    parameter: str
    setting_a: dict
    setting_b: dict
    n_tight: int = 0
    n_open: int = 0
    support_tight: int = 0
    support_open: int = 0
    entropy_tight: float = float("nan")
    entropy_open: float = float("nan")
    # primary: one-sided entropy drop
    dh: float = float("nan")
    dh_null_mean: float = float("nan")
    dh_p: float = float("nan")
    # secondary: two-sided total variation
    tv: float = float("nan")
    tv_null_mean: float = float("nan")
    tv_p: float = float("nan")
    # The primary p-value, mirroring dh_p. Declared rather than assigned
    # dynamically: holm_adjust reads it, and an undeclared attribute works when
    # assess_parameter builds the object and fails when one is rebuilt from JSON.
    p_value: float = float("nan")
    n_permutations: int = 0
    status: str = "ok"               # ok | rejected | insufficient
    detail: str = ""

    @property
    def excess(self) -> float:
        """How far the observed entropy drop sits above exchangeability alone."""
        return self.dh - self.dh_null_mean

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["excess"] = self.excess
        return d


def entropy(xs: list[str]) -> float:
    """Empirical Shannon entropy, in bits, of a sample of completions."""
    n = len(xs)
    if not n:
        return float("nan")
    c = Counter(xs)
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def tv_distance(a: list[str], b: list[str]) -> float:
    """Total variation distance between two empirical distributions."""
    ca, cb = Counter(a), Counter(b)
    na, nb = len(a), len(b)
    if not na or not nb:
        return float("nan")
    return 0.5 * sum(
        abs(ca.get(w, 0) / na - cb.get(w, 0) / nb) for w in set(ca) | set(cb)
    )


def permutation_test(
    a: list[str],
    b: list[str],
    statistic=None,
    one_sided: bool = False,
    n_permutations: int = 10_000,
    random_state: int = 0,
) -> tuple[float, float, float, float]:
    """Exact-in-the-limit permutation test of exchangeability.

    `statistic(a, b)` defaults to total variation distance. Returns
    (observed, null mean, null 95th percentile, p-value).

    The p-value uses the add-one correction, (1 + #{null >= observed}) / (1 + B).
    Without it a statistic beyond every permutation reports p = 0, which claims
    more than B permutations can support and is wrong at any B.
    """
    statistic = statistic or tv_distance
    obs = statistic(a, b)
    if not np.isfinite(obs):
        return obs, float("nan"), float("nan"), float("nan")

    pooled = np.array(a + b, dtype=object)
    na = len(a)
    rng = np.random.default_rng(random_state)
    null = np.empty(n_permutations)
    for i in range(n_permutations):
        rng.shuffle(pooled)
        null[i] = statistic(list(pooled[:na]), list(pooled[na:]))

    hits = np.sum(null >= obs) if one_sided else np.sum(np.abs(null) >= abs(obs))
    p = (1 + int(hits)) / (1 + n_permutations)
    return obs, float(null.mean()), float(np.percentile(null, 95)), p


def assess_parameter(
    model: str,
    parameter: str,
    tight_setting: dict,
    open_setting: dict,
    completions_tight: list[str],
    completions_open: list[str],
    n_permutations: int = 10_000,
    random_state: int = 0,
) -> Distinguishability:
    """One (model, parameter) test from two already-collected samples.

    Arms are named by direction, not by label: `tight` is the setting that should
    NARROW the output distribution if the parameter is honoured. Getting these
    the wrong way round would invert a one-sided test into one with no power,
    which is why they are named rather than positional a/b.

    Not named test_* on purpose: pytest collects anything so named, and a library
    function masquerading as a test case is a confusing failure.
    """
    res = Distinguishability(
        model=model, parameter=parameter,
        setting_a=tight_setting, setting_b=open_setting,
        n_tight=len(completions_tight), n_open=len(completions_open),
        n_permutations=n_permutations,
    )
    if res.n_tight < 5 or res.n_open < 5:
        res.status = "insufficient"
        res.detail = f"n={res.n_tight}/{res.n_open}; need >= 5 per arm"
        return res

    res.support_tight = len(set(completions_tight))
    res.support_open = len(set(completions_open))
    res.entropy_tight = entropy(completions_tight)
    res.entropy_open = entropy(completions_open)

    if res.support_tight == 1 and res.support_open == 1:
        # Both arms collapsed to one string: exchangeable by construction, so the
        # test has no power here at all. Reporting p = 1.0 would read as evidence
        # of no effect when it is evidence of nothing — the prompt carried no
        # entropy for this model.
        res.status = "insufficient"
        res.detail = (
            "both arms are a single constant completion; the prompt has no "
            "entropy for this model and the test cannot discriminate"
        )
        res.dh = res.tv = 0.0
        return res

    res.dh, res.dh_null_mean, _, res.dh_p = permutation_test(
        completions_open, completions_tight,
        statistic=lambda a, b: entropy(a) - entropy(b),
        one_sided=True, n_permutations=n_permutations, random_state=random_state,
    )
    res.tv, res.tv_null_mean, res.null_p95, res.tv_p = permutation_test(
        completions_tight, completions_open,
        statistic=tv_distance, one_sided=False,
        n_permutations=n_permutations, random_state=random_state,
    )
    res.p_value = res.dh_p          # primary
    return res


def holm_adjust(results: list[Distinguishability]) -> dict[tuple[str, str], float]:
    """Holm-Bonferroni adjusted p-values across the whole (model, parameter) grid.

    Forty tests at alpha = 0.05 expect two false positives by chance, and the
    claim being made is per-cell ("this model ignores this parameter"), so the
    family-wise rate is the one that matters. Holm is uniformly more powerful
    than Bonferroni and needs no independence assumption, which these tests do
    not have — they share models and prompts.
    """
    testable = [r for r in results if r.status == "ok" and np.isfinite(r.p_value)]
    order = sorted(testable, key=lambda r: r.p_value)
    m = len(order)
    adjusted: dict[tuple[str, str], float] = {}
    running = 0.0
    for i, r in enumerate(order):
        val = min(1.0, (m - i) * r.p_value)
        running = max(running, val)          # enforce monotonicity
        adjusted[(r.model, r.parameter)] = running
    return adjusted


# --------------------------------------------------------------------------
# positive control
# --------------------------------------------------------------------------
# A null result means nothing on its own. "Not distinguishable" is produced
# identically by a parameter the provider ignores and by a probe that has no
# power on this model, and the test cannot tell them apart from the inside. The
# only way to separate them is a positive control: on the SAME model and the SAME
# prompt, a parameter known to work must show a large effect. If it does not, the
# probe has no power here and every other null on this model is uninterpretable.
#
# temperature is the control. It is the one parameter no serving stack drops, and
# 0 vs 1.5 is the widest contrast available, so its entropy drop is the ceiling
# on what any test can show on this model with this prompt.

POSITIVE_CONTROL = "temperature"
CONTROL_MIN_FRACTION = 0.5   # control must remove at least half the open-arm entropy


@dataclass
class ModelPower:
    """What the positive control says about a model's testability."""

    model: str
    control_dh: float = float("nan")
    control_h_open: float = float("nan")
    control_h_tight: float = float("nan")      # residual entropy at temperature 0
    control_support_tight: int = 0
    control_n_tight: int = 0
    control_p: float = float("nan")
    fraction_removed: float = float("nan")
    powered: bool = False
    detail: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def positive_control(results: list[Distinguishability]) -> dict[str, ModelPower]:
    """Per model: did the control show a large effect, so nulls can be read?"""
    out: dict[str, ModelPower] = {}
    for r in results:
        if r.parameter != POSITIVE_CONTROL:
            continue
        mp = ModelPower(model=r.model)
        if r.status != "ok":
            mp.detail = f"control not run: {r.status}"
            out[r.model] = mp
            continue
        mp.control_dh = r.dh
        mp.control_h_open = r.entropy_open
        mp.control_h_tight = r.entropy_tight
        mp.control_support_tight = r.support_tight
        mp.control_n_tight = r.n_tight
        mp.control_p = r.dh_p
        mp.fraction_removed = r.dh / r.entropy_open if r.entropy_open > 0 else float("nan")
        mp.powered = bool(mp.fraction_removed >= CONTROL_MIN_FRACTION)
        if not mp.powered:
            mp.detail = (
                f"temperature 0 vs 1.5 removed only {mp.fraction_removed:.0%} of "
                f"{mp.control_h_open:.2f} bits; {mp.control_h_tight:.2f} bits "
                f"remain at temperature 0 ({mp.control_support_tight}/"
                f"{mp.control_n_tight} distinct). Something other than the sampler "
                "generates that variation, and no truncation parameter can remove "
                "it — a null on this model is uninterpretable."
            )
        out[r.model] = mp
    return out


def interpret(r: Distinguishability, power: dict[str, ModelPower],
              p_adjusted: float) -> str:
    """The verdict a cell actually supports, given its model's positive control.

    Three outcomes, and the difference between the last two is the entire point:

      distinguishable   the effect is there
      no effect seen    control passed, so the probe HAD power here and still
                        saw nothing — this is evidence about the parameter
      underpowered      control failed, so the probe could not have shown a
                        large effect on this model — this is evidence of nothing
    """
    if r.status != "ok":
        return r.status
    if p_adjusted < 0.05:
        return "distinguishable"
    mp = power.get(r.model)
    if mp is None or not mp.powered:
        return "underpowered"
    return "no effect seen"

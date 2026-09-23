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
    prompt: str = ""                 # prompt id; a verdict is per (model, param, prompt)
    provider: str = "fireworks"      # which deployment answered; the model name alone is ambiguous
    # When the provider answered these calls (ISO UTC, from the cache entries).
    # Provider behaviour changes; the date is part of the result.
    collected_from: str = ""
    collected_to: str = ""
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
    # The primary p-value: dh_p for a truncation parameter, whose direction is
    # known (tight arm has less entropy), tv_p for a penalty, whose direction is
    # not. Declared rather than assigned dynamically: holm_adjust reads it, and
    # an undeclared attribute works when assess_parameter builds the object and
    # fails when one is rebuilt from JSON.
    primary: str = "dh"              # "dh" | "tv"
    p_value: float = float("nan")
    n_permutations: int = 0
    status: str = "ok"               # ok | rejected | unsupported | insufficient | transport
    detail: str = ""
    # Retained so a negative control can be run on the data after the fact.
    completions_tight: list[str] = field(default_factory=list)
    completions_open: list[str] = field(default_factory=list)
    # Empty completions per arm. They are kept in the sample as a sentinel
    # token; these counts make the censoring visible instead of silent.
    empty_tight: int = 0
    empty_open: int = 0

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


def entropy_drop(a: list[str], b: list[str]) -> float:
    """H(a) - H(b): positive when b is the narrower sample."""
    return entropy(a) - entropy(b)


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

    # Encode completions as small ints once. The first version shuffled an
    # object array of strings and rebuilt two Counters of strings per
    # permutation — 10,000 times per cell, 16 cells per model-prompt — and the
    # multi-prompt grid was killed for memory three hours in. Integer codes make
    # each permutation a bincount over a fixed-size array.
    vocab = {w: i for i, w in enumerate(dict.fromkeys(a + b))}
    codes = np.fromiter((vocab[w] for w in a + b), dtype=np.int32, count=len(a) + len(b))
    K = len(vocab)
    na, nb = len(a), len(b)
    rng = np.random.default_rng(random_state)
    null = np.empty(n_permutations)

    def _H(counts, n):
        p = counts[counts > 0] / n
        return float(-(p * np.log2(p)).sum())

    if statistic is tv_distance:
        def fast(ca, cb):
            return 0.5 * np.abs(ca / na - cb / nb).sum()
    elif statistic is entropy_drop:
        def fast(ca, cb):
            return _H(ca, na) - _H(cb, nb)
    else:
        # A caller-supplied statistic still receives lists; slow, but not on
        # either path this module actually uses.
        inv = list(vocab)

        def fast(ca, cb):
            la = [w for w, c in zip(inv, ca) for _ in range(int(c))]
            lb = [w for w, c in zip(inv, cb) for _ in range(int(c))]
            return statistic(la, lb)

    for i in range(n_permutations):
        rng.shuffle(codes)
        ca = np.bincount(codes[:na], minlength=K)
        cb = np.bincount(codes[na:], minlength=K)
        null[i] = fast(ca, cb)

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
    primary: str = "dh",
) -> Distinguishability:
    """One (model, parameter) test from two already-collected samples.

    `primary` picks which p-value is THE p-value for this cell. A truncation
    parameter (top_p, top_k, min_p, typical_p, mirostat) narrows the
    distribution when honoured, so the one-sided entropy drop is the sensitive
    test. A penalty (repetition, frequency, presence) reshapes it in no fixed
    direction, so the two-sided total variation is the honest one; using dH
    there would call a real effect "no effect" whenever it happened to raise
    entropy.

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
    if primary not in ("dh", "tv"):
        raise ValueError(f"primary must be 'dh' or 'tv', not {primary!r}")
    res.primary = primary
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
        return res
    if (res.support_tight >= res.n_tight - 1 and res.support_open >= res.n_open - 1):
        # The opposite degeneracy, and the one that bites penalty contrasts:
        # both arms all-unique. Entropy is log2(n) on both sides, dH is 0 and
        # TV is 1 under every permutation, so p = 1 by construction. The
        # negative control excludes such pairs for exactly this reason; the
        # main test must too, or a penalty on a high-entropy prompt reads as
        # "no effect seen" behind a passed temperature control.
        res.status = "insufficient"
        res.detail = (
            f"both arms all-unique ({res.support_tight}/{res.n_tight}, "
            f"{res.support_open}/{res.n_open}); exact-match statistics have no "
            "range here and cannot discriminate"
        )
        res.dh = res.tv = 0.0
        return res

    res.dh, res.dh_null_mean, _, res.dh_p = permutation_test(
        completions_open, completions_tight,
        statistic=entropy_drop,
        one_sided=True, n_permutations=n_permutations, random_state=random_state,
    )
    res.tv, res.tv_null_mean, res.null_p95, res.tv_p = permutation_test(
        completions_tight, completions_open,
        statistic=tv_distance, one_sided=False,
        n_permutations=n_permutations, random_state=random_state,
    )
    res.p_value = res.dh_p if primary == "dh" else res.tv_p
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
# The positive control asks: at this n, on this (model, prompt), could the test
# have detected an HONOURED truncation parameter? The tight settings under test
# (top_k=1, top_p=0.01, min_p=0.9, typical_p=0.1) are greedy-equivalent when
# honoured, and every open arm is unrestricted sampling at temperature 1.0. So
# the largest effect an honoured parameter can produce is exactly the contrast
# temperature 0 versus temperature 1.0 — and both arms are already collected:
# the temperature cell's tight arm (T=0) and the top_p cell's open arm
# (T=1.0, top_p=1.0, i.e. no truncation).
#
# The control passes when the test has POWER to detect that contrast: the
# probability, estimated by bootstrapping the two arms, that the same test at
# the same n rejects at the same corrected level the parameter tests face.
# Powered means power >= CONTROL_POWER (0.8, the conventional value; stated,
# not derived). Mere detectability is not enough — a control that clears
# p < 0.05 by a hair has about 50% power, and the parameter tests face a Holm
# correction on top, so a null licensed by it would be a coin flip dressed as
# evidence. That was the first version of this fix, and it turned several
# truncation cells on low-headroom models from "undetermined" to "inert".
#
# "No effect seen" therefore means: an effect as large as a fully honoured
# (greedy-equivalent) setting would have been detected with >= 80% power at
# the corrected level, and none was. It does not rule out a partially
# honoured parameter with a smaller effect.
#
# The first version contrasted T=0 with T=1.5 and required half the open-arm
# entropy removed. Both choices were wrong for the same reason: the parameter
# arms sit at 1.0, not 1.5, so the control demonstrated power over a range the
# tests never span — and at 1.5 several reasoning models run to the token cap
# and return nothing, collapsing the open arm to a point and failing a control
# that says nothing about the arms actually tested. The 0.5 threshold had no
# derivation. Kept as `legacy_*` fields for comparison, never used for verdicts.

POSITIVE_CONTROL = "temperature"
# Contrasts whose OPEN arm is unrestricted sampling at temperature 1.0, in
# order of preference: mirostat's open arm is literally {temperature: 1.0};
# the others add a parameter at its no-op value (top_p=1.0, min_p=0.0,
# typical_p=1.0). Any one serves as the T=1.0 reference, so a report that
# holds only some contrasts can still compute its control.
CONTROL_REFERENCES = ("mirostat", "top_p", "min_p", "typical_p")
CONTROL_ALPHA = 0.05                 # family-wise level; the power analysis uses alpha / m
CONTROL_POWER = 0.8                  # powered = P(detect the full-strength effect) >= this
CONTROL_BOOT = 200                   # bootstrap draws for the power estimate
CONTROL_PERM = 800                   # permutations per draw (resolves p down to ~0.0012)
CONTROL_MIN_FRACTION = 0.5           # LEGACY rule; reported, not applied


@dataclass
class ModelPower:
    """What the positive control says about a model's testability on a prompt."""

    model: str
    contrast: str = "T=0 vs T=1.0"
    control_dh: float = float("nan")
    control_h_open: float = float("nan")       # entropy at T=1.0, unrestricted
    control_h_tight: float = float("nan")      # residual entropy at temperature 0
    control_support_tight: int = 0
    control_n_tight: int = 0
    control_p: float = float("nan")
    fraction_removed: float = float("nan")     # reported; not a gate
    control_alpha: float = float("nan")        # corrected level the power is computed at
    control_power: float = float("nan")        # P(reject at control_alpha) for the full effect
    powered: bool = False
    legacy_fraction_removed: float = float("nan")   # T=0 vs T=1.5
    legacy_powered: bool = False                    # >= 0.5 of the T=1.5 entropy
    detail: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def estimate_power(pairs: list[tuple[list[str], list[str]]], alpha: float,
                   boot: int = CONTROL_BOOT, n_permutations: int = CONTROL_PERM,
                   random_state: int = 0, primary: str = "dh") -> float:
    """P(the stratified dH test rejects at `alpha`) for data like `pairs`.

    Each (tight, open) pair is one stratum; the arms are resampled with
    replacement, the stratified test is run on the resample, and the rejection
    rate over `boot` draws is the power estimate. With one pair this is the
    per-prompt test's power; with several, the pooled test's.
    """
    pairs = [(list(t), list(o)) for t, o in pairs if t and o]
    if not pairs:
        return float("nan")
    rng = np.random.default_rng(random_state)
    hits = 0
    for _ in range(boot):
        obs, null = 0.0, np.zeros(n_permutations)
        for t, o in pairs:
            bt = [t[i] for i in rng.integers(0, len(t), len(t))]
            bo = [o[i] for i in rng.integers(0, len(o), len(o))]
            ob, nl = _perm_stats(bt, bo, primary, n_permutations, rng)
            obs += ob
            null += nl
        p = (1 + int(np.sum(null >= obs - 1e-12))) / (1 + n_permutations)
        hits += p < alpha
    return hits / boot


def positive_control(results: list[Distinguishability], n_permutations: int = 10_000,
                     random_state: int = 0, alpha: float = CONTROL_ALPHA) -> dict[str, ModelPower]:
    """Per model, on one prompt: could the test have seen an honoured parameter?

    `alpha` is the corrected level the parameter tests on this prompt face
    (the caller passes CONTROL_ALPHA / number of tests in the family)."""
    temp = {r.model: r for r in results if r.parameter == POSITIVE_CONTROL}
    ref: dict[str, Distinguishability] = {}
    for name in reversed(CONTROL_REFERENCES):         # earlier names win
        for r in results:
            if r.parameter == name and r.completions_open:
                ref[r.model] = r
    out: dict[str, ModelPower] = {}
    for model, t in temp.items():
        mp = ModelPower(model=model)
        if t.status == "ok" and t.entropy_open > 0:
            mp.legacy_fraction_removed = t.dh / t.entropy_open
            mp.legacy_powered = bool(mp.legacy_fraction_removed >= CONTROL_MIN_FRACTION)
        r = ref.get(model)
        t0 = t.completions_tight
        t1 = r.completions_open if r is not None else []
        if not t0 or not t1:
            mp.detail = "control not computable: T=0 or unrestricted T=1.0 arm missing"
            out[model] = mp
            continue
        c = assess_parameter(model, "control", {"temperature": 0.0}, {"temperature": 1.0},
                             t0, t1, n_permutations=n_permutations, random_state=random_state)
        mp.control_h_open, mp.control_h_tight = c.entropy_open, c.entropy_tight
        mp.control_support_tight, mp.control_n_tight = c.support_tight, c.n_tight
        if c.status != "ok":
            mp.detail = f"control {c.status}: {c.detail}"
            out[model] = mp
            continue
        mp.control_dh, mp.control_p = c.dh, c.dh_p
        mp.fraction_removed = c.dh / c.entropy_open if c.entropy_open > 0 else float("nan")
        mp.control_alpha = alpha
        mp.control_power = estimate_power([(t0, t1)], alpha, random_state=random_state)
        mp.powered = bool(mp.control_power >= CONTROL_POWER)
        if not mp.powered:
            mp.detail = (
                f"power {mp.control_power:.0%} (< {CONTROL_POWER:.0%}) to detect temperature 0 "
                f"vs 1.0 at alpha={alpha:.4f} (dH={c.dh:+.2f}; {c.support_tight}/{c.n_tight} "
                "distinct at T=0). Even a fully honoured truncation parameter could "
                "have been missed, so a null on this prompt means nothing."
            )
        out[model] = mp
    return out


# --------------------------------------------------------------------------
# stratified permutation test across prompts
# --------------------------------------------------------------------------
# The per-prompt verdicts used to be combined by majority vote, which has no
# stated error rate. The exact replacement: permute arm labels WITHIN each
# prompt (the null is exchangeability on every prompt at once), sum the
# per-prompt statistic, and compare the observed sum with that null. One
# p-value per (model, parameter), valid at any number of prompts, and a prompt
# with little signal contributes little rather than casting a full vote.


def _perm_stats(tight: list[str], open_: list[str], primary: str, B: int,
                rng: np.random.Generator) -> tuple[float, np.ndarray]:
    """Observed statistic and B within-stratum permutation draws."""
    vocab = {w: i for i, w in enumerate(dict.fromkeys(tight + open_))}
    K = len(vocab)
    codes = np.fromiter((vocab[w] for w in tight + open_), dtype=np.int64,
                        count=len(tight) + len(open_))
    nt = len(tight)
    n = len(codes)
    perms = rng.permuted(np.tile(codes, (B + 1, 1)), axis=1)
    perms[0] = codes                                   # row 0 is the observed labelling
    offs = (np.arange(B + 1) * K)[:, None]
    ct = np.bincount((perms[:, :nt] + offs).ravel(), minlength=(B + 1) * K).reshape(B + 1, K)
    co = np.bincount((perms[:, nt:] + offs).ravel(), minlength=(B + 1) * K).reshape(B + 1, K)
    no = n - nt
    if primary == "tv":
        stat = 0.5 * np.abs(ct / nt - co / no).sum(axis=1)
    else:
        def H(c, m):
            p = c / m
            with np.errstate(divide="ignore", invalid="ignore"):
                return -np.where(p > 0, p * np.log2(p), 0.0).sum(axis=1)
        stat = H(co, no) - H(ct, nt)                   # dH = H(open) - H(tight)
    return float(stat[0]), stat[1:]


def stratified_test(cells: list[Distinguishability], primary: str = "dh",
                    n_permutations: int = 10_000, random_state: int = 0) -> tuple[float, float, int]:
    """(observed sum, p, prompts used) over cells of one (model, parameter).

    Only cells with status "ok" enter: an all-unique or constant pair has no
    range under permutation and contributes nothing but noise to the sum.
    """
    usable = [c for c in cells if c.status == "ok"
              and c.completions_tight and c.completions_open]
    if not usable:
        return float("nan"), float("nan"), 0
    rng = np.random.default_rng(random_state)
    obs, null = 0.0, np.zeros(n_permutations)
    for c in usable:
        o, d = _perm_stats(c.completions_tight, c.completions_open, primary, n_permutations, rng)
        obs += o
        null += d
    p = (1 + int(np.sum(null >= obs - 1e-12))) / (1 + n_permutations)
    return obs, p, len(usable)


def interpret(r: Distinguishability, power: dict[str, ModelPower],
              p_adjusted: float, forced: bool = False) -> str:
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
    if forced:
        # A prompt built so the parameter MUST act if honoured — forced
        # repetition for a penalty. The temperature control is beside the
        # point there: the prompt is its own control, and a null means the
        # provider is not applying the parameter.
        return "no effect seen"
    mp = power.get(r.model)
    if mp is None or not mp.powered:
        return "underpowered"
    return "no effect seen"


# --------------------------------------------------------------------------
# aggregating across prompts
# --------------------------------------------------------------------------
# One prompt is one sample of prompt space. It can be degenerate for a particular
# model — every completion "The sun set slowly..." — and a one-word noun prompt
# with a strong mode was already shown not to predict task-level sensitivity.
# So each (model, parameter) is tested on several prompts with genuine entropy,
# every prompt is reported on its own, and the verdict is a majority over the
# prompts that PASSED THEIR POSITIVE CONTROL. A single degenerate prompt then
# cannot drive a verdict in either direction: it fails the control and drops out
# of the denominator rather than casting a vote.

MIN_POWERED_PROMPTS = 2


@dataclass
class Aggregate:
    model: str
    parameter: str
    n_prompts: int = 0
    # Two different things, kept apart because conflating them was a defect:
    #   n_control_passed  prompts whose POSITIVE CONTROL passed — the probe is
    #                     known to have power on that (model, prompt)
    #   n_verdict         prompts that produced a verdict at all, i.e. a
    #                     detected effect (which needs no control to be
    #                     believed) or a null with a passed control
    # The majority below is over n_verdict; a reader judging how much to trust
    # a null needs n_control_passed, and the two can differ sharply — a model
    # whose control fails on three prompts of four can still show an effect on
    # all four.
    n_control_passed: int = -1       # -1 = not supplied by the caller
    n_verdict: int = 0
    n_powered: int = 0               # DEPRECATED alias of n_verdict; kept so
                                     # reports written before 2026-09-23 parse
    n_distinguishable: int = 0       # among prompts with a verdict
    n_no_effect: int = 0             # among prompts with a verdict
    per_prompt: dict = field(default_factory=dict)   # prompt_id -> verdict
    verdict: str = "underpowered"
    # For a penalty: the verdict on the forced-repetition prompt, which is the
    # test of whether the provider APPLIES the parameter. `verdict` above is then
    # whether it matters on free text. Both are reported; they answer different
    # questions and can disagree — honoured, and inert on a sentence.
    forced: dict = field(default_factory=dict)       # prompt_id -> verdict
    verdict_forced: str = ""
    # The primary aggregate since 2026-09-23: a stratified permutation test
    # over prompts (stratified_test). `verdict` carries its result;
    # `verdict_majority` keeps the old majority vote so the two can be compared.
    stratified_stat: float = float("nan")
    stratified_p: float = float("nan")
    stratified_p_holm: float = float("nan")
    stratified_prompts: int = 0
    stratified_control_p: float = float("nan")
    stratified_control_power: float = float("nan")
    verdict_majority: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def aggregate(per_prompt_verdicts: dict[str, str], model: str, parameter: str,
              control_passed: dict[str, bool] | None = None) -> Aggregate:
    """Majority over powered prompts.

    per_prompt_verdicts: {prompt_id: verdict} where verdict is one of
    "distinguishable" | "no effect seen" | "underpowered" | other status.

      distinguishable   on a majority of powered prompts
      no effect seen    powered on >= MIN_POWERED_PROMPTS prompts, a majority
                        of them null — the probe had power on several prompts
                        and saw nothing on most
      mixed             powered prompts split with no majority; reported as
                        such rather than forced
      underpowered      fewer than MIN_POWERED_PROMPTS prompts had power
    """
    agg = Aggregate(model=model, parameter=parameter, per_prompt=dict(per_prompt_verdicts))
    agg.n_prompts = len(per_prompt_verdicts)
    powered = [v for v in per_prompt_verdicts.values()
               if v in ("distinguishable", "no effect seen")]
    agg.n_verdict = agg.n_powered = len(powered)
    if control_passed is not None:
        agg.n_control_passed = sum(bool(control_passed.get(pid))
                                   for pid in per_prompt_verdicts)
    agg.n_distinguishable = sum(v == "distinguishable" for v in powered)
    agg.n_no_effect = sum(v == "no effect seen" for v in powered)
    statuses = set(per_prompt_verdicts.values())
    if agg.n_verdict < MIN_POWERED_PROMPTS and len(statuses) == 1 and statuses <= {
            "rejected", "unsupported", "transport"}:
        # Every prompt failed the same way BEFORE any statistics: the provider
        # refused the parameter, the adapter could not send it, or the request
        # never completed. That is the verdict, not "underpowered".
        agg.verdict = statuses.pop()
    elif agg.n_verdict < MIN_POWERED_PROMPTS:
        agg.verdict = "underpowered"
    elif agg.n_distinguishable * 2 > agg.n_verdict:
        agg.verdict = "distinguishable"
    elif agg.n_no_effect * 2 > agg.n_verdict:
        agg.verdict = "no effect seen"
    else:
        agg.verdict = "mixed"
    return agg

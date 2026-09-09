"""Calibration of the honoured/ignored test.

This replaces a heuristic ("fewer than 3 distinct of 8 => honoured") that had an
arbitrary cutoff and no null. A replacement is only better if it is calibrated,
so these check size under the null and power under known alternatives before the
test is trusted to make claims about a provider.
"""

from __future__ import annotations

import numpy as np
import pytest

from samplerconfound.distinguish import (
    Distinguishability,
    holm_adjust,
    permutation_test,
    assess_parameter,
    tv_distance,
)


def _draw(rng, probs: dict[str, float], n: int) -> list[str]:
    words = list(probs)
    p = np.array([probs[w] for w in words], dtype=float)
    return list(rng.choice(words, size=n, p=p / p.sum()))


# --------------------------------------------------------------------------
# the statistic
# --------------------------------------------------------------------------
def test_tv_is_zero_for_identical_samples():
    assert tv_distance(["a", "b"], ["a", "b"]) == 0.0


def test_tv_is_one_for_disjoint_supports():
    assert tv_distance(["a"] * 10, ["b"] * 10) == pytest.approx(1.0)


def test_tv_matches_a_hand_computation():
    # A: 3/4 a, 1/4 b.  B: 1/4 a, 3/4 b.  TV = 1/2(|.75-.25| + |.25-.75|) = 0.5
    a = ["a", "a", "a", "b"]
    b = ["a", "b", "b", "b"]
    assert tv_distance(a, b) == pytest.approx(0.5)


def test_tv_is_symmetric_and_bounded():
    rng = np.random.default_rng(0)
    for _ in range(20):
        a = _draw(rng, {"x": 0.5, "y": 0.3, "z": 0.2}, 30)
        b = _draw(rng, {"x": 0.2, "y": 0.3, "z": 0.5}, 30)
        t = tv_distance(a, b)
        assert 0.0 <= t <= 1.0
        assert t == pytest.approx(tv_distance(b, a))


# --------------------------------------------------------------------------
# size: false positives under the null the test is built around
# --------------------------------------------------------------------------
def test_size_is_near_nominal_when_the_parameter_is_ignored():
    """The null IS the failure mode: an ignored parameter means one distribution.

    A test that over-rejects here would report honoured parameters everywhere,
    which is the more dangerous direction — it would let a study proceed on cells
    that are secretly duplicates.
    """
    rng = np.random.default_rng(1)
    dist = {"apple": 0.3, "table": 0.25, "chair": 0.2, "tree": 0.15, "book": 0.1}
    rejects = 0
    trials = 200
    for t in range(trials):
        a = _draw(rng, dist, 40)
        b = _draw(rng, dist, 40)          # same distribution: parameter ignored
        _, _, _, p = permutation_test(a, b, n_permutations=400, random_state=t)
        rejects += p < 0.05
    rate = rejects / trials
    assert rate < 0.11, f"false positive rate {rate:.1%} at nominal 5%"


def test_p_values_are_roughly_uniform_under_the_null():
    rng = np.random.default_rng(2)
    dist = {"a": 0.4, "b": 0.35, "c": 0.25}
    ps = []
    for t in range(150):
        ps.append(permutation_test(_draw(rng, dist, 40), _draw(rng, dist, 40),
                                   n_permutations=300, random_state=t)[3])
    ps = np.array(ps)
    # A discrete statistic makes this lumpy; check the middle rather than tails.
    assert 0.35 < ps.mean() < 0.65, f"mean p = {ps.mean():.2f}, expected near 0.5"


def test_p_value_never_reports_zero():
    # (1 + #{null >= obs}) / (1 + B): a statistic beyond every permutation still
    # cannot claim more than B permutations support.
    _, _, _, p = permutation_test(["a"] * 30, ["b"] * 30, n_permutations=100)
    assert p > 0.0
    assert p == pytest.approx(1 / 101)


# --------------------------------------------------------------------------
# power: can it see a real effect?
# --------------------------------------------------------------------------
def test_power_against_a_large_effect():
    """A parameter that collapses the distribution — what top_k=1 should do."""
    rng = np.random.default_rng(3)
    wide = {"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}
    narrow = {"a": 0.95, "b": 0.05}
    hits = 0
    for t in range(60):
        _, _, _, p = permutation_test(_draw(rng, wide, 40), _draw(rng, narrow, 40),
                                      n_permutations=400, random_state=t)
        hits += p < 0.05
    assert hits / 60 > 0.95, f"power {hits/60:.0%} against a collapse effect"


def test_power_rises_with_sample_size():
    rng = np.random.default_rng(4)
    wide = {"a": 0.4, "b": 0.3, "c": 0.3}
    shifted = {"a": 0.7, "b": 0.2, "c": 0.1}

    def power(n, trials=40):
        hits = 0
        for t in range(trials):
            _, _, _, p = permutation_test(_draw(rng, wide, n), _draw(rng, shifted, n),
                                          n_permutations=300, random_state=t)
            hits += p < 0.05
        return hits / trials

    assert power(10) < power(60), "more completions must buy more power"


# --------------------------------------------------------------------------
# degenerate cases the provider actually produces
# --------------------------------------------------------------------------
def test_a_constant_output_is_reported_as_no_power_not_as_no_effect():
    """Both arms one string: exchangeable by construction.

    p = 1.0 here would read as evidence the parameter does nothing, when it is
    evidence of nothing at all — the prompt had no entropy for this model.
    """
    r = assess_parameter("m", "top_p", {"top_p": 0.01}, {"top_p": 1.0},
                       ["table"] * 20, ["table"] * 20)
    assert r.status == "insufficient"
    assert "entropy" in r.detail


def test_too_few_completions_is_refused():
    r = assess_parameter("m", "top_k", {"top_k": 1}, {"top_k": 100},
                       ["a", "b"], ["c", "d"])
    assert r.status == "insufficient"


def test_observed_tv_is_reported_against_the_null_not_against_zero():
    rng = np.random.default_rng(5)
    dist = {"a": 0.5, "b": 0.3, "c": 0.2}
    r = assess_parameter("m", "min_p", {"min_p": 0.9}, {"min_p": 0.0},
                         _draw(rng, dist, 40), _draw(rng, dist, 40),
                         n_permutations=400)
    assert r.status == "ok"
    # Under the null both statistics are biased away from zero — two samples from
    # one distribution do not coincide — so the observed value only means
    # something against the null's own mean.
    assert r.tv_null_mean > 0.0
    assert abs(r.excess) < 0.5
    assert r.p_value == r.dh_p, "the primary p-value must be the one-sided dH test"


# --------------------------------------------------------------------------
# multiplicity
# --------------------------------------------------------------------------
def test_holm_is_monotone_and_never_smaller_than_the_raw_p():
    rs = []
    for i, p in enumerate([0.001, 0.01, 0.04, 0.2, 0.9]):
        r = Distinguishability(model=f"m{i}", parameter="top_p",
                               setting_a={}, setting_b={}, status="ok")
        r.p_value = p
        rs.append(r)
    adj = holm_adjust(rs)
    vals = [adj[(r.model, r.parameter)] for r in rs]
    assert all(v >= r.p_value for v, r in zip(vals, rs)), "adjustment must not shrink p"
    assert vals == sorted(vals), "adjusted p-values must be monotone in raw p"


def test_holm_skips_untestable_cells():
    ok = Distinguishability(model="a", parameter="top_p", setting_a={}, setting_b={})
    ok.status, ok.p_value = "ok", 0.01
    bad = Distinguishability(model="b", parameter="top_p", setting_a={}, setting_b={})
    bad.status = "insufficient"
    adj = holm_adjust([ok, bad])
    assert ("b", "top_p") not in adj

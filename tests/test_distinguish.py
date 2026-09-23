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


# --------------------------------------------------------------------------
# positive control
#
# A null result is produced identically by an ignored parameter and by a probe
# with no power on this model. The test cannot tell them apart from the inside.
# On the same model and prompt, a parameter known to work must show a large
# effect, or every other null on that model is uninterpretable.
# --------------------------------------------------------------------------
from samplerconfound.distinguish import ModelPower, interpret, positive_control


def _cell(model, param, dh, h_open, h_tight, support_tight=5, n=40, status="ok"):
    r = Distinguishability(model=model, parameter=param, setting_a={}, setting_b={})
    r.status, r.dh, r.entropy_open, r.entropy_tight = status, dh, h_open, h_tight
    r.support_tight, r.n_tight = support_tight, n
    r.dh_p = r.p_value = 0.5
    return r


def _control(t0: list[str], t1: list[str], model="m"):
    """A temperature cell whose tight arm is T=0, and a top_p cell whose open
    arm is unrestricted T=1.0 — the two arms the control now contrasts."""
    temp = Distinguishability(model=model, parameter="temperature", setting_a={}, setting_b={})
    temp.status, temp.completions_tight = "ok", t0
    temp.completions_open = t1
    temp.entropy_open, temp.dh = 5.0, 1.0
    top = Distinguishability(model=model, parameter="top_p", setting_a={}, setting_b={})
    top.status, top.completions_open = "ok", t1
    return positive_control([temp, top], n_permutations=2000)


SPREAD = [f"s{i % 20}" for i in range(40)]          # 20 distinct, ~4.3 bits


def test_control_passes_when_t0_vs_t1_is_detectable():
    power = _control(["a"] * 38 + ["b"] * 2, SPREAD)
    assert power["m"].powered
    assert power["m"].contrast == "T=0 vs T=1.0"
    assert power["m"].fraction_removed > 0.8


def test_control_fails_when_t0_is_as_spread_as_t1():
    # nemotron-lightning: temperature 0 leaves most of the entropy in place
    t0 = [f"s{(i * 3) % 20}" for i in range(40)]
    power = _control(t0, SPREAD)
    assert not power["m"].powered
    assert "means nothing" in power["m"].detail


def test_a_null_reads_differently_depending_on_the_control():
    """The whole point. Same cell, same p-value, opposite meaning."""
    cell = _cell("m", "top_p", dh=0.10, h_open=5.0, h_tight=4.9)
    strong = _control(["a"] * 40, SPREAD)
    weak = _control([f"s{(i * 3) % 20}" for i in range(40)], SPREAD)
    assert interpret(cell, strong, p_adjusted=0.4) == "no effect seen"
    assert interpret(cell, weak, p_adjusted=0.4) == "underpowered"


def test_control_needs_the_unrestricted_arm_and_says_so_when_missing():
    temp = Distinguishability(model="m", parameter="temperature", setting_a={}, setting_b={})
    temp.status, temp.completions_tight = "ok", ["a"] * 40
    power = positive_control([temp], n_permutations=200)
    assert not power["m"].powered and "missing" in power["m"].detail


def test_legacy_rule_is_reported_but_never_decides():
    """The T=0 vs 1.5 fraction rule is kept for comparison only."""
    power = _control(["a"] * 40, SPREAD)
    assert power["m"].legacy_fraction_removed == pytest.approx(0.2)   # dh 1.0 / 5.0
    assert not power["m"].legacy_powered and power["m"].powered


def test_a_significant_effect_is_distinguishable_regardless_of_control():
    cell = _cell("m", "top_k", dh=3.0, h_open=5.0, h_tight=2.0)
    weak = positive_control([_cell("m", "temperature", dh=0.5, h_open=5.0, h_tight=4.5)])
    assert interpret(cell, weak, p_adjusted=0.001) == "distinguishable"


def test_a_model_without_a_control_is_underpowered_not_no_effect():
    # No control run at all: a null cannot be promoted to evidence.
    cell = _cell("m", "min_p", dh=0.1, h_open=5.0, h_tight=4.9)
    assert interpret(cell, {}, p_adjusted=0.6) == "underpowered"


def test_a_significant_effect_is_distinguishable_regardless_of_control_ceiling():
    weak = _control([f"s{(i * 3) % 20}" for i in range(40)], SPREAD)
    cell = _cell("m", "top_k", dh=3.0, h_open=5.0, h_tight=2.0)
    assert interpret(cell, weak, p_adjusted=0.001) == "distinguishable"


# --------------------------------------------------------------------------
# stratified permutation test over prompts
# --------------------------------------------------------------------------

def _arm_cell(tight, open_, status="ok"):
    c = Distinguishability(model="m", parameter="top_k", setting_a={}, setting_b={})
    c.status, c.completions_tight, c.completions_open = status, tight, open_
    return c


def test_stratified_test_pools_weak_signal_across_prompts():
    """Four prompts each too weak alone; together decisive."""
    from samplerconfound.distinguish import stratified_test
    weak = [_arm_cell(["a"] * 14 + [f"x{i}" for i in range(6)],
                      ["a"] * 8 + [f"y{i}" for i in range(12)]) for _ in range(4)]
    single = [assess_parameter("m", "top_k", {}, {}, c.completions_tight, c.completions_open,
                               n_permutations=2000).dh_p for c in weak]
    _, p, k = stratified_test(weak, "dh", n_permutations=4000)
    assert k == 4 and p < min(single) and p < 0.05


def test_stratified_test_is_calibrated_under_the_null():
    """Exchangeable arms on every prompt: rejection rate near alpha."""
    from samplerconfound.distinguish import stratified_test
    rng = np.random.default_rng(1)
    rejects = 0
    trials = 120
    for t in range(trials):
        cells = []
        for _ in range(4):
            pool = [f"w{int(x)}" for x in rng.zipf(1.6, 80) % 25]
            cells.append(_arm_cell(pool[:40], pool[40:]))
        _, p, _ = stratified_test(cells, "dh", n_permutations=400, random_state=t)
        rejects += p < 0.05
    assert rejects / trials < 0.12


def test_stratified_test_skips_degenerate_prompts():
    from samplerconfound.distinguish import stratified_test
    cells = [_arm_cell(["a"] * 40, [f"s{i % 20}" for i in range(40)]),
             _arm_cell([], [], status="insufficient")]
    _, p, k = stratified_test(cells, "dh", n_permutations=1000)
    assert k == 1 and p < 0.01


# --------------------------------------------------------------------------
# negative control
#
# Two arms at IDENTICAL settings must show no effect. This is what calibrates
# the false-positive rate on real output, where the simulated calibration above
# cannot reach: real completions are collected sequentially by a live provider,
# and if its state drifts between the first arm and the second, identical
# settings stop being exchangeable and every positive in the grid inherits the
# inflation. Measured live on 2026-09-11: 0 of 25 informative pairs rejected at
# 0.05, 1 of 25 at 0.10, dH mean -0.08. The unit tests below pin the machinery.
# --------------------------------------------------------------------------
def test_identical_settings_produce_a_null_result_on_real_style_data():
    # Same distribution, two draws, the exact call the script makes.
    rng = np.random.default_rng(11)
    dist = {f"w{i}": 1.0 for i in range(25)}
    r = assess_parameter("m", "null_0", {"temperature": 1.0}, {"temperature": 1.0},
                         _draw(rng, dist, 40), _draw(rng, dist, 40), n_permutations=500)
    assert r.status == "ok"
    assert abs(r.dh) < 1.0
    assert r.dh_p > 0.05


def test_negative_control_rate_is_near_nominal_over_many_pairs():
    rng = np.random.default_rng(12)
    dist = {f"w{i}": 1.0 + (i % 3) for i in range(20)}
    fp = 0
    for k in range(60):
        r = assess_parameter("m", f"null_{k}", {}, {}, _draw(rng, dist, 40),
                             _draw(rng, dist, 40), n_permutations=300, random_state=k)
        fp += r.dh_p < 0.05
    assert fp / 60 < 0.12, f"false-positive rate {fp/60:.0%} at nominal 5%"


def test_a_degenerate_pair_is_insufficient_and_must_not_count():
    # Both arms all-unique: H = log2(n) on both sides, dH = 0, p = 1 by
    # construction. Such a pair cannot produce a false positive, so counting it
    # in a rate flatters the calibration; and it cannot show an effect, so
    # counting it as "no effect" flatters a parameter. It is neither.
    a = [f"u{i}" for i in range(40)]
    b = [f"v{i}" for i in range(40)]
    r = assess_parameter("m", "null", {}, {}, a, b, n_permutations=300)
    assert r.status == "insufficient" and "all-unique" in r.detail
    assert r.support_tight == 40 and r.support_open == 40
# --------------------------------------------------------------------------
# aggregation across prompts
# --------------------------------------------------------------------------
from samplerconfound.distinguish import aggregate


def test_majority_of_powered_prompts_decides():
    a = aggregate({"p1": "distinguishable", "p2": "distinguishable",
                   "p3": "no effect seen"}, "m", "top_p")
    assert a.verdict == "distinguishable" and a.n_powered == 3


def test_a_degenerate_prompt_drops_out_rather_than_voting():
    # p3 failed its control. It must not count against the majority.
    a = aggregate({"p1": "distinguishable", "p2": "distinguishable",
                   "p3": "underpowered", "p4": "underpowered"}, "m", "top_p")
    assert a.verdict == "distinguishable"
    assert a.n_powered == 2


def test_too_few_powered_prompts_is_underpowered_not_no_effect():
    a = aggregate({"p1": "no effect seen", "p2": "underpowered",
                   "p3": "underpowered"}, "m", "top_p")
    assert a.verdict == "underpowered"


def test_no_effect_needs_a_majority_of_powered_prompts():
    a = aggregate({"p1": "no effect seen", "p2": "no effect seen",
                   "p3": "distinguishable"}, "m", "min_p")
    assert a.verdict == "no effect seen"


def test_an_even_split_is_reported_as_mixed_not_forced():
    a = aggregate({"p1": "distinguishable", "p2": "no effect seen"}, "m", "top_k")
    assert a.verdict == "mixed"


def test_one_prompt_alone_cannot_carry_a_verdict():
    a = aggregate({"p1": "distinguishable"}, "m", "top_p")
    assert a.verdict == "underpowered", "a single prompt is a single sample of prompt space"


def test_a_penalty_contrast_uses_total_variation_as_its_primary():
    """A penalty can raise entropy; the one-sided entropy drop would then call a
    real effect 'no effect'. The two-sided TV is the primary for those cells."""
    import random
    rng = random.Random(0)
    narrow = [rng.choice("ab") for _ in range(40)]
    wide = [rng.choice("abcdefgh") for _ in range(40)]
    # "tight" arm is the WIDER one here: entropy goes up, dH is negative
    r = assess_parameter("m", "repetition_penalty", {"repetition_penalty": 1.0},
                         {"repetition_penalty": 2.0}, wide, narrow,
                         n_permutations=2000, primary="tv")
    assert r.primary == "tv" and r.p_value == r.tv_p
    assert r.tv_p < 0.01, "a large two-sided shift must be detected"
    assert r.dh_p > 0.5, "and the one-sided test would have missed it"
    d = assess_parameter("m", "top_p", {}, {}, narrow, wide, n_permutations=500)
    assert d.primary == "dh" and d.p_value == d.dh_p


def test_primary_must_be_a_known_statistic():
    import pytest
    with pytest.raises(ValueError, match="primary"):
        assess_parameter("m", "x", {}, {}, ["a"] * 10, ["b"] * 10, primary="chi2")


def test_both_arms_all_unique_is_insufficient_not_no_effect():
    """The degeneracy that bites penalty contrasts: at temperature 1.0 both
    arms are 40 distinct strings, so TV is 1 under every permutation and
    p = 1 by construction. That is not evidence the parameter did nothing."""
    tight = [f"s{i}" for i in range(40)]
    open_ = [f"t{i}" for i in range(40)]
    r = assess_parameter("m", "presence_penalty", {}, {}, tight, open_,
                         n_permutations=200, primary="tv")
    assert r.status == "insufficient" and "all-unique" in r.detail
    # one repeat short of all-unique on one side still counts as degenerate
    r = assess_parameter("m", "x", {}, {}, tight[:-1] + ["s0"], open_, n_permutations=200)
    assert r.status == "insufficient"
    # but a tight arm with real repeats is a live test
    r = assess_parameter("m", "x", {}, {}, ["a"] * 20 + ["b"] * 20, open_, n_permutations=500)
    assert r.status == "ok"


def test_a_forced_prompt_needs_no_temperature_control_to_call_a_null():
    r = Distinguishability(model="m", parameter="frequency_penalty", setting_a={}, setting_b={},
                           status="ok", p_value=0.6)
    assert interpret(r, {}, 0.6) == "underpowered"            # no control for this model
    assert interpret(r, {}, 0.6, forced=True) == "no effect seen"
    assert interpret(r, {}, 0.01, forced=True) == "distinguishable"


def test_a_parameter_that_fails_the_same_way_on_every_prompt_gets_that_verdict():
    from samplerconfound.distinguish import aggregate
    assert aggregate({"a": "transport", "b": "transport"}, "m", "rep").verdict == "transport"
    assert aggregate({"a": "rejected", "b": "rejected", "c": "rejected"}, "m", "top_k").verdict == "rejected"
    assert aggregate({"a": "transport", "b": "underpowered"}, "m", "rep").verdict == "underpowered"


def test_control_passed_and_verdict_count_are_reported_separately():
    """They are different questions and were once one column called 'powered'.
    A detected effect needs no control, so a parameter can have a verdict on
    more prompts than passed the control — minimax-m3 is 4 and 2."""
    from samplerconfound.distinguish import aggregate
    verdicts = {"a": "distinguishable", "b": "distinguishable",
                "c": "distinguishable", "d": "distinguishable"}
    control = {"a": False, "b": True, "c": False, "d": True}
    agg = aggregate(verdicts, "m", "top_k", control_passed=control)
    assert agg.n_verdict == 4 and agg.n_control_passed == 2
    assert agg.n_powered == agg.n_verdict, "deprecated alias still parses"
    assert agg.verdict == "distinguishable"
    # without the control map the count is explicitly unknown, not zero
    assert aggregate(verdicts, "m", "top_k").n_control_passed == -1


def test_stratified_test_needs_the_completions_attached():
    """Regression: assess_parameter returns statistics without the samples, and
    a cell built from it alone gives the stratified test nothing to permute."""
    from samplerconfound.distinguish import stratified_test
    bare = assess_parameter("m", "control", {}, {}, ["a"] * 40, SPREAD, n_permutations=100)
    assert stratified_test([bare], "dh", n_permutations=100)[2] == 0
    bare.completions_tight, bare.completions_open = ["a"] * 40, SPREAD
    _, p, k = stratified_test([bare], "dh", n_permutations=500)
    assert k == 1 and p < 0.01


def test_a_barely_detectable_control_is_not_powered():
    """Regression. T=0 at 18 distinct values against T=1.0 at 20 is detectable
    (p ~ 0.01) but far from reliably so; at the Holm-corrected level a family of
    eight tests faces, the power to see a full-strength effect is low, and a
    null licensed by it would be a coin flip. It must read underpowered."""
    t0 = [f"s{(i * 7) % 18}" for i in range(40)]
    temp = Distinguishability(model="m", parameter="temperature", setting_a={}, setting_b={})
    temp.status, temp.completions_tight, temp.entropy_open, temp.dh = "ok", t0, 5.0, 1.0
    top = Distinguishability(model="m", parameter="top_p", setting_a={}, setting_b={})
    top.status, top.completions_open = "ok", SPREAD
    mp = positive_control([temp, top], n_permutations=2000, alpha=0.05 / 8)["m"]
    assert mp.control_p < 0.05, "the contrast IS detectable at 0.05"
    assert mp.control_power < 0.8 and not mp.powered, "but not with the power a null needs"


def test_power_estimate_is_high_for_a_large_effect_and_near_alpha_for_none():
    from samplerconfound.distinguish import estimate_power
    assert estimate_power([(["a"] * 40, SPREAD)], alpha=0.05 / 8, boot=60) > 0.95
    same = [f"s{(i * 3) % 20}" for i in range(40)]
    assert estimate_power([(same, SPREAD)], alpha=0.05, boot=60) < 0.3

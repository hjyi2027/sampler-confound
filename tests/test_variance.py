"""Recovery tests: simulate known variance components, check they come back.

A decomposition that runs without erroring proves nothing. These tests generate
data from a random-effects model with components chosen in advance and assert the
estimator recovers them. If the EMS algebra is wrong, these fail; nothing else in
the pipeline would.
"""

import numpy as np
import pytest

from samplerconfound.variance import decompose_accuracy, decompose_items


def simulate_two_way(a, b, n, s2_model, s2_sampler, s2_ab, s2_e, mu=0.4, seed=0):
    rng = np.random.default_rng(seed)
    A = rng.normal(0, np.sqrt(s2_model), a)
    B = rng.normal(0, np.sqrt(s2_sampler), b)
    AB = rng.normal(0, np.sqrt(s2_ab), (a, b))
    vals, ms, ss = [], [], []
    for i in range(a):
        for j in range(b):
            for _ in range(n):
                vals.append(mu + A[i] + B[j] + AB[i, j] + rng.normal(0, np.sqrt(s2_e)))
                ms.append(f"m{i}")
                ss.append(f"s{j}")
    return np.array(vals), ms, ss


def test_two_way_is_unbiased_for_the_components():
    """Averaged over simulations, each component must land on its true value.

    Deliberately NOT a single-draw test. A random-effects component is the variance
    of the *population* the levels are drawn from, and any one draw of k levels has
    a realised variance scattered around it by roughly sqrt(2/(k-1)) in relative
    terms — 26% at k=30. A single-draw assertion would therefore be testing the
    luck of the draw, and would have to be loosened until it stopped testing the
    algebra at all. Unbiasedness across draws is the property this estimator
    actually has, so that is what gets asserted.
    """
    truth = {"model": 0.02, "sampler": 0.01, "model:sampler": 0.004, "resampling": 0.001}
    got = {k: [] for k in truth}
    for seed in range(60):
        y, ms, ss = simulate_two_way(
            8, 8, 5, truth["model"], truth["sampler"], truth["model:sampler"],
            truth["resampling"], seed=seed,
        )
        d = decompose_accuracy(y, ms, ss, n_boot=0)
        for k in truth:
            got[k].append(d.get(k)["var_component"])
    for source, want in truth.items():
        mean = float(np.mean(got[source]))
        assert mean == pytest.approx(want, rel=0.20), f"{source}: mean {mean} vs {want}"


def test_two_way_recovers_the_realised_level_variance():
    """A single draw must match the spread of the effects actually drawn.

    Complements the unbiasedness test: that one could pass while every individual
    estimate was garbage. This one pins one concrete draw.
    """
    rng = np.random.default_rng(21)
    a, b, n = 40, 40, 6
    A = rng.normal(0, np.sqrt(0.02), a)
    B = rng.normal(0, np.sqrt(0.01), b)
    y, ms, ss = [], [], []
    for i in range(a):
        for j in range(b):
            for _ in range(n):
                y.append(0.4 + A[i] + B[j] + rng.normal(0, np.sqrt(0.001)))
                ms.append(f"m{i}")
                ss.append(f"s{j}")
    d = decompose_accuracy(np.array(y), ms, ss, n_boot=0)
    assert d.get("model")["var_component"] == pytest.approx(A.var(ddof=1), rel=0.15)
    assert d.get("sampler")["var_component"] == pytest.approx(B.var(ddof=1), rel=0.15)


def test_two_way_ratio_tracks_truth():
    """The headline number is a ratio; check it, not just the parts."""
    y, ms, ss = simulate_two_way(25, 25, 8, 0.01, 0.02, 0.002, 0.001, seed=3)
    d = decompose_accuracy(y, ms, ss, n_boot=0)
    assert d.sampler_to_model == pytest.approx(2.0, rel=0.3)


def test_two_way_ss_is_orthogonal():
    """Balanced and fully crossed means the parts must sum to the total exactly."""
    y, ms, ss = simulate_two_way(4, 6, 5, 0.02, 0.01, 0.005, 0.002, seed=11)
    d = decompose_accuracy(y, ms, ss, n_boot=0)
    parts = sum(r["ss"] for r in d.table)
    total = ((y - y.mean()) ** 2).sum()
    assert parts == pytest.approx(total, rel=1e-10)


def test_zero_sampler_effect_gives_zero_component():
    """A null must come back as a null, not as a small positive number."""
    y, ms, ss = simulate_two_way(20, 20, 8, 0.02, 0.0, 0.0, 0.001, seed=5)
    d = decompose_accuracy(y, ms, ss, n_boot=0)
    assert d.get("sampler")["var_component"] < 0.002
    assert d.sampler_to_model < 0.15


def test_greedy_style_zero_within_cell_variance():
    """A deterministic cell has no resampling variance and must not divide by zero.

    This is the greedy-decoding case, and it is expected in the real sweep.
    """
    vals, ms, ss = [], [], []
    for i in range(3):
        for j in range(4):
            for _ in range(5):
                vals.append(0.5 + 0.01 * i + 0.02 * j)
                ms.append(f"m{i}")
                ss.append(f"s{j}")
    d = decompose_accuracy(np.array(vals), ms, ss, n_boot=0)
    assert d.get("resampling")["var_component"] == pytest.approx(0.0, abs=1e-12)
    assert np.isfinite(d.get("sampler")["var_component"])


def test_unbalanced_design_raises_rather_than_guessing():
    y, ms, ss = simulate_two_way(3, 4, 5, 0.02, 0.01, 0.005, 0.001)
    with pytest.raises(ValueError, match="unbalanced"):
        decompose_accuracy(y[:-1], ms[:-1], ss[:-1], n_boot=0)


def test_single_replicate_raises():
    y, ms, ss = simulate_two_way(3, 4, 1, 0.02, 0.01, 0.005, 0.001)
    with pytest.raises(ValueError, match="replicates"):
        decompose_accuracy(y, ms, ss, n_boot=0)


def test_bootstrap_interval_brackets_the_estimate():
    y, ms, ss = simulate_two_way(6, 8, 6, 0.01, 0.02, 0.003, 0.001, seed=9)
    d = decompose_accuracy(y, ms, ss, n_boot=400, random_state=1)
    lo, hi = d.sampler_to_model_ci
    assert np.isfinite(lo) and lo > 0
    assert lo <= d.sampler_to_model <= hi


def simulate_three_way(a, b, c, n, comps, mu=0.4, seed=0):
    rng = np.random.default_rng(seed)
    A = rng.normal(0, np.sqrt(comps["model"]), a)
    B = rng.normal(0, np.sqrt(comps["sampler"]), b)
    C = rng.normal(0, np.sqrt(comps["problem"]), c)
    AB = rng.normal(0, np.sqrt(comps["model:sampler"]), (a, b))
    AC = rng.normal(0, np.sqrt(comps["model:problem"]), (a, c))
    BC = rng.normal(0, np.sqrt(comps["sampler:problem"]), (b, c))
    ABC = rng.normal(0, np.sqrt(comps["model:sampler:problem"]), (a, b, c))
    sd_e = np.sqrt(comps["resampling"])
    vals, ml, sl, pl = [], [], [], []
    for i in range(a):
        for j in range(b):
            for k in range(c):
                base = mu + A[i] + B[j] + C[k] + AB[i, j] + AC[i, k] + BC[j, k] + ABC[i, j, k]
                for _ in range(n):
                    vals.append(base + rng.normal(0, sd_e))
                    ml.append(f"m{i}")
                    sl.append(f"s{j}")
                    pl.append(f"p{k}")
    return np.array(vals), ml, sl, pl


def test_three_way_is_unbiased_for_the_components():
    """Same argument as the two-way: unbiasedness across draws, not one draw.

    This is the test that would catch an error in the three-way EMS algebra, which
    is the piece written fresh here rather than ported.
    """
    truth = {
        "model": 0.03,
        "sampler": 0.015,
        "problem": 0.05,
        "model:sampler": 0.004,
        "model:problem": 0.006,
        "sampler:problem": 0.005,
        "model:sampler:problem": 0.003,
        "resampling": 0.002,
    }
    got = {k: [] for k in truth}
    for seed in range(40):
        y, ml, sl, pl = simulate_three_way(6, 6, 7, 3, truth, seed=seed)
        d = decompose_items(y, ml, sl, pl)
        for k in truth:
            got[k].append(d.get(k)["var_component"])
    for source, want in truth.items():
        mean = float(np.mean(got[source]))
        assert mean == pytest.approx(want, rel=0.25), f"{source}: mean {mean} vs {want}"


def test_three_way_ss_is_orthogonal():
    truth = dict.fromkeys(
        [
            "model", "sampler", "problem", "model:sampler", "model:problem",
            "sampler:problem", "model:sampler:problem", "resampling",
        ],
        0.01,
    )
    y, ml, sl, pl = simulate_three_way(3, 4, 5, 3, truth, seed=2)
    d = decompose_items(y, ml, sl, pl)
    parts = sum(r["ss"] for r in d.table)
    assert parts == pytest.approx(((y - y.mean()) ** 2).sum(), rel=1e-10)


def test_three_way_on_binary_outcomes_runs_and_is_bounded():
    """The real item-level response is 0/1. Shares must still be a partition."""
    rng = np.random.default_rng(4)
    vals, ml, sl, pl = [], [], [], []
    for i in range(3):
        for j in range(4):
            for k in range(20):
                # Problem difficulty spans a far wider range than model or
                # sampler, which is the real regime: some problems are simply hard.
                p = 1 / (1 + np.exp(-(0.3 * i + 0.1 * j - 0.35 * (k - 10))))
                for _ in range(5):
                    vals.append(float(rng.random() < p))
                    ml.append(f"m{i}")
                    sl.append(f"s{j}")
                    pl.append(f"p{k}")
    d = decompose_items(np.array(vals), ml, sl, pl)
    shares = list(d.var_share.values())
    assert all(s >= 0 for s in shares)
    assert sum(shares) == pytest.approx(1.0)
    # Problem difficulty is the dominant term by construction; if it is not, the
    # blocking factor is not doing its job.
    assert d.var_share["problem"] > d.var_share["model"]


# --------------------------------------------------------------------------
# the bootstrap interval must contain its own point estimate
#
# Found by the 1/20 smoke run, which reported "sampler:model ratio = 0.000,
# 95% CI [0.013, inf]" — an interval excluding the estimate it describes, which
# reads as "significantly nonzero" when the estimate is exactly zero. The cause
# was dropping bootstrap replicates whose ratio was zero. Those zeros are data:
# the components are method-of-moments estimates clamped at zero, so the ratio's
# sampling distribution has a genuine atom there.
# --------------------------------------------------------------------------
def test_ci_contains_a_zero_point_estimate():
    rng = np.random.default_rng(0)
    # Sampler has no effect; model does. The sampler component will clamp to 0.
    models, samplers, acc = [], [], []
    for mi, base in enumerate([0.60, 0.75, 0.90, 0.95]):
        for s in ["greedy", "lowtemp", "standard", "hightemp", "topk"]:
            for _ in range(5):
                models.append(f"m{mi}")
                samplers.append(s)
                acc.append(base + rng.normal(0, 0.01))
    d = decompose_accuracy(acc, models, samplers, n_boot=500)
    lo, hi = d.sampler_to_model_ci
    r = d.sampler_to_model
    if np.isfinite(r):
        assert lo <= r <= hi, f"CI [{lo}, {hi}] excludes point estimate {r}"


def test_ci_contains_the_estimate_when_sampler_dominates():
    rng = np.random.default_rng(1)
    models, samplers, acc = [], [], []
    sampler_effect = {"greedy": 0.0, "lowtemp": 0.06, "standard": 0.12,
                      "hightemp": 0.18, "topk": 0.24}
    for mi in range(4):
        for s, eff in sampler_effect.items():
            for _ in range(5):
                models.append(f"m{mi}")
                samplers.append(s)
                acc.append(0.70 + eff + rng.normal(0, 0.01))
    d = decompose_accuracy(acc, models, samplers, n_boot=500)
    lo, hi = d.sampler_to_model_ci
    r = d.sampler_to_model
    assert r > 1.0, "sampler should dominate in this fixture"
    assert lo <= r <= hi, f"CI [{lo}, {hi}] excludes point estimate {r}"


def test_zero_ratio_replicates_are_not_discarded():
    # A distribution that is mostly zeros must yield a lower bound of zero,
    # not the 2.5th percentile of the positive tail.
    rng = np.random.default_rng(2)
    models, samplers, acc = [], [], []
    for mi, base in enumerate([0.50, 0.70, 0.85, 0.95]):
        for s in ["greedy", "lowtemp", "standard", "hightemp", "topk"]:
            for _ in range(5):
                models.append(f"m{mi}")
                samplers.append(s)
                acc.append(base + rng.normal(0, 0.005))
    d = decompose_accuracy(acc, models, samplers, n_boot=500)
    lo, _ = d.sampler_to_model_ci
    assert lo == 0.0 or lo < 0.01, f"lower bound {lo} looks like the positive tail"

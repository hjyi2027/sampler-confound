"""Per-problem solve rate: aggregation, recovery, and the binomial correction."""

import numpy as np
import pytest

from samplerconfound.variance import decompose_solve_rate, solve_rates


def simulate_binary(a, b, c, n, seed=0, model_eff=0.15, sampler_eff=0.08, prob_sd=1.2):
    """Bernoulli outcomes from a logistic model with known factor structure."""
    rng = np.random.default_rng(seed)
    A = rng.normal(0, model_eff, a)
    B = rng.normal(0, sampler_eff, b)
    C = rng.normal(0, prob_sd, c)
    y, ml, sl, pl = [], [], [], []
    for i in range(a):
        for j in range(b):
            for k in range(c):
                p = 1 / (1 + np.exp(-(A[i] + B[j] + C[k])))
                for _ in range(n):
                    y.append(float(rng.random() < p))
                    ml.append(f"m{i}")
                    sl.append(f"s{j}")
                    pl.append(f"p{k}")
    return np.array(y), ml, sl, pl


# --- aggregation --------------------------------------------------------


def test_solve_rates_aggregates_to_one_value_per_cell():
    y, ml, sl, pl = simulate_binary(3, 4, 10, 5, seed=1)
    rates, m, s, p, n = solve_rates(y, ml, sl, pl)
    assert n == 5
    assert rates.size == 3 * 4 * 10
    assert len(m) == len(s) == len(p) == rates.size
    assert rates.min() >= 0.0 and rates.max() <= 1.0
    # Rates from 5 replicates can only take 6 values.
    assert set(np.unique(rates)).issubset({0.0, 0.2, 0.4, 0.6, 0.8, 1.0})


def test_solve_rates_computes_the_right_fraction():
    y = [1, 1, 0, 0,  1, 0, 0, 0]
    ml = ["m0"] * 8
    sl = ["s0"] * 4 + ["s1"] * 4
    pl = ["p0"] * 8
    with pytest.raises(ValueError):        # only one problem level
        decompose_solve_rate(*solve_rates(y, ml, sl, pl)[:4])
    rates, _, _, _, n = solve_rates(y, ml, sl, pl)
    assert n == 4
    assert sorted(rates.tolist()) == [0.25, 0.5]


def test_solve_rates_rejects_an_unbalanced_grid():
    y, ml, sl, pl = simulate_binary(2, 2, 3, 4, seed=2)
    with pytest.raises(ValueError, match="unbalanced"):
        solve_rates(y[:-1], ml[:-1], sl[:-1], pl[:-1])


# --- the decomposition --------------------------------------------------


def test_end_to_end_from_binary_outcomes():
    y, ml, sl, pl = simulate_binary(3, 5, 40, 6, seed=3)
    rates, m, s, p, n = solve_rates(y, ml, sl, pl)
    d = decompose_solve_rate(rates, m, s, p, n_replicates=n)
    assert d.n_models == 3 and d.n_samplers == 5 and d.n_problems == 40
    assert d.n_replicates == 6
    shares = list(d.var_share.values())
    assert all(x >= 0 for x in shares)
    assert sum(shares) == pytest.approx(1.0)
    # Problem difficulty dominates by construction, as it will in the real sweep.
    assert d.var_share["problem"] > d.var_share["sampler"]


def test_sums_of_squares_partition_the_total():
    y, ml, sl, pl = simulate_binary(3, 4, 12, 5, seed=4)
    rates, m, s, p, n = solve_rates(y, ml, sl, pl)
    d = decompose_solve_rate(rates, m, s, p, n_replicates=n)
    assert sum(r["ss"] for r in d.table) == pytest.approx(
        ((rates - rates.mean()) ** 2).sum(), rel=1e-10
    )


def _crossed_rates(a, b, c, sd_a, sd_b, sd_c, seed):
    rng = np.random.default_rng(seed)
    A = rng.normal(0, sd_a, a)
    B = rng.normal(0, sd_b, b)
    C = rng.normal(0, sd_c, c)
    rates, ml, sl, pl = [], [], [], []
    for i in range(a):
        for j in range(b):
            for k in range(c):
                rates.append(np.clip(0.5 + A[i] + B[j] + C[k], 0, 1))
                ml.append(f"m{i}")
                sl.append(f"s{j}")
                pl.append(f"p{k}")
    return np.array(rates), ml, sl, pl, A, B, C


def test_recovers_the_realised_level_variance():
    """Pinned against the effects actually drawn, not the population parameter.

    A random-effects component estimates the variance of the population the levels
    came from, and one draw of k levels scatters around it by about sqrt(2/(k-1))
    in relative terms — 53% at k=8. Asserting against the generating parameter
    would be testing the draw; asserting against the realised spread tests the
    algebra, which is the thing that can be wrong.
    """
    rates, ml, sl, pl, A, B, C = _crossed_rates(
        8, 8, 30, np.sqrt(0.004), np.sqrt(0.002), np.sqrt(0.02), seed=11
    )
    d = decompose_solve_rate(rates, ml, sl, pl, correct_binomial_noise=False)
    assert d.get("model")["var_component"] == pytest.approx(A.var(ddof=1), rel=0.20)
    assert d.get("sampler")["var_component"] == pytest.approx(B.var(ddof=1), rel=0.20)
    assert d.get("problem")["var_component"] == pytest.approx(C.var(ddof=1), rel=0.20)


def test_is_unbiased_for_the_components_across_draws():
    """The complementary property: averaged over draws, land on the truth."""
    truth = {"model": 0.004, "sampler": 0.002, "problem": 0.02}
    got = {k: [] for k in truth}
    for seed in range(40):
        rates, ml, sl, pl, *_ = _crossed_rates(
            5, 5, 12, np.sqrt(truth["model"]), np.sqrt(truth["sampler"]),
            np.sqrt(truth["problem"]), seed=seed,
        )
        d = decompose_solve_rate(rates, ml, sl, pl, correct_binomial_noise=False)
        for k in truth:
            got[k].append(d.get(k)["var_component"])
    for src, want in truth.items():
        mean = float(np.mean(got[src]))
        assert mean == pytest.approx(want, rel=0.25), f"{src}: mean {mean} vs {want}"


def test_ratio_tracks_a_known_sampler_to_model_ratio():
    rng = np.random.default_rng(12)
    a, b, c = 10, 10, 25
    A = rng.normal(0, np.sqrt(0.002), a)
    B = rng.normal(0, np.sqrt(0.006), b)      # sampler 3x model
    C = rng.normal(0, np.sqrt(0.01), c)
    rates, ml, sl, pl = [], [], [], []
    for i in range(a):
        for j in range(b):
            for k in range(c):
                rates.append(np.clip(0.5 + A[i] + B[j] + C[k], 0, 1))
                ml.append(f"m{i}")
                sl.append(f"s{j}")
                pl.append(f"p{k}")
    d = decompose_solve_rate(np.array(rates), ml, sl, pl, correct_binomial_noise=False)
    assert d.sampler_to_model == pytest.approx(3.0, rel=0.4)


# --- the binomial correction -------------------------------------------


def test_binomial_correction_reduces_the_residual():
    y, ml, sl, pl = simulate_binary(3, 4, 30, 5, seed=5)
    rates, m, s, p, n = solve_rates(y, ml, sl, pl)
    corrected = decompose_solve_rate(rates, m, s, p, n_replicates=n)
    raw = decompose_solve_rate(rates, m, s, p, correct_binomial_noise=False)
    assert corrected.binomial_noise > 0
    assert corrected.residual_corrected < raw.get("residual")["var_component"]
    assert corrected.residual_raw == pytest.approx(raw.get("residual")["var_component"])


def test_uncorrected_residual_deflates_the_sampler_share():
    """The reason the correction exists: without it, shares depend on R.

    Finite-replicate noise inflates the error stratum, which shrinks every other
    share. Left in, the headline ratio would move when the sweep was rerun with a
    different number of replicates — a property of the budget, not the world.
    """
    y, ml, sl, pl = simulate_binary(3, 5, 40, 5, seed=6)
    rates, m, s, p, n = solve_rates(y, ml, sl, pl)
    corrected = decompose_solve_rate(rates, m, s, p, n_replicates=n)
    raw = decompose_solve_rate(rates, m, s, p, correct_binomial_noise=False)
    assert corrected.var_share["sampler"] > raw.var_share["sampler"]


def test_correction_cannot_drive_the_residual_negative():
    """Pure noise: every rate is an independent coin flip, so the true residual
    is zero and the estimated noise may exceed the observed residual."""
    rng = np.random.default_rng(7)
    a, b, c, n = 3, 4, 20, 5
    y, ml, sl, pl = [], [], [], []
    for i in range(a):
        for j in range(b):
            for k in range(c):
                for _ in range(n):
                    y.append(float(rng.random() < 0.5))
                    ml.append(f"m{i}")
                    sl.append(f"s{j}")
                    pl.append(f"p{k}")
    rates, m, s, p, r = solve_rates(np.array(y), ml, sl, pl)
    d = decompose_solve_rate(rates, m, s, p, n_replicates=r)
    assert d.residual_corrected >= 0.0
    assert all(row["var_component"] >= 0.0 for row in d.table)


# --- guards -------------------------------------------------------------


def test_rejects_values_outside_the_unit_interval():
    y, ml, sl, pl = simulate_binary(2, 2, 5, 4, seed=8)
    rates, m, s, p, n = solve_rates(y, ml, sl, pl)
    counts = rates * n          # raw counts, a plausible mistake
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        decompose_solve_rate(counts, m, s, p, n_replicates=n)


def test_rejects_an_incomplete_grid():
    y, ml, sl, pl = simulate_binary(2, 3, 6, 4, seed=9)
    rates, m, s, p, n = solve_rates(y, ml, sl, pl)
    with pytest.raises(ValueError, match="expected exactly one solve rate"):
        decompose_solve_rate(rates[:-1], m[:-1], s[:-1], p[:-1], n_replicates=n)


def test_rejects_duplicate_cells():
    y, ml, sl, pl = simulate_binary(2, 2, 4, 3, seed=10)
    rates, m, s, p, n = solve_rates(y, ml, sl, pl)
    p2 = list(p)
    p2[0] = p2[1]               # same cell twice, another cell now missing
    with pytest.raises(ValueError, match="duplicate"):
        decompose_solve_rate(rates, m, s, p2, n_replicates=n)

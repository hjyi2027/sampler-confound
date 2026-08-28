"""Inversion rate: constructed cases where the right answer is known by hand."""

import numpy as np
import pytest

from samplerconfound.inversion import inversion_rate


def build(cells, n=5, noise=0.0, seed=0):
    """cells: {(model, sampler): accuracy} -> flat replicate arrays."""
    rng = np.random.default_rng(seed)
    y, ms, ss = [], [], []
    for (m, s), acc in cells.items():
        for _ in range(n):
            y.append(acc + (rng.normal(0, noise) if noise else 0.0))
            ms.append(m)
            ss.append(s)
    return np.array(y), ms, ss


def test_no_inversion_when_ranking_is_stable():
    cells = {
        ("A", "greedy"): 0.60, ("A", "hot"): 0.55,
        ("B", "greedy"): 0.50, ("B", "hot"): 0.45,
    }
    y, ms, ss = build(cells, noise=0.002, seed=1)
    r = inversion_rate(y, ms, ss)
    assert r.n_comparisons == 1
    assert r.n_raw == 0
    assert r.decisive_rate == 0.0


def test_clean_inversion_is_caught_and_called_decisive():
    # A beats B under greedy; B beats A under hot. Separation is far above noise.
    cells = {
        ("A", "greedy"): 0.60, ("A", "hot"): 0.40,
        ("B", "greedy"): 0.50, ("B", "hot"): 0.55,
    }
    y, ms, ss = build(cells, noise=0.002, seed=2)
    r = inversion_rate(y, ms, ss)
    assert r.n_raw == 1
    assert r.n_decisive == 1
    assert r.pairs_ever_inverted == ["A vs B"]


def test_flip_between_tied_models_is_raw_but_not_decisive():
    """The distinction the paper rests on: a meaningless flip must not count."""
    cells = {
        ("A", "greedy"): 0.5001, ("A", "hot"): 0.4999,
        ("B", "greedy"): 0.4999, ("B", "hot"): 0.5001,
    }
    y, ms, ss = build(cells, noise=0.02, seed=3)
    r = inversion_rate(y, ms, ss)
    assert r.n_raw == 1
    assert r.n_decisive == 0


def test_denominator_is_pairs_times_sampler_pairs():
    models = ["A", "B", "C"]
    samplers = [f"s{i}" for i in range(6)]
    cells = {
        (m, s): 0.5 + 0.01 * i + 0.003 * j
        for i, m in enumerate(models)
        for j, s in enumerate(samplers)
    }
    y, ms, ss = build(cells, noise=0.001, seed=4)
    r = inversion_rate(y, ms, ss)
    assert r.n_comparisons == 3 * 15


def test_sampler_range_reports_the_travel_of_one_model():
    cells = {
        ("A", "greedy"): 0.60, ("A", "hot"): 0.44, ("A", "mid"): 0.52,
        ("B", "greedy"): 0.50, ("B", "hot"): 0.48, ("B", "mid"): 0.49,
    }
    y, ms, ss = build(cells, noise=0.001, seed=5)
    r = inversion_rate(y, ms, ss)
    a = next(x for x in r.sampler_range if x["model"] == "A")
    assert a["range"] == pytest.approx(0.16, abs=0.01)
    assert a["argmax_sampler"] == "greedy"
    assert a["argmin_sampler"] == "hot"


def test_incomplete_grid_raises():
    cells = {("A", "greedy"): 0.6, ("A", "hot"): 0.5, ("B", "greedy"): 0.5}
    y, ms, ss = build(cells)
    with pytest.raises(ValueError, match="incomplete"):
        inversion_rate(y, ms, ss)


# --------------------------------------------------------------------------
# paired, problem-level decisiveness
#
# Core metric 2 is now the headline, so its decisiveness test has to survive the
# case this study is full of: a deterministic cell. At temperature 0 the
# replicate SEM is exactly zero and `abs(d) > z * 0` holds for any nonzero
# difference, so every greedy comparison is "decisive" by construction — on the
# configuration harnesses claim to use. Two of four greedy cells in the smoke run
# had an SEM of exactly zero.
# --------------------------------------------------------------------------
from samplerconfound.inversion import inversion_rate_paired


def _grid(spec, n_problems=40, n_reps=3):
    """spec: {(model, sampler): per-problem solve probability} -> flat arrays."""
    rng = np.random.default_rng(0)
    correct, ml, sl, pl = [], [], [], []
    for (m, s), prob in spec.items():
        for i in range(n_problems):
            for _ in range(n_reps):
                correct.append(float(rng.random() < prob))
                ml.append(m); sl.append(s); pl.append(f"p{i}")
    return correct, ml, sl, pl


def _deterministic_grid(spec, n_problems=40, n_reps=3):
    """Every replicate identical — the temperature-0 case."""
    correct, ml, sl, pl = [], [], [], []
    for (m, s), k in spec.items():
        for i in range(n_problems):
            for _ in range(n_reps):
                correct.append(1.0 if i < k else 0.0)
                ml.append(m); sl.append(s); pl.append(f"p{i}")
    return correct, ml, sl, pl


def test_deterministic_cells_do_not_make_everything_decisive():
    # A one-problem edge in each direction: a real flip, but far inside benchmark
    # noise. The replicate SEM is zero here, so the old test calls it decisive.
    spec = {("A", "s1"): 21, ("B", "s1"): 20, ("A", "s2"): 20, ("B", "s2"): 21}
    correct, ml, sl, pl = _deterministic_grid(spec)
    paired = inversion_rate_paired(correct, ml, sl, pl)
    assert paired.n_raw == 1, "the sign really does flip"
    assert paired.n_decisive == 0, "a one-problem edge must not count as decisive"


def test_a_large_deterministic_flip_is_still_decisive():
    # Same deterministic structure, but a 30-point swing each way.
    spec = {("A", "s1"): 34, ("B", "s1"): 22, ("A", "s2"): 22, ("B", "s2"): 34}
    correct, ml, sl, pl = _deterministic_grid(spec)
    paired = inversion_rate_paired(correct, ml, sl, pl)
    assert paired.n_raw == 1
    assert paired.n_decisive == 1


def test_paired_se_is_finite_when_replicates_are_identical():
    spec = {("A", "s1"): 30, ("B", "s1"): 10, ("A", "s2"): 10, ("B", "s2"): 30}
    correct, ml, sl, pl = _deterministic_grid(spec)
    ex = inversion_rate_paired(correct, ml, sl, pl).examples
    assert ex and all(np.isfinite(e["se_first"]) and np.isfinite(e["se_second"])
                      for e in ex), "problem variation survives deterministic decoding"


def test_no_inversion_when_one_model_dominates_everywhere():
    spec = {("A", "s1"): 0.9, ("B", "s1"): 0.5, ("A", "s2"): 0.85, ("B", "s2"): 0.45}
    inv = inversion_rate_paired(*_grid(spec))
    assert inv.n_raw == 0 and inv.n_decisive == 0


def test_incomplete_grid_is_refused():
    correct, ml, sl, pl = _grid({("A", "s1"): 0.5, ("B", "s1"): 0.5,
                                 ("A", "s2"): 0.5, ("B", "s2"): 0.5})
    with pytest.raises(ValueError, match="incomplete grid"):
        inversion_rate_paired(correct[:-3], ml[:-3], sl[:-3], pl[:-3])


def test_denominator_is_every_model_pair_by_sampler_pair():
    spec = {(m, s): 0.6 for m in ("A", "B", "C") for s in ("s1", "s2", "s3")}
    inv = inversion_rate_paired(*_grid(spec, n_problems=10, n_reps=2))
    # 3 model pairs x 3 sampler pairs
    assert inv.n_comparisons == 9

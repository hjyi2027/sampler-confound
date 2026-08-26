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

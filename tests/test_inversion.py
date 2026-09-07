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


# --------------------------------------------------------------------------
# quotable inversions
#
# "A single inverted comparison is a concrete, quotable harm." A rate is an
# aggregate and aggregates are easy to discount; one named comparison, with both
# accuracies and the decoding parameters that produced them, is not.
# --------------------------------------------------------------------------
from samplerconfound.inversion import quotable


def _flip_grid():
    # A beats B under s1; B beats A under s2. Deterministic so the numbers are exact.
    spec = {("A", "s1"): 34, ("B", "s1"): 22, ("A", "s2"): 22, ("B", "s2"): 34}
    return _deterministic_grid(spec)


def test_a_quote_names_both_accuracies_and_both_directions():
    inv = inversion_rate_paired(*_flip_grid())
    q = quotable(inv)[0]
    # Both sides of the flip, as percentages, must appear.
    assert "85.0%" in q and "55.0%" in q
    assert "reverses" in q
    assert "Same models, same problems, same prompt, same grader" in q


def test_a_quote_names_the_decoding_parameters_when_available():
    inv = inversion_rate_paired(*_flip_grid())
    defs = [{"id": "s1", "temperature": 0.0},
            {"id": "s2", "temperature": 0.3, "top_p": 1.0}]
    q = quotable(inv, samplers=defs)[0]
    assert "temperature 0.0" in q, "a quote must name the config, not an internal id"
    assert "temperature 0.3" in q and "top-p 1.0" in q


def test_decisive_examples_are_quoted_first():
    # One decisive flip (large) and one trivial flip (one problem each way).
    spec = {("A", "s1"): 34, ("B", "s1"): 22, ("A", "s2"): 22, ("B", "s2"): 34,
            ("C", "s1"): 21, ("D", "s1"): 20, ("C", "s2"): 20, ("D", "s2"): 21}
    inv = inversion_rate_paired(*_deterministic_grid(spec))
    assert inv.n_raw >= 2
    quotes = quotable(inv, top_n=5)
    assert "decisive" in quotes[0] and "within benchmark noise" not in quotes[0]


def test_quote_ranks_on_the_weaker_side_of_the_flip():
    # A reader's objection lands on whichever direction is least convincing, so
    # ranking must use the smaller margin, not the sum or the larger one.
    spec = {("A", "s1"): 38, ("B", "s1"): 18, ("A", "s2"): 19, ("B", "s2"): 21,
            ("C", "s1"): 30, ("D", "s1"): 22, ("C", "s2"): 22, ("D", "s2"): 30}
    inv = inversion_rate_paired(*_deterministic_grid(spec))
    quotes = quotable(inv, top_n=5)
    # The C/D flip is 20/20 points; the A/B flip is 50 points then only 5.
    assert "C" in quotes[0] and "D" in quotes[0]


def test_no_inversions_yields_no_quotes():
    spec = {("A", "s1"): 0.9, ("B", "s1"): 0.5, ("A", "s2"): 0.88, ("B", "s2"): 0.48}
    assert quotable(inversion_rate_paired(*_grid(spec))) == []


# --------------------------------------------------------------------------
# an interval for the headline rate
#
# The obvious interval is binomial over the comparisons, and it is wrong: with
# 3 models and 7 samplers there are 63 comparisons drawn from 21 cells, so each
# cell feeds about twelve of them. Treating them as independent draws overstates
# the effective sample size and reports an interval narrower than the evidence.
# Problems are the sampling unit; resampling them moves every cell coherently.
# --------------------------------------------------------------------------
from samplerconfound.inversion import bootstrap_inversion_ci


def test_the_interval_responds_to_problem_count_where_a_binomial_cannot():
    """The sharpest statement of why the binomial is the wrong interval.

    Its width is a function of the number of COMPARISONS alone, so halving the
    problems — halving the actual evidence — leaves it unchanged. The inversion
    rate is an estimate about a benchmark, and evidence about a benchmark is
    problems. A cluster bootstrap over problems widens as it must.

    Measured at the study's grid shape, the binomial covered a known population
    rate 84% of the time against a nominal 95%; the cluster bootstrap covered
    100%. It is anti-conservative, not merely differently scaled.
    """
    spec = {(m, s): 0.45 + 0.12 * (i % 4) for i, (m, s) in enumerate(
        [(m, s) for m in ("A", "B", "C") for s in ("s1", "s2", "s3", "s4")])}
    wide = inversion_rate_paired(*_grid(spec, n_problems=25, n_reps=2),
                                 n_boot=250, random_state=1)
    narrow = inversion_rate_paired(*_grid(spec, n_problems=200, n_reps=2),
                                   n_boot=250, random_state=1)
    assert wide.n_comparisons == narrow.n_comparisons, "binomial n is identical"
    w_wide = wide.raw_rate_ci[1] - wide.raw_rate_ci[0]
    w_narrow = narrow.raw_rate_ci[1] - narrow.raw_rate_ci[0]
    assert w_wide > w_narrow, (
        f"25 problems gave CI width {w_wide:.3f}, 200 problems {w_narrow:.3f}; "
        "the interval must reflect how many problems the evidence rests on")


def test_interval_brackets_the_point_estimate():
    spec = {("A", "s1"): 0.8, ("B", "s1"): 0.6, ("A", "s2"): 0.6, ("B", "s2"): 0.8,
            ("A", "s3"): 0.7, ("B", "s3"): 0.7}
    correct, ml, sl, pl = _grid(spec, n_problems=50, n_reps=3)
    inv = inversion_rate_paired(correct, ml, sl, pl, n_boot=300)
    for rate, (lo, hi) in ((inv.raw_rate, inv.raw_rate_ci),
                           (inv.decisive_rate, inv.decisive_rate_ci)):
        assert lo <= rate <= hi, f"{rate} outside [{lo}, {hi}]"


def test_rates_and_bounds_stay_in_the_unit_interval():
    spec = {(m, s): 0.6 for m in ("A", "B", "C") for s in ("s1", "s2")}
    inv = inversion_rate_paired(*_grid(spec, n_problems=30, n_reps=2), n_boot=200)
    for lo, hi in (inv.raw_rate_ci, inv.decisive_rate_ci):
        assert 0.0 <= lo <= hi <= 1.0


def test_no_bootstrap_by_default_leaves_the_ci_undefined():
    # The bootstrap is the expensive part; callers opt in.
    spec = {("A", "s1"): 0.8, ("B", "s1"): 0.6, ("A", "s2"): 0.6, ("B", "s2"): 0.8}
    inv = inversion_rate_paired(*_grid(spec, n_problems=20, n_reps=2))
    assert np.isnan(inv.raw_rate_ci[0]) and np.isnan(inv.decisive_rate_ci[0])


def test_bootstrap_is_deterministic_for_a_fixed_seed():
    spec = {("A", "s1"): 0.8, ("B", "s1"): 0.6, ("A", "s2"): 0.6, ("B", "s2"): 0.8}
    args = _grid(spec, n_problems=25, n_reps=2)
    a = bootstrap_inversion_ci(*args, n_boot=150, random_state=3)
    b = bootstrap_inversion_ci(*args, n_boot=150, random_state=3)
    assert a == b


def test_a_stable_ranking_gives_an_interval_pinned_at_zero():
    # One model dominates under every config: no inversions, and resampling
    # problems must not manufacture any.
    spec = {("A", "s1"): 0.95, ("B", "s1"): 0.35,
            ("A", "s2"): 0.93, ("B", "s2"): 0.33,
            ("A", "s3"): 0.94, ("B", "s3"): 0.34}
    inv = inversion_rate_paired(*_grid(spec, n_problems=60, n_reps=3), n_boot=300)
    assert inv.raw_rate == 0.0
    assert inv.raw_rate_ci == (0.0, 0.0)

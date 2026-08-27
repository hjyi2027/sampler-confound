"""Cost estimates. The budget is $25 and the failure mode is not overspending —
it is running out of credit mid-sweep, which leaves an unbalanced grid. Unbalanced
is not a noisier version of this study: variance.py assumes equal cell counts, so
a half-finished sweep yields no headline at all.
"""

from __future__ import annotations

import pytest

from samplerconfound.config import BENCHMARKS, SAMPLER_CONFIGS
from samplerconfound.pricing import PRICES, TokenProfile, cell_cost, sweep_cost

N_PER_MODEL = {
    b: len(SAMPLER_CONFIGS) * 5 * spec["n_problems"] for b, spec in BENCHMARKS.items()
}


def test_grid_share_per_model_is_what_the_design_says():
    # 6 samplers x 5 replicates x problems, per model.
    assert N_PER_MODEL["math500"] == 5000
    assert N_PER_MODEL["aime"] == 1500


def test_cell_cost_is_linear_and_bills_reasoning_as_output():
    p = TokenProfile(input_tokens=200, output_tokens=1000)
    one = cell_cost("gpt-oss-20b", p, 1)
    assert one == pytest.approx((200 * 0.07 + 1000 * 0.30) / 1e6)
    assert cell_cost("gpt-oss-20b", p, 1000) == pytest.approx(one * 1000)


def test_unpriced_model_is_an_error_not_a_zero():
    # Silently costing an unknown model at $0 is how a budget gets blown.
    with pytest.raises(KeyError, match="no price recorded"):
        cell_cost("some-new-model", TokenProfile(100, 100), 10)


def test_safety_factor_is_applied_and_visible():
    profiles = {("gpt-oss-20b", "math500"): TokenProfile(200, 300)}
    r = sweep_cost(profiles, N_PER_MODEL, safety=1.5)
    assert r["budgeted"] == pytest.approx(r["raw"] * 1.5)
    assert r["safety"] == 1.5


def test_totals_sum_over_benchmarks_per_model():
    profiles = {
        ("gpt-oss-20b", "math500"): TokenProfile(200, 300),
        ("gpt-oss-20b", "aime"): TokenProfile(250, 1400),
    }
    r = sweep_cost(profiles, N_PER_MODEL)
    expected = (
        cell_cost("gpt-oss-20b", TokenProfile(200, 300), N_PER_MODEL["math500"])
        + cell_cost("gpt-oss-20b", TokenProfile(250, 1400), N_PER_MODEL["aime"])
    )
    assert r["per_model"]["gpt-oss-20b"] == pytest.approx(expected)
    assert r["raw"] == pytest.approx(expected)


def test_the_four_cheapest_models_fit_the_budget_at_measured_rates():
    # Measured on gpt-oss-20b at reasoning_effort=low, 2026-08-27:
    # math500 ~254 output tokens, aime ~1376. Scaled per model, x1.5 safety.
    cheap = ["nemotron-lightning-3p5-30b-a3b", "gpt-oss-20b",
             "gpt-oss-120b", "deepseek-v4-flash-0731"]
    profiles = {}
    for m in cheap:
        profiles[(m, "math500")] = TokenProfile(200, 300)
        profiles[(m, "aime")] = TokenProfile(250, 1500)
    r = sweep_cost(profiles, N_PER_MODEL)
    assert r["budgeted"] < 25.0, f"over budget: ${r['budgeted']:.2f}"


def test_frontier_models_do_not_fit_and_the_estimate_says_so():
    profiles = {("kimi-k3", "math500"): TokenProfile(200, 300),
                ("kimi-k3", "aime"): TokenProfile(250, 1500)}
    assert sweep_cost(profiles, N_PER_MODEL)["budgeted"] > 25.0


def test_every_serverless_price_has_both_directions():
    for model, (pin, pout) in PRICES.items():
        assert pin > 0 and pout > 0, model
        assert pout >= pin, f"{model}: output should not be cheaper than input"

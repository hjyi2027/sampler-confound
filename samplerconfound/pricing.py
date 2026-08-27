"""What the sweep costs, in dollars, before it is run.

The budget is $25 of Fireworks credit and the grid is 31,200 generations. That is
tight enough that "we'll see how it goes" is not a plan: running out of credit
half way through leaves an UNBALANCED grid, and an unbalanced grid is not a
cheaper version of this study — `variance.py` assumes equal cell counts, which is
what makes the sums of squares orthogonal and the components estimable at all.
A half-finished sweep produces no headline, not a noisier one.

So cost is estimated up front from measured token counts, and the estimate carries
a safety factor rather than a point value.

Prices are per 1M tokens, serverless standard tier, read from
docs.fireworks.ai/serverless/pricing on 2026-08-27. They are not fetched at run
time on purpose: a price change should be a visible diff here, not a number that
moves under the budget silently.
"""

from __future__ import annotations

from dataclasses import dataclass

PRICING_DATE = "2026-08-27"

# model -> (input $/1M, output $/1M). Reasoning tokens bill as output.
PRICES: dict[str, tuple[float, float]] = {
    "nemotron-lightning-3p5-30b-a3b": (0.05, 0.20),
    "gpt-oss-20b":                    (0.07, 0.30),
    "gpt-oss-120b":                   (0.15, 0.60),
    "deepseek-v4-flash-0731":         (0.22, 0.66),
    "minimax-m3":                     (0.30, 1.20),
    "muse-glimmer-30b":               (0.35, 1.50),
    "kimi-k2p6":                      (0.95, 4.00),
    "glm-5p2":                        (1.40, 4.40),
    "deepseek-v4-pro":                (1.74, 3.48),
    "qwen3p8-max":                    (2.00, 6.00),
    "kimi-k3":                        (3.00, 15.00),
}


@dataclass
class TokenProfile:
    """Measured per-generation token counts for one model on one benchmark."""

    input_tokens: float
    output_tokens: float   # including reasoning tokens, which bill as output


def cell_cost(model: str, profile: TokenProfile, n_generations: int) -> float:
    if model not in PRICES:
        raise KeyError(f"no price recorded for {model!r} as of {PRICING_DATE}")
    p_in, p_out = PRICES[model]
    return n_generations * (
        profile.input_tokens * p_in + profile.output_tokens * p_out
    ) / 1_000_000


def sweep_cost(
    profiles: dict[tuple[str, str], TokenProfile],
    n_per_model: dict[str, int],
    safety: float = 1.5,
) -> dict:
    """Total cost across (model, benchmark) cells, with a safety factor.

    `safety` defaults to 1.5 because the measured token counts come from a
    handful of problems at one temperature, and output length is not constant
    across the grid — high-temperature samples ramble, and a reasoning model that
    wanders spends the whole budget on tokens nobody grades. Underestimating here
    costs the study its balance, so the multiplier is deliberately blunt.
    """
    per_model: dict[str, float] = {}
    for (model, benchmark), profile in profiles.items():
        per_model[model] = per_model.get(model, 0.0) + cell_cost(
            model, profile, n_per_model[benchmark]
        )
    raw = sum(per_model.values())
    return {
        "per_model": per_model,
        "raw": raw,
        "safety": safety,
        "budgeted": raw * safety,
    }

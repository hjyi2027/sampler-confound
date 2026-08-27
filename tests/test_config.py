"""The grid. These tests exist to stop a config change from silently changing the
study, which is the failure mode that produces a paper with correct arithmetic and
a wrong headline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from samplerconfound.config import (
    BENCHMARKS,
    FIXED,
    MODEL_CANDIDATES,
    N_MODEL_LEVELS,
    PILOT_BAND,
    PILOT_PROBLEM_SEED,
    SAMPLER_CONFIGS,
    BUDGET_USD,
    SAMPLER_CONFIGS,
    Design,
    affordable,
    grid_cost_usd,
    required_params,
    set_cost_usd,
    select_models,
    supports_grid,
)

ROOT = Path(__file__).resolve().parent.parent
FAM = {c["id"]: c["family"] for c in MODEL_CANDIDATES}
IDS = [c["id"] for c in MODEL_CANDIDATES]

# Synthetic ids for the rule-logic tests. They are deliberately NOT real
# candidates: select_models() drops any known model whose probed sampler support
# is incomplete, and mixing that filter into tests of the band and the spread
# would make those tests pass or fail for the wrong reason.
FAKE = [f"vendor{i}/model" for i in range(6)]
FAKE_FAM = {m: f"v{i}" for i, m in enumerate(FAKE)}


# --------------------------------------------------------------------------
# the frozen templates
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name,benchmark", [("main", "math500"), ("aime", "aime")])
def test_template_is_frozen_and_unrunnable(name, benchmark):
    t = json.loads((ROOT / "configs" / f"{name}.template.json").read_text())
    assert t["models"] == [], "a template with models filled in can be run by accident"
    assert t["benchmark"] == benchmark
    assert t["n_problems"] == BENCHMARKS[benchmark]["n_problems"]
    assert t["n_replicates"] == 5
    assert t["fixed"] == FIXED
    assert [s["id"] for s in t["samplers"]] == [s["id"] for s in SAMPLER_CONFIGS]
    with pytest.raises(ValueError):
        Design(**t).validate()


def test_pilot_problems_are_drawn_apart_from_the_sweep():
    # Selecting models on the same items they are then scored on inflates the
    # model component with selection noise.
    assert PILOT_PROBLEM_SEED not in {b["problem_seed"] for b in BENCHMARKS.values()}


def test_shortlist_is_large_enough_and_distinct():
    assert len(IDS) == len(set(IDS))
    assert len(IDS) > N_MODEL_LEVELS, "no slack if a candidate is missing from the catalog"
    assert len(set(FAM.values())) >= 3, "the model factor must not be one vendor's size ladder"


# --------------------------------------------------------------------------
# the selection rule
# --------------------------------------------------------------------------
def test_selects_the_tightest_band():
    pilot = dict(zip(FAKE, [0.60, 0.62, 0.61, 0.63, 0.80, 0.75]))
    chosen = select_models(pilot, families=FAKE_FAM)
    assert sorted(chosen) == sorted(FAKE[:4])


def test_excludes_ceiling_and_floor_even_when_they_are_tight():
    # Four models clustered at 0.97 have the tightest possible spread and are
    # exactly the set that must NOT be picked: a ceiling flattens the numerator.
    pilot = dict(zip(FAKE, [0.99, 0.99, 0.60, 0.65, 0.70, 0.68]))
    chosen = select_models(pilot, families=FAKE_FAM)
    assert all(PILOT_BAND[0] <= pilot[m] <= PILOT_BAND[1] for m in chosen)


def test_refuses_rather_than_widening_the_band():
    pilot = dict(zip(FAKE, [0.99, 0.99, 0.99, 0.10, 0.05, 0.60]))
    with pytest.raises(ValueError, match="pre-registered band"):
        select_models(pilot)


def test_family_diversity_breaks_ties():
    a, b, c, d, e, f = FAKE
    fam = {a: "x", b: "x", c: "x", d: "y", e: "z", f: "w"}
    # Several subsets tie at spread 0.0; the four-vendor one must win, because
    # the paper's claim is about model *choice*, not one vendor's size ladder.
    pilot = dict.fromkeys(FAKE, 0.70)
    chosen = select_models(pilot, families=fam)
    assert len({fam[m] for m in chosen}) == 4


def test_selection_is_deterministic():
    pilot = dict(zip(FAKE, [0.60, 0.62, 0.61, 0.63, 0.80, 0.75]))
    assert select_models(pilot, families=FAKE_FAM) == select_models(pilot, families=FAKE_FAM)


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------
def test_rejects_unknown_benchmark():
    d = Design(models=IDS[:4], benchmark="gsm8k", n_problems=200)
    with pytest.raises(ValueError, match="unknown benchmark"):
        d.validate()


def test_rejects_resized_benchmark():
    # Halving n_problems to save money changes the CIs the budget was set against.
    d = Design(models=IDS[:4], benchmark="math500", n_problems=100)
    with pytest.raises(ValueError, match="frozen at 200"):
        d.validate()


def test_generation_count_matches_the_frozen_budget():
    d = Design(models=IDS[:4], benchmark="math500", n_problems=200)
    d.validate()
    assert d.n_generations == 28_000
    a = Design(models=IDS[:4], benchmark="aime", n_problems=60, problem_seed=0)
    a.validate()
    assert a.n_generations == 8_400


# --------------------------------------------------------------------------
# measured sampler support
# --------------------------------------------------------------------------
def test_required_params_covers_every_knob_the_grid_varies():
    # min_p is gone: probing found it honoured by only three of eight models on
    # this provider, so the cell would have been a duplicate of hightemp for the
    # rest. See the note beside SAMPLER_CONFIGS.
    assert required_params() == {"temperature", "top_p", "top_k"}


def test_the_minp_cell_is_gone_and_stays_gone():
    assert "minp" not in {s["id"] for s in SAMPLER_CONFIGS}
    assert not any("min_p" in s for s in SAMPLER_CONFIGS)


def test_unprobed_model_counts_as_unsupported():
    # An unverified parameter is indistinguishable from a working one until the
    # numbers are already wrong, which is the entire reason the probe exists.
    assert not supports_grid({"id": "x", "sampler_support": None})
    assert not supports_grid({"id": "x"})


def test_partial_support_is_not_support():
    # top_p ignored means `standard` — the de facto default, and the paper's
    # motivating case — silently duplicates another cell for this model only.
    assert not supports_grid({
        "id": "x",
        "sampler_support": {"temperature": True, "top_p": False,
                            "top_k": True, "min_p": True},
    })


def test_full_support_passes():
    assert supports_grid({
        "id": "x",
        "sampler_support": dict.fromkeys(required_params(), True),
    })


def test_ignoring_min_p_no_longer_disqualifies():
    # The point of dropping the cell: models that discard min_p can still run
    # every condition the grid actually contains.
    partial = {"id": "x", "sampler_support": {"temperature": True, "top_p": True,
                                              "top_k": True, "min_p": False}}
    assert supports_grid(partial)


def test_real_candidates_are_filtered_by_probed_support():
    excluded = [c["id"] for c in MODEL_CANDIDATES if not supports_grid(c)]
    # muse-glimmer ignores top_p; minimax-m2p7 rejects temperature > 1.0.
    assert len(excluded) == 2


def test_selection_respects_the_budget():
    # Every real candidate lands in band, so only cost can separate the sets.
    pilot = {c["id"]: 0.70 for c in MODEL_CANDIDATES if supports_grid(c)}
    chosen = select_models(pilot)
    assert affordable(chosen)
    assert set_cost_usd(chosen) <= BUDGET_USD


def test_an_unaffordable_grid_fails_loudly_rather_than_silently_shrinking():
    expensive = [c["id"] for c in MODEL_CANDIDATES
                 if supports_grid(c) and grid_cost_usd(c) > 10]
    others = [c["id"] for c in MODEL_CANDIDATES
              if supports_grid(c) and grid_cost_usd(c) > 2]
    pilot = dict.fromkeys(expensive + others, 0.70)
    if len(pilot) >= 4 and not any(
        affordable(c) for c in __import__("itertools").combinations(sorted(pilot), 4)
    ):
        with pytest.raises(ValueError, match="no affordable set"):
            select_models(pilot)

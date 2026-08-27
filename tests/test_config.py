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
    Design,
    select_models,
)

ROOT = Path(__file__).resolve().parent.parent
FAM = {c["id"]: c["family"] for c in MODEL_CANDIDATES}
IDS = [c["id"] for c in MODEL_CANDIDATES]


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
    pilot = dict(zip(IDS, [0.60, 0.62, 0.61, 0.63, 0.80, 0.75]))
    chosen = select_models(pilot)
    assert sorted(chosen) == sorted(IDS[:4])


def test_excludes_ceiling_and_floor_even_when_they_are_tight():
    # Four models clustered at 0.97 have the tightest possible spread and are
    # exactly the set that must NOT be picked: a ceiling flattens the numerator.
    pilot = dict(zip(IDS, [0.99, 0.99, 0.60, 0.65, 0.70, 0.68]))
    chosen = select_models(pilot)
    assert all(PILOT_BAND[0] <= pilot[m] <= PILOT_BAND[1] for m in chosen)


def test_refuses_rather_than_widening_the_band():
    pilot = dict(zip(IDS, [0.99, 0.99, 0.99, 0.10, 0.05, 0.60]))
    with pytest.raises(ValueError, match="pre-registered band"):
        select_models(pilot)


def test_family_diversity_breaks_ties():
    a, b, c, d, e, f = IDS
    fam = {a: "x", b: "x", c: "x", d: "y", e: "z", f: "w"}
    # Several subsets tie at spread 0.0; the four-vendor one must win, because
    # the paper's claim is about model *choice*, not one vendor's size ladder.
    pilot = dict.fromkeys(IDS, 0.70)
    chosen = select_models(pilot, families=fam)
    assert len({fam[m] for m in chosen}) == 4


def test_selection_is_deterministic():
    pilot = dict(zip(IDS, [0.60, 0.62, 0.61, 0.63, 0.80, 0.75]))
    assert select_models(pilot) == select_models(pilot)


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
    assert d.n_generations == 24_000
    a = Design(models=IDS[:4], benchmark="aime", n_problems=60, problem_seed=0)
    a.validate()
    assert a.n_generations == 7_200

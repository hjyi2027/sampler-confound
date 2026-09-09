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
    chosen = select_models(pilot, k=4, families=FAKE_FAM)
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
    chosen = select_models(pilot, k=4, families=fam)
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
    # A REJECTED top_p means `standard` — the de facto default, and the paper's
    # motivating case — cannot be run at all on this model.
    assert not supports_grid({
        "id": "x",
        "sampler_support": {"temperature": "yes", "top_p": "rejected",
                            "top_k": "yes", "min_p": "yes"},
    })


def test_full_support_passes():
    assert supports_grid({
        "id": "x",
        "sampler_support": dict.fromkeys(required_params(), "yes"),
    })


def test_a_parameter_outside_the_grid_cannot_disqualify():
    # min_p is not in SAMPLER_CONFIGS, so its status is irrelevant to whether a
    # model can run the grid — even "rejected".
    partial = {"id": "x", "sampler_support": {"temperature": "yes", "top_p": "yes",
                                              "top_k": "yes", "min_p": "rejected"}}
    assert supports_grid(partial)


def test_candidates_are_excluded_on_evidence_not_on_absence_of_it():
    """The exclusion rule changed on 2026-09-09 and this test changed with it.

    muse-glimmer-30b used to be excluded for "ignoring top_p". A properly
    powered two-sample test put its top_p entropy drop at 3.68 bits, p < 0.003 —
    the heuristic had simply lacked the power to see it, and the exclusion was
    made on bad evidence. It is back in the pool.

    What still excludes a model is evidence of breakage: withdrawn from the
    catalogue, or a parameter the API rejects outright.
    """
    excluded = {c["id"].split("/")[-1] for c in MODEL_CANDIDATES if not supports_grid(c)}
    assert "gpt-oss-20b" in excluded, "withdrawn 2026-08-27"
    assert "minimax-m2p7" in excluded, "withdrawn 2026-09-09"
    assert "muse-glimmer-30b" not in excluded, (
        "excluded on a heuristic that a powered test overturned"
    )


def test_unverified_parameters_do_not_exclude_but_are_reported():
    from samplerconfound.config import unverified_params
    # nemotron-lightning is a frozen model level whose top_p effect the test
    # could not demonstrate (dH=+0.10, p=0.063). Absence of evidence must not
    # silently remove a level; it must surface as a stated limitation.
    nem = next(c for c in MODEL_CANDIDATES if "nemotron-lightning" in c["id"])
    assert supports_grid(nem)
    assert "top_p" in unverified_params(nem)


def test_a_rejected_parameter_still_disqualifies():
    assert not supports_grid({
        "id": "x",
        "sampler_support": {"temperature": "rejected", "top_p": "yes", "top_k": "yes"},
    })


def test_a_withdrawn_model_cannot_be_selected():
    # It is kept in MODEL_CANDIDATES on purpose — a benchmark model vanishing
    # from a provider mid-study is a fact this paper reports — so the guard has
    # to be `available`, not absence from the list.
    gone = next(c for c in MODEL_CANDIDATES if c.get("available") is False)
    assert not supports_grid(gone)
    assert gone.get("withdrawn")


def test_selection_never_returns_an_unaffordable_set():
    # Either it finds an affordable set or it refuses loudly. What it must never
    # do is hand back a set the account cannot pay for, which would end the sweep
    # part way and leave an unbalanced grid.
    pilot = {c["id"]: 0.70 for c in MODEL_CANDIDATES if supports_grid(c)}
    try:
        chosen = select_models(pilot)
    except ValueError as e:
        assert "no affordable set" in str(e) or "pre-registered band" in str(e)
        return
    assert affordable(chosen)
    assert set_cost_usd(chosen) <= BUDGET_USD


def test_an_unaffordable_grid_fails_loudly_rather_than_silently_shrinking():
    expensive = [c["id"] for c in MODEL_CANDIDATES
                 if supports_grid(c) and grid_cost_usd(c) > 10]
    others = [c["id"] for c in MODEL_CANDIDATES
              if supports_grid(c) and grid_cost_usd(c) > 2]
    pilot = dict.fromkeys(expensive + others, 0.70)
    k = N_MODEL_LEVELS
    if len(pilot) >= k and not any(
        affordable(c) for c in __import__("itertools").combinations(sorted(pilot), k)
    ):
        with pytest.raises(ValueError, match="no affordable set"):
            select_models(pilot)


def test_level_count_matches_the_frozen_configs():
    """N_MODEL_LEVELS and configs/ must not drift apart.

    They did: gpt-oss-20b's withdrawal left the configs at three models while
    N_MODEL_LEVELS still said four, so `make select` would have raised "only 3
    candidates landed inside the band" — a real failure reported as a band
    problem rather than as the catalogue change it was.
    """
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for name in ("main", "aime"):
        cfg = root / "configs" / f"{name}.json"
        if not cfg.exists():
            continue
        models = json.loads(cfg.read_text())["models"]
        assert len(models) == N_MODEL_LEVELS, (
            f"configs/{name}.json has {len(models)} models, "
            f"N_MODEL_LEVELS is {N_MODEL_LEVELS}"
        )


def test_enough_usable_candidates_exist_for_the_declared_level_count():
    usable = [c for c in MODEL_CANDIDATES if supports_grid(c)]
    assert len(usable) >= N_MODEL_LEVELS, (
        f"{len(usable)} candidates can run the grid but {N_MODEL_LEVELS} levels "
        "are declared; selection cannot succeed"
    )

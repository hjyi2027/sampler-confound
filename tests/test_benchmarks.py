"""Problem sets and draws.

The draw is a factor in the decomposition. A draw that shifts between the pilot
and the sweep, or between one machine and another, moves the problem component
and the residual with nothing visible in the code to explain it — so these tests
are about reproducibility and mix, not about happy-path loading.
"""

from __future__ import annotations

import collections
import hashlib
import json

import pytest

from samplerconfound import benchmarks as B
from samplerconfound.benchmarks import DATA, Problem, load, pilot_split, select, sweep_split
from samplerconfound.config import BENCHMARKS, PILOT_N_PROBLEMS, PILOT_PROBLEM_SEED

pytestmark = pytest.mark.skipif(
    not (DATA / "MANIFEST.json").exists(),
    reason="benchmarks not fetched; run scripts/fetch_benchmarks.py",
)


def _synthetic(n: int, levels: list[int] | None = None) -> list[Problem]:
    levels = levels or [1, 2, 3, 4, 5]
    return [
        Problem(id=f"p/{i:04d}", problem=f"q{i}", answer=str(i),
                level=levels[i % len(levels)], subject="alg")
        for i in range(n)
    ]


# --------------------------------------------------------------------------
# the pinned files
# --------------------------------------------------------------------------
@pytest.mark.parametrize("benchmark", sorted(BENCHMARKS))
def test_loads_and_matches_manifest(benchmark):
    problems = load(benchmark)
    manifest = json.loads((DATA / "MANIFEST.json").read_text())
    assert len(problems) == manifest["files"][benchmark]["n_problems"]
    assert all(p.problem.strip() and p.answer.strip() for p in problems)
    assert len({p.id for p in problems}) == len(problems)


def test_hash_mismatch_is_fatal(tmp_path, monkeypatch):
    # Silently accepting a changed problem set is the failure this guards.
    manifest = json.loads((DATA / "MANIFEST.json").read_text())
    text = (DATA / "math500.jsonl").read_text(encoding="utf-8")
    tampered = tmp_path / "math500.jsonl"
    tampered.write_text(text.replace("problem", "problem ", 1), encoding="utf-8")
    manifest["files"]["math500"]["path"] = "math500.jsonl"
    (tmp_path / "MANIFEST.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(B, "DATA", tmp_path)
    with pytest.raises(ValueError, match="does not match the manifest"):
        load("math500")


def test_aime_answers_are_integers_in_range():
    for p in load("aime"):
        assert p.answer.isdigit() and 0 <= int(p.answer) <= 999


def test_aime_is_both_years_evenly():
    # 2025 postdates several candidates' training cutoff; it is the half that
    # makes differential contamination visible, so it must actually be there.
    years = collections.Counter(p.id.split("/")[0] for p in load("aime"))
    assert years == {"aime2024": 30, "aime2025": 30}


def test_unknown_benchmark_rejected():
    with pytest.raises(ValueError, match="unknown benchmark"):
        load("gsm8k")


# --------------------------------------------------------------------------
# the draw
# --------------------------------------------------------------------------
def test_draw_is_reproducible_and_seed_dependent():
    ps = _synthetic(500)
    assert [p.id for p in select(ps, 200, 0)] == [p.id for p in select(ps, 200, 0)]
    assert [p.id for p in select(ps, 200, 0)] != [p.id for p in select(ps, 200, 1)]


def test_draw_does_not_depend_on_the_input_order():
    ps = _synthetic(500)
    a = [p.id for p in select(ps, 200, 0)]
    b = [p.id for p in select(list(reversed(ps)), 200, 0)]
    assert a == b


# The exact problems the paper reports on. Pinned so that a change to _rank, to
# the stratification, or to a seed fails here rather than in a rerun six months
# later that quietly disagrees with the published numbers.
SPLIT_DIGESTS = {
    ("sweep", "math500"): "5cdb518f18f09988848b7694d853168ec0a0d8b2c0cbdbf88c759b1903e49064",
    ("sweep", "aime"):    "2633b524c2bc83c996c52b5460ffd7aef572f312e4303f00dd0fdd42e3f89631",
    ("pilot", "math500"): "28ad1a3d706466af1852280a7f14c5d6c1ab59728950fbe1ff2c10d3f822ce93",
}


def _digest(problems) -> str:
    return hashlib.sha256("\n".join(p.id for p in problems).encode()).hexdigest()


@pytest.mark.parametrize("benchmark", sorted(BENCHMARKS))
def test_sweep_draw_is_pinned(benchmark):
    assert _digest(sweep_split(benchmark)) == SPLIT_DIGESTS[("sweep", benchmark)]


def test_pilot_draw_is_pinned():
    assert _digest(pilot_split()) == SPLIT_DIGESTS[("pilot", "math500")]


def test_stratification_preserves_the_level_mix():
    full = load("math500")
    drawn = sweep_split("math500")
    fc = collections.Counter(p.level for p in full)
    dc = collections.Counter(p.level for p in drawn)
    for lv in fc:
        # Difficulty decides whether a cell sits near ceiling or floor, and both
        # flatten the variance being measured. Within one problem of exact.
        assert abs(dc[lv] - len(drawn) * fc[lv] / len(full)) <= 1.0


def test_strata_quotas_sum_exactly():
    for n in (7, 13, 100, 199, 200, 333):
        assert len(select(_synthetic(500), n, 0)) == n


def test_unstratified_when_no_levels():
    ps = [Problem(id=f"x/{i}", problem="q", answer="1") for i in range(60)]
    assert len(select(ps, 10, 0)) == 10


def test_select_rejects_oversized_draw():
    with pytest.raises(ValueError, match="only 60 available"):
        select(_synthetic(60), 61, 0)


# --------------------------------------------------------------------------
# the study's splits
# --------------------------------------------------------------------------
@pytest.mark.parametrize("benchmark", sorted(BENCHMARKS))
def test_sweep_split_matches_the_frozen_config(benchmark):
    assert len(sweep_split(benchmark)) == BENCHMARKS[benchmark]["n_problems"]


def test_aime_sweep_is_the_whole_benchmark():
    assert {p.id for p in sweep_split("aime")} == {p.id for p in load("aime")}


def test_pilot_is_disjoint_from_the_sweep():
    # Selecting models on the items they are then scored on pushes selection
    # noise into the model component — the headline ratio's denominator.
    assert not ({p.id for p in pilot_split()} & {p.id for p in sweep_split("math500")})


def test_pilot_is_sized_and_seeded_as_registered():
    assert len(pilot_split()) == PILOT_N_PROBLEMS
    assert PILOT_PROBLEM_SEED != BENCHMARKS["math500"]["problem_seed"]


def test_pilot_keeps_the_level_mix_too():
    # If the pilot skewed easy, every candidate would score near ceiling and the
    # selection band would reject the whole shortlist.
    full = load("math500")
    pilot = pilot_split()
    fc = collections.Counter(p.level for p in full)
    pc = collections.Counter(p.level for p in pilot)
    for lv in fc:
        assert abs(pc[lv] / len(pilot) - fc[lv] / len(full)) < 0.05


def test_splits_are_stable_across_calls():
    assert [p.id for p in pilot_split()] == [p.id for p in pilot_split()]
    assert [p.id for p in sweep_split("math500")] == [p.id for p in sweep_split("math500")]

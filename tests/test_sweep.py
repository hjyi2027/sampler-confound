"""The runner's correctness properties, which are not about generation.

Balance and resume are the two things that decide whether a finished run is
usable. Both fail silently if wrong: an unbalanced grid still produces numbers,
and a broken resume still produces a file.
"""

from __future__ import annotations

import json

import pytest

from scripts.analyse import check_balance
from scripts.run_sweep import build_jobs, cell_key, design_fingerprint, load_done


class _P:
    def __init__(self, pid):
        self.id = pid
        self.problem = "q"
        self.answer = "1"


class _D:
    def __init__(self, models, samplers, reps):
        self.models = models
        self.samplers = [{"id": s, "temperature": 0.7} for s in samplers]
        self.n_replicates = reps


def _records(models, samplers, reps, problems, drop=()):
    out = []
    for m in models:
        for s in samplers:
            for r in range(reps):
                for p in problems:
                    if (m, s, r, p) in drop:
                        continue
                    out.append({"model": m, "sampler": s, "replicate": r,
                                "problem_id": p, "verdict": {"status": "correct"}})
    return out


# --------------------------------------------------------------------------
# resume
# --------------------------------------------------------------------------
def test_finished_cells_are_not_rerun(tmp_path):
    d = _D(["m1", "m2"], ["greedy", "topk"], 2)
    problems = [_P("p1"), _P("p2")]
    f = tmp_path / "r.jsonl"
    f.write_text(json.dumps({"model": "m1", "sampler": "greedy",
                             "replicate": 0, "problem_id": "p1"}) + "\n")
    done = load_done(f)
    jobs = build_jobs(d, problems, done)
    assert len(jobs) == 2 * 2 * 2 * 2 - 1
    assert not any(j[0] == "m1" and j[1]["id"] == "greedy" and j[2] == 0
                   and j[3].id == "p1" for j in jobs)


def test_a_torn_final_line_is_redone_not_crashed_on(tmp_path):
    # A run killed mid-write leaves a partial JSON line. Resuming must treat that
    # cell as unfinished rather than aborting the whole restart.
    f = tmp_path / "r.jsonl"
    f.write_text(
        json.dumps({"model": "m1", "sampler": "greedy",
                    "replicate": 0, "problem_id": "p1"}) + "\n"
        + '{"model": "m1", "sampler": "gre'
    )
    done = load_done(f)
    assert len(done) == 1
    assert cell_key("m1", "greedy", 0, "p1") in done


def test_resume_from_nothing_runs_the_whole_grid(tmp_path):
    d = _D(["m1", "m2"], ["greedy"], 3)
    jobs = build_jobs(d, [_P("p1"), _P("p2")], load_done(tmp_path / "missing.jsonl"))
    assert len(jobs) == 2 * 1 * 3 * 2


# --------------------------------------------------------------------------
# load spreading
# --------------------------------------------------------------------------
def test_jobs_are_interleaved_across_models():
    # The provider rate-limits per model. Consecutive jobs on one model saturate
    # it while others idle, and the resulting failures cluster on a single cell —
    # which is exactly the pattern that unbalances the grid.
    d = _D(["m1", "m2", "m3"], ["greedy"], 2)
    jobs = build_jobs(d, [_P(f"p{i}") for i in range(4)], {})
    first = [j[0] for j in jobs[:6]]
    assert len(set(first)) == 3, f"not spread across models: {first}"


# --------------------------------------------------------------------------
# balance
# --------------------------------------------------------------------------
def test_balanced_grid_passes():
    recs = _records(["m1", "m2"], ["greedy", "topk"], 2, ["p1", "p2"])
    models, samplers, problems, reps = check_balance(recs)
    assert (len(models), len(samplers), len(problems), reps) == (2, 2, 2, 2)


def test_missing_cell_is_refused():
    recs = _records(["m1", "m2"], ["greedy", "topk"], 2, ["p1", "p2"],
                    drop={("m2", "topk", 1, "p2")})
    with pytest.raises(SystemExit, match="unbalanced grid"):
        check_balance(recs)


def test_duplicate_cell_is_refused():
    # Two records for one cell double-count it. Appending on resume without the
    # skip check would produce exactly this.
    recs = _records(["m1", "m2"], ["greedy", "topk"], 2, ["p1", "p2"])
    recs.append(dict(recs[0]))
    with pytest.raises(SystemExit, match="unbalanced grid"):
        check_balance(recs)


# --------------------------------------------------------------------------
# design fingerprint
#
# The guard's whole purpose is a COMPLETE grid whose protocol changed
# underneath it: nothing is left to run, verify() says "balanced", and the file
# quietly holds two different experiments. The first version of this check ran
# after the no-jobs early return and so missed exactly that case.
# --------------------------------------------------------------------------
def _design(max_tokens=8192, prompt="Solve it."):
    class D:
        models = ["m1", "m2"]
        samplers = [{"id": "greedy", "temperature": 0.0}]
        n_replicates = 2
        benchmark = "math500"
        n_problems = 200
        fixed = {"prompt_template": prompt, "max_tokens": max_tokens}
    return D()


def test_fingerprint_changes_with_max_tokens():
    assert design_fingerprint(_design()) != design_fingerprint(_design(max_tokens=4096))


def test_fingerprint_changes_with_the_prompt():
    assert design_fingerprint(_design()) != design_fingerprint(_design(prompt="Other."))


def test_fingerprint_is_stable_for_the_same_design():
    assert design_fingerprint(_design()) == design_fingerprint(_design())


def test_fingerprint_changes_with_a_sampler_definition():
    a = _design()
    b = _design()
    b.samplers = [{"id": "greedy", "temperature": 0.1}]
    assert design_fingerprint(a) != design_fingerprint(b)

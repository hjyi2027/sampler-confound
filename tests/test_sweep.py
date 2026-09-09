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


# --------------------------------------------------------------------------
# concurrency lock
#
# Nothing prevented two sweeps writing one output file. Both would snapshot the
# same `done` set at startup, decide on the same remaining work, and bill every
# cell twice — an easy mistake over a twenty-hour run and an expensive one.
#
# Ordering is the whole point. The lock must be taken BEFORE `done` is read: a
# lock acquired afterwards leaves open exactly the window it exists to close.
# The first version was also placed after the no-jobs early return, so a complete
# grid skipped the check entirely.
# --------------------------------------------------------------------------
import os

from scripts.run_sweep import RunLock, _pid_alive


def test_lock_is_taken_and_released(tmp_path):
    out = tmp_path / "r.jsonl"
    lock = out.with_suffix(out.suffix + ".lock")
    with RunLock(out):
        assert lock.exists()
        assert int(lock.read_text().strip()) == os.getpid()
    assert not lock.exists()


def test_a_live_lock_refuses_a_second_run(tmp_path):
    out = tmp_path / "r.jsonl"
    with RunLock(out):
        with pytest.raises(SystemExit, match="already writing"):
            with RunLock(out):
                pass


def test_a_stale_lock_does_not_block_the_resume_it_protects(tmp_path):
    # SIGKILL leaves the lockfile behind with no chance to clean up, so the
    # common case after the failure this guards IS a stale lock. Blocking on it
    # would turn a recoverable interruption into a manual one.
    out = tmp_path / "r.jsonl"
    lock = out.with_suffix(out.suffix + ".lock")
    lock.write_text("999999\n")          # a pid that cannot be running
    with RunLock(out):
        assert int(lock.read_text().strip()) == os.getpid()
    assert not lock.exists()


def test_a_corrupt_lockfile_is_treated_as_stale(tmp_path):
    out = tmp_path / "r.jsonl"
    lock = out.with_suffix(out.suffix + ".lock")
    lock.write_text("not-a-pid\n")
    with RunLock(out):
        assert int(lock.read_text().strip()) == os.getpid()


def test_lock_is_released_even_when_the_run_raises(tmp_path):
    out = tmp_path / "r.jsonl"
    lock = out.with_suffix(out.suffix + ".lock")
    with pytest.raises(RuntimeError):
        with RunLock(out):
            raise RuntimeError("boom")
    assert not lock.exists(), "a crashed run must not leave a lock blocking resume"


def test_pid_alive_recognises_this_process_and_not_a_fake_one():
    assert _pid_alive(os.getpid())
    assert not _pid_alive(999999)

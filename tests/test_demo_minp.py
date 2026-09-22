"""The 'ignored' construction is literal: an ignoring backend answers the
minp request with a hightemp draw, so the grid swaps in fresh hightemp
replicates — never a copy of the ones already in the hightemp cell."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.demo_minp_confound import IGNORED_REPS, N_REPS, grid


def _rec(model, sampler, rep, pid, ok):
    return {"model": f"accounts/fireworks/models/{model}", "sampler": sampler, "replicate": rep,
            "problem_id": pid, "verdict": {"status": "correct" if ok else "incorrect"}}


def test_ignored_model_gets_fresh_hightemp_draws_relabelled_and_no_real_minp():
    smoke = [_rec("m1", "hightemp", r, "p", True) for r in range(N_REPS)]
    extra = ([_rec("m1", "minp", r, "p", False) for r in range(N_REPS)]
             + [_rec("m1", "hightemp", r, "p", True) for r in IGNORED_REPS])
    honest = grid(smoke, extra, ["m1"], ignored=set())
    assert sum(r["sampler"] == "minp" for r in honest) == N_REPS
    assert all(r["verdict"]["status"] == "incorrect" for r in honest if r["sampler"] == "minp")
    assert sum(r["sampler"] == "hightemp" for r in honest) == N_REPS   # reps 5-9 stay out
    ignored = grid(smoke, extra, ["m1"], ignored={"m1"})
    minp = [r for r in ignored if r["sampler"] == "minp"]
    assert len(minp) == N_REPS and sorted(r["replicate"] for r in minp) == list(range(N_REPS))
    assert all(r["verdict"]["status"] == "correct" for r in minp), "the ignoring backend's answers"
    assert sum(r["sampler"] == "hightemp" for r in ignored) == N_REPS

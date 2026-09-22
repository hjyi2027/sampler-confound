#!/usr/bin/env python3
"""What a silently ignored min_p does to the study, shown on the smoke corpus.

The design's `minp` cell is {temperature 1.0, min_p 0.05} and its `hightemp`
cell is {temperature 1.0, top_p 1.0}. On a backend that applies min_p they are
two conditions. On a backend that accepts min_p and ignores it they are THE
SAME CONDITION RUN TWICE — the minp cell is a fresh draw from the hightemp
distribution. If some models in a grid are served by the first kind of backend
and some by the second, the minp − hightemp contrast is real for some models
and zero for others, and the two-way decomposition books that as a
model × sampler interaction. Nothing in the accuracy table distinguishes it
from a genuine one.

This script does not simulate that. It builds both cases from real
generations on the smoke run's models and problems:

    honoured   a real minp cell (Fireworks applies min_p on every model probed)
    ignored    five more hightemp replicates — exactly what an ignoring backend
               returns for the minp request, because that is what "ignored"
               means

and runs the study's own analysis (variance.decompose_accuracy, the
model × sampler share and the sampler/model ratio; inversion_rate_paired) on:

    A   every model honoured               the honest grid
    B_k model k ignored, the others not    one silent backend in the mix
    C   every model ignored                uniform, so no interaction is made

Collection goes through the sweep's own generate() — same prompt, same fixed
settings, cached under the request hash — so the new cells are of a piece
with the smoke corpus. About 300 generations.

    python3 scripts/demo_minp_confound.py            # collect if needed, then analyse
    SAMPLERCONFOUND_OFFLINE=1 python3 scripts/demo_minp_confound.py   # analyse only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.benchmarks import sweep_split
from samplerconfound.config import Design, FIXED
from samplerconfound.inversion import inversion_rate_paired
from samplerconfound.paths import iso, resolve_out, show
from samplerconfound.provider import load_key
from samplerconfound.variance import decompose_accuracy
from scripts.run_sweep import generate

SMOKE = ROOT / "runs" / "smoke" / "math500.jsonl"
EXTRA = ROOT / "runs" / "smoke" / "minp_confound.jsonl"
OUT = ROOT / "runs" / "smoke" / "minp_confound.json"

MINP = {"id": "minp", "temperature": 1.0, "min_p": 0.05}
HIGHTEMP = {"id": "hightemp", "temperature": 1.0, "top_p": 1.0}
N_REPS = 5
IGNORED_REPS = range(N_REPS, 2 * N_REPS)          # hightemp replicates 5..9 stand in for "ignored minp"


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def collect(models: list[str], problems, workers: int) -> list[dict]:
    """The two extra cells, resumable: what is already in EXTRA is kept."""
    have = load(EXTRA)
    done = {(r["model"], r["sampler"], r["replicate"], r["problem_id"]) for r in have}
    design = Design(models=models, samplers=[MINP, HIGHTEMP], n_replicates=2 * N_REPS,
                    benchmark="math500", n_problems=len(problems), fixed=dict(FIXED))
    jobs = []
    for m in models:
        for p in problems:
            for rep in range(N_REPS):
                if (m, "minp", rep, p.id) not in done:
                    jobs.append((m, MINP, rep, p))
            for rep in IGNORED_REPS:
                if (m, "hightemp", rep, p.id) not in done:
                    jobs.append((m, HIGHTEMP, rep, p))
    if not jobs:
        print(f"extra cells complete: {len(have)} records in {show(EXTRA)}")
        return have
    key = load_key()
    print(f"collecting {len(jobs)} generations for the minp and extra hightemp cells")
    t0 = time.time()
    with EXTRA.open("a") as fh, ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(generate, key, design, m, s, rep, p): (m, s["id"], rep, p.id)
                for m, s, rep, p in jobs}
        for i, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            if rec:
                fh.write(json.dumps(rec) + "\n"); fh.flush()
                have.append(rec)
            if i % 25 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)}  {(time.time() - t0) / 60:.1f} min", flush=True)
    return have


# --------------------------------------------------------------------------

def cell_accuracy(records: list[dict]) -> dict[tuple[str, str, int], float]:
    """accuracy per (model, sampler, replicate) over the problem set — the
    number a paper reports for that cell."""
    by = defaultdict(list)
    for r in records:
        by[(r["model"].split("/")[-1], r["sampler"], r["replicate"])].append(
            r["verdict"]["status"] == "correct")
    return {k: sum(v) / len(v) for k, v in by.items()}


def problem_correct(records: list[dict]) -> dict[tuple[str, str, str], float]:
    """per (model, sampler, problem): mean over replicates — for the inversion metric."""
    by = defaultdict(list)
    for r in records:
        by[(r["model"].split("/")[-1], r["sampler"], r["problem_id"])].append(
            r["verdict"]["status"] == "correct")
    return {k: sum(v) / len(v) for k, v in by.items()}


def grid(smoke: list[dict], extra: list[dict], models: list[str], ignored: set[str]):
    """The seven-cell grid per model, with minp real or 'ignored' per model."""
    base = [r for r in smoke if r["model"].split("/")[-1] in models]
    rows = list(base)
    for r in extra:
        m = r["model"].split("/")[-1]
        if m not in models:
            continue
        if r["sampler"] == "minp" and m not in ignored:
            rows.append(r)
        elif r["sampler"] == "hightemp" and r["replicate"] in IGNORED_REPS and m in ignored:
            # the ignoring backend's answer to the minp request
            rows.append({**r, "sampler": "minp", "replicate": r["replicate"] - N_REPS})
    return rows


def analyse(rows: list[dict], n_boot: int, seed: int = 0) -> dict:
    acc = cell_accuracy(rows)
    keys = sorted(acc)
    tw = decompose_accuracy([acc[k] for k in keys], [k[0] for k in keys], [k[1] for k in keys],
                            n_boot=n_boot, random_state=seed)
    pc = problem_correct(rows)
    pk = sorted(pc)
    inv = inversion_rate_paired([pc[k] for k in pk], [k[0] for k in pk], [k[1] for k in pk],
                                [k[2] for k in pk], n_boot=0)
    contrast = {}
    for m in sorted({k[0] for k in keys}):
        mp = [acc[(m, "minp", r)] for r in range(N_REPS) if (m, "minp", r) in acc]
        ht = [acc[(m, "hightemp", r)] for r in range(N_REPS) if (m, "hightemp", r) in acc]
        contrast[m] = (float(np.mean(mp) - np.mean(ht)), float(np.mean(mp)), float(np.mean(ht)))
    return {"var_share": tw.var_share, "sampler_to_model": tw.sampler_to_model,
            "interaction_share": tw.var_share.get("model:sampler", float("nan")),
            "inversion_rate": inv.raw_rate, "decisive_rate": inv.decisive_rate,
            "n_inversions": inv.n_raw, "n_decisive": inv.n_decisive, "n_comparisons": inv.n_comparisons,
            "minp_minus_hightemp": contrast}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--n-boot", type=int, default=500)
    args = ap.parse_args()

    smoke = load(SMOKE)
    if not smoke:
        raise SystemExit(f"no smoke corpus at {show(SMOKE)}")
    all_models = sorted({r["model"] for r in smoke})
    # gpt-oss-20b was withdrawn from the provider on 2026-08-27; it has no minp cell and cannot get one
    models = [m for m in all_models if not m.endswith("gpt-oss-20b")]
    pids = sorted({r["problem_id"] for r in smoke})
    problems = [p for p in sweep_split("math500") if p.id in set(pids)]
    assert len(problems) == len(pids), "smoke problems must come from the sweep split"

    extra = collect(models, problems, args.workers)
    short = [m.split("/")[-1] for m in models]

    print(f"\n{len(short)} models x 6 samplers x {N_REPS} replicates x {len(problems)} problems")
    scenarios = {"A: all honoured": set()}
    for m in short:
        scenarios[f"B: {m} ignores min_p"] = {m}
    scenarios["C: all ignore"] = set(short)
    for m in short:
        # the manufactured direction: a uniform grid (C) with one model served
        # by a backend that applies min_p
        scenarios[f"D: only {m} honours min_p"] = set(short) - {m}
    results = {}
    print(f"\n{'grid':<40}{'model:sampler':>14}{'sampler/model':>14}{'inversions':>11}   minp - hightemp per model")
    for name, ignored in scenarios.items():
        rows = grid(smoke, extra, short, ignored)
        res = analyse(rows, args.n_boot)
        results[name] = res
        c = "  ".join(f"{m[:12]} {d:+.2f}" for m, (d, _, _) in res["minp_minus_hightemp"].items())
        print(f"{name:<40}{res['interaction_share']:>13.1%} {res['sampler_to_model']:>13.2f} "
              f"{res['inversion_rate']:>10.1%}   {c}")

    # ---- the point, stated ------------------------------------------------
    a = results["A: all honoured"]["interaction_share"]
    bs = {n: r["interaction_share"] for n, r in results.items() if n.startswith("B")}
    ds = {n: r["interaction_share"] for n, r in results.items() if n.startswith("D")}
    c = results["C: all ignore"]["interaction_share"]
    print(f"\nmodel x sampler share — honest grid {a:.1%}; one model's backend ignores min_p: "
          f"{min(bs.values()):.1%} to {max(bs.values()):.1%}; every backend ignores it: {c:.1%}; "
          f"every backend ignores it but one: {min(ds.values()):.1%} to {max(ds.values()):.1%}.")
    print("A and each B differ only in which backend served one model's minp request; C and each D "
          "likewise. The accuracy table carries no trace of which grid it is.")

    OUT.write_text(json.dumps({
        "written": iso(time.time()),
        "models": short, "problems": pids, "n_replicates": N_REPS,
        "cells": {"minp": MINP, "hightemp": HIGHTEMP},
        "ignored_construction": "hightemp replicates 5-9 relabelled as minp replicates 0-4",
        "collected": {"from": min((r.get("collected_at", "") for r in extra if r.get("collected_at")), default=""),
                      "to": max((r.get("collected_at", "") for r in extra if r.get("collected_at")), default="")},
        "scenarios": {k: {**v, "ignored": sorted(scenarios[k])} for k, v in results.items()},
    }, indent=2, default=str) + "\n")
    print(f"wrote {show(OUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

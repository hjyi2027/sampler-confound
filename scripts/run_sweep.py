#!/usr/bin/env python3
"""Run the grid. Cached, resumable, and balanced or it does not report.

The smoke run and the real sweep are the same code path with a different
`--scale`. A smoke test that exercised a separate simplified runner would prove
nothing about the runner that spends the budget.

Three properties matter more than speed here.

**Resumable.** Results are appended to JSONL keyed by
(model, sampler, replicate, problem). On restart, finished cells are skipped.
A 26,000-generation run takes hours; it will be interrupted.

**Balanced.** `variance.py` assumes every (model, sampler) cell holds the same
number of replicates — that is what makes the sums of squares orthogonal and the
components estimable. A run that quietly drops failures is not a noisier version
of this study, it is a broken one, so `--verify` refuses to pass an incomplete
grid and names the missing cells rather than reporting numbers from them.

**Spread across models.** The provider rate-limits per model. Saturating one
model while another idles is how the first grader-check run lost 35% of its
generations, all from the same (model, sampler) pair — failures that cluster on
a cell are precisely the ones that unbalance the grid. Jobs are interleaved
round-robin by model.

    python3 scripts/run_sweep.py --config configs/smoke.json --scale 20
    python3 scripts/run_sweep.py --config configs/smoke.json --verify
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.benchmarks import sweep_split
from samplerconfound.config import Design
from samplerconfound.grade import grade
from samplerconfound.paths import resolve_out, show

BASE = "https://api.fireworks.ai/inference/v1/chat/completions"
MAX_RETRIES = 6
BACKOFF_BASE = 2.0

_lock = threading.Lock()


def load_key() -> str:
    key = os.environ.get("FIREWORKS_API_KEY")
    if not key and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("FIREWORKS_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        raise SystemExit("no FIREWORKS_API_KEY (env or .env)")
    return key


def cell_key(model: str, sampler: str, replicate: int, problem_id: str) -> str:
    return f"{model}|{sampler}|{replicate}|{problem_id}"


def load_done(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    done = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn final line from a killed run; it will be redone
        done[cell_key(r["model"], r["sampler"], r["replicate"], r["problem_id"])] = r
    return done


def build_jobs(design: Design, problems, done: dict) -> list[tuple]:
    jobs = []
    for model in design.models:
        for sampler in design.samplers:
            for rep in range(design.n_replicates):
                for p in problems:
                    k = cell_key(model, sampler["id"], rep, p.id)
                    if k not in done:
                        jobs.append((model, sampler, rep, p))
    # Round-robin by model so concurrent workers spread across the provider's
    # per-model limits instead of hammering one.
    by_model = defaultdict(list)
    for j in jobs:
        by_model[j[0]].append(j)
    interleaved, i = [], 0
    while any(by_model.values()):
        for m in list(by_model):
            if by_model[m]:
                interleaved.append(by_model[m].pop())
        i += 1
    return interleaved


def generate(key: str, design: Design, model: str, sampler: dict, rep: int, p) -> dict | None:
    fixed = design.fixed
    body = {
        "model": model,
        "messages": [{"role": "user",
                      "content": f"{fixed['prompt_template']}\n\n{p.problem}"}],
        "max_tokens": fixed["max_tokens"],
        **{k: v for k, v in sampler.items() if k != "id"},
    }
    if fixed.get("reasoning_effort"):
        body["reasoning_effort"] = fixed["reasoning_effort"]
    if fixed.get("system_prompt"):
        body["messages"].insert(0, {"role": "system", "content": fixed["system_prompt"]})

    r = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(BASE, headers={"Authorization": f"Bearer {key}"},
                              json=body, timeout=300)
        except requests.RequestException:
            if attempt == MAX_RETRIES - 1:
                return None
            time.sleep(BACKOFF_BASE * 2 ** attempt + random.uniform(0, 1))
            continue
        if r.status_code == 200:
            break
        if r.status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
            time.sleep(BACKOFF_BASE * 2 ** attempt + random.uniform(0, 1))
            continue
        with _lock:
            print(f"  ! HTTP {r.status_code} {model.split('/')[-1]}/{sampler['id']}: "
                  f"{r.text[:120]}")
        return None
    if r is None or r.status_code != 200:
        return None

    d = r.json()
    choice = d["choices"][0]
    text = choice["message"].get("content") or ""
    truncated = choice["finish_reason"] == "length"
    v = grade(text, p.answer, truncated=truncated)
    return {
        "model": model, "sampler": sampler["id"], "replicate": rep,
        "problem_id": p.id, "benchmark": design.benchmark, "gold": p.answer,
        "response": text, "finish_reason": choice["finish_reason"],
        "input_tokens": d["usage"]["prompt_tokens"],
        "output_tokens": d["usage"]["completion_tokens"],
        "verdict": v.to_dict(),
    }


def verify(design: Design, problems, done: dict) -> bool:
    """Is every cell complete? Names what is missing rather than averaging over it."""
    expected = Counter()
    for model in design.models:
        for sampler in design.samplers:
            expected[(model, sampler["id"])] = design.n_replicates * len(problems)
    actual = Counter()
    for r in done.values():
        actual[(r["model"], r["sampler"])] += 1

    missing = {c: expected[c] - actual[c] for c in expected if actual[c] != expected[c]}
    print(f"\ngrid: {len(design.models)} models x {len(design.samplers)} samplers "
          f"x {design.n_replicates} replicates x {len(problems)} problems "
          f"= {sum(expected.values()):,} cells")
    if not missing:
        print("balanced: every cell complete")
        return True
    print(f"UNBALANCED — {len(missing)} incomplete cells, {sum(missing.values())} "
          "generations short:")
    for (m, s), n in sorted(missing.items(), key=lambda kv: -kv[1])[:20]:
        print(f"  {m.split('/')[-1]:<34} {s:<9} short {n}")
    print("\nThe decomposition assumes equal cell counts; running it on this grid "
          "would produce numbers that look fine and are wrong. Re-run to fill.")
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--scale", type=int, default=1,
                    help="run 1/N of the problems (smoke runs); models, samplers "
                         "and replicates are never scaled — the design's shape is "
                         "what the smoke run exists to exercise")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--verify", action="store_true", help="check balance and exit")
    args = ap.parse_args()

    design = Design.load(args.config)
    problems = sweep_split(design.benchmark)
    if args.scale > 1:
        problems = problems[:: args.scale]

    out = args.out or ROOT / "runs" / args.config.stem / f"{design.benchmark}.jsonl"
    out = resolve_out(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = load_done(out)

    if args.verify:
        return 0 if verify(design, problems, done) else 1

    jobs = build_jobs(design, problems, done)
    total = design.n_replicates * len(problems) * len(design.models) * len(design.samplers)
    print(f"{show(out)}: {len(done)}/{total} done, {len(jobs)} to run")
    if not jobs:
        return 0 if verify(design, problems, done) else 1

    key = load_key()
    t0 = time.time()
    written = failed = 0
    with out.open("a") as fh, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(generate, key, design, m, s, rep, p): (m, s, rep, p)
                for m, s, rep, p in jobs}
        for i, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            with _lock:
                if rec:
                    fh.write(json.dumps(rec) + "\n")
                    fh.flush()          # crash-safe: resume loses at most one line
                    written += 1
                else:
                    failed += 1
                if i % 50 == 0 or i == len(jobs):
                    rate = i / max(time.time() - t0, 1e-9)
                    eta = (len(jobs) - i) / rate if rate else 0
                    print(f"  {i}/{len(jobs)}  {rate:.1f}/s  eta {eta/60:.1f}m  "
                          f"failed {failed}")

    print(f"\nwrote {written}, failed {failed}, elapsed {(time.time()-t0)/60:.1f}m")
    return 0 if verify(design, problems, load_done(out)) else 1


if __name__ == "__main__":
    raise SystemExit(main())

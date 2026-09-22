#!/usr/bin/env python3
"""Generate a graded corpus for grader verification. Not the sweep harness.

This exists only to feed `scripts/verify_grader.py`, which needs real model
output and has never had any. It is deliberately small and deliberately skewed:
the point is to populate the rare strata the verification cares about —
unparseable responses, `last_number` fallbacks, numeric-tolerance matches — and
those come from hard problems at high temperature, not from a representative
sample. A representative sample would spend the whole budget confirming that
"Answer: 42" equals 42.

Problems are drawn from the PILOT split, never the sweep split, so nothing here
touches the problems the study reports on.

    python3 scripts/sample_for_grader_check.py --out runs/grader_check/graded.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.benchmarks import pilot_split, sweep_split
from samplerconfound.config import FIXED, SAMPLER_CONFIGS
from samplerconfound.grade import grade
from samplerconfound.paths import iso
from samplerconfound.provider import complete, load_key

MODELS = [
    "accounts/fireworks/models/gpt-oss-20b",
    "accounts/fireworks/models/nemotron-lightning-3p5-30b-a3b",
]

_print_lock = threading.Lock()


def generate(key: str, model: str, sampler: dict, problem) -> dict | None:
    params = {k: v for k, v in sampler.items() if k != "id"}
    body = {
        "model": model,
        "messages": [{"role": "user",
                      "content": f"{FIXED['prompt_template']}\n\n{problem.problem}"}],
        "max_tokens": FIXED["max_tokens"],
        "reasoning_effort": FIXED["reasoning_effort"],
        **params,
    }
    # Grader-check generations are one replicate each; the replicate index is
    # the sampler's position so distinct samplers with equal bodies cannot
    # collide (they cannot — the body differs — but the key is explicit).
    c, err = complete(key, body, 0)
    if err:
        with _print_lock:
            print(f"  ! {model.split('/')[-1]}/{sampler['id']}/{problem.id}: {err[:100]}")
        return None
    verdict = grade(c.text, problem.answer, truncated=c.truncated)
    return {
        "problem_id": problem.id,
        "benchmark": "aime" if problem.id.startswith("aime") else "math500",
        "model": model,
        "sampler": sampler["id"],
        "gold": problem.answer,
        "response": c.text,
        "finish_reason": c.finish_reason,
        "collected_at": iso(c.collected_at),
        "output_tokens": c.completion_tokens,
        "verdict": verdict.to_dict(),
    }


def regrade(path: Path) -> int:
    """Re-score a stored corpus in place, reporting what moved.

    Necessary whenever grade.py changes: the verdicts written at generation time
    are stale, and a verification run against stale verdicts measures a grader
    that no longer exists. Responses are stored verbatim precisely so this costs
    nothing and can be repeated.
    """
    records = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    moved = []
    for r in records:
        before = r["verdict"]
        after = grade(r["response"], r["gold"],
                      truncated=r.get("finish_reason") == "length").to_dict()
        if before["status"] != after["status"] or before.get("extracted") != after.get("extracted"):
            moved.append((r, before, after))
        r["verdict"] = after
    path.write_text("".join(json.dumps(r) + "\n" for r in records))

    print(f"regraded {len(records)} records; {len(moved)} changed")
    for r, b, a in moved[:25]:
        print(f"  {r['sampler']:<9} {r['problem_id']:<26} "
              f"{b['status']}->{a['status']}  {b.get('extracted')!r} -> {a.get('extracted')!r}")
    if len(moved) > 25:
        print(f"  ... and {len(moved) - 25} more")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("runs/grader_check/graded.jsonl"))
    ap.add_argument("--n-math", type=int, default=15)
    ap.add_argument("--n-aime", type=int, default=10)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--regrade", action="store_true",
                    help="re-score an existing corpus from its stored responses, "
                         "no API calls; use after any change to grade.py")
    args = ap.parse_args()

    out = args.out if args.out.is_absolute() else ROOT / args.out
    if args.regrade:
        return regrade(out)

    key = load_key()
    # Pilot problems for MATH-500 (never the sweep draw). AIME has no spare
    # problems at all — all 60 are in the sweep — so grader verification borrows
    # them. That is harmless: verification does not select models or tune the
    # grader against outcomes, it only checks that the parser agrees with a human.
    problems = pilot_split()[: args.n_math] + sweep_split("aime")[: args.n_aime]

    jobs = [(m, s, p) for m in MODELS for s in SAMPLER_CONFIGS for p in problems]
    print(f"{len(jobs)} generations: {len(MODELS)} models x {len(SAMPLER_CONFIGS)} "
          f"samplers x {len(problems)} problems")

    out.parent.mkdir(parents=True, exist_ok=True)

    records, done = [], 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(generate, key, m, s, p): (m, s, p) for m, s, p in jobs}
        for fut in as_completed(futures):
            rec = fut.result()
            done += 1
            if rec:
                records.append(rec)
            if done % 25 == 0:
                with _print_lock:
                    print(f"  {done}/{len(jobs)}")

    records.sort(key=lambda r: (r["model"], r["sampler"], r["problem_id"]))
    out.write_text("".join(json.dumps(r) + "\n" for r in records))
    print(f"\nwrote {out.relative_to(ROOT)}: {len(records)} records "
          f"({len(jobs) - len(records)} failed)")

    from collections import Counter
    print("  status:", dict(Counter(r["verdict"]["status"] for r in records)))
    print("  method:", dict(Counter(r["verdict"]["method"] for r in records)))
    print("  match: ", dict(Counter(r["verdict"].get("match") for r in records)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

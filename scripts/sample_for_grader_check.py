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

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.benchmarks import pilot_split, sweep_split
from samplerconfound.config import FIXED, SAMPLER_CONFIGS
from samplerconfound.grade import grade

BASE = "https://api.fireworks.ai/inference/v1/chat/completions"

MODELS = [
    "accounts/fireworks/models/gpt-oss-20b",
    "accounts/fireworks/models/nemotron-lightning-3p5-30b-a3b",
]

_print_lock = threading.Lock()


def load_key() -> str:
    key = os.environ.get("FIREWORKS_API_KEY")
    if not key:
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("FIREWORKS_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        raise SystemExit("no FIREWORKS_API_KEY")
    return key


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
    try:
        r = requests.post(BASE, headers={"Authorization": f"Bearer {key}"},
                          json=body, timeout=300)
    except requests.RequestException as e:
        with _print_lock:
            print(f"  ! {model.split('/')[-1]}/{sampler['id']}/{problem.id}: {e}")
        return None
    if r.status_code != 200:
        with _print_lock:
            print(f"  ! HTTP {r.status_code} {model.split('/')[-1]}/{sampler['id']}: "
                  f"{r.text[:120]}")
        return None
    d = r.json()
    choice = d["choices"][0]
    text = (choice["message"].get("content") or "")
    verdict = grade(text, problem.answer)
    return {
        "problem_id": problem.id,
        "benchmark": "aime" if problem.id.startswith("aime") else "math500",
        "model": model,
        "sampler": sampler["id"],
        "gold": problem.answer,
        "response": text,
        "finish_reason": choice["finish_reason"],
        "output_tokens": d["usage"]["completion_tokens"],
        "verdict": verdict.to_dict(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("runs/grader_check/graded.jsonl"))
    ap.add_argument("--n-math", type=int, default=15)
    ap.add_argument("--n-aime", type=int, default=10)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    key = load_key()
    # Pilot problems for MATH-500 (never the sweep draw). AIME has no spare
    # problems at all — all 60 are in the sweep — so grader verification borrows
    # them. That is harmless: verification does not select models or tune the
    # grader against outcomes, it only checks that the parser agrees with a human.
    problems = pilot_split()[: args.n_math] + sweep_split("aime")[: args.n_aime]

    jobs = [(m, s, p) for m in MODELS for s in SAMPLER_CONFIGS for p in problems]
    print(f"{len(jobs)} generations: {len(MODELS)} models x {len(SAMPLER_CONFIGS)} "
          f"samplers x {len(problems)} problems")

    out = args.out if args.out.is_absolute() else ROOT / args.out
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

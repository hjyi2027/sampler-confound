#!/usr/bin/env python3
"""Download the benchmarks once, pin them, and never touch the network again.

MATH-500 (500 problems) and AIME 2024 + 2025 (30 each, 60 total). Fetched through
the HuggingFace datasets-server rows API rather than the `datasets` library: the
whole dependency is `requests`, and the exact bytes that were downloaded stay
visible in `data/` as plain JSONL.

The manifest records a sha256 over each normalised file. `benchmarks.load()`
checks it on every load. This is not ceremony: the problem set is a factor in the
three-way decomposition, so a dataset that is silently revised upstream between
the pilot and the sweep would change the problem component and the residual
without changing anything visible in the code.

    python3 scripts/fetch_benchmarks.py

Writes data/math500.jsonl, data/aime.jsonl, data/MANIFEST.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

API = "https://datasets-server.huggingface.co/rows"
PAGE = 100

# Pinned sources. Each entry is (dataset, config, split, expected row count). The
# count is asserted, not trusted: a source that quietly grows or shrinks changes
# the denominator of every accuracy in the paper.
SOURCES = {
    "math500": [
        ("HuggingFaceH4/MATH-500", "default", "test", 500),
    ],
    "aime": [
        ("HuggingFaceH4/aime_2024", "default", "train", 30),
        ("yentinglin/aime_2025", "default", "train", 30),
    ],
}


def fetch_rows(dataset: str, config: str, split: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        resp = requests.get(
            API,
            params={"dataset": dataset, "config": config, "split": split,
                    "offset": offset, "length": PAGE},
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        batch = [x["row"] for x in payload["rows"]]
        rows.extend(batch)
        total = payload["num_rows_total"]
        if len(rows) >= total or not batch:
            break
        offset += PAGE
        time.sleep(0.2)  # be polite to a free public API
    return rows


def normalise_math500(row: dict, _source: str) -> dict:
    return {
        "id": row["unique_id"],
        "problem": row["problem"],
        "answer": str(row["answer"]),
        "level": int(row["level"]),
        "subject": row["subject"],
    }


def normalise_aime(row: dict, source: str) -> dict:
    year = str(row["year"])
    # AIME answers are integers in [0, 999]; anything else means the source
    # schema moved and the grader would start scoring against the wrong field.
    answer = str(row["answer"]).strip()
    if not (answer.isdigit() and 0 <= int(answer) <= 999):
        raise ValueError(f"{source}: answer {answer!r} is not an AIME integer")
    return {
        "id": f"aime{year}/{row['id']}",
        "problem": row["problem"],
        "answer": str(int(answer)),  # drop any zero padding; grader compares ints
        "level": None,               # AIME has no per-problem difficulty label
        "subject": None,
    }


NORMALISE = {"math500": normalise_math500, "aime": normalise_aime}


def build(benchmark: str) -> list[dict]:
    out: list[dict] = []
    for dataset, config, split, expected in SOURCES[benchmark]:
        rows = fetch_rows(dataset, config, split)
        if len(rows) != expected:
            raise ValueError(
                f"{dataset} returned {len(rows)} rows, expected {expected}. The "
                "source changed upstream; update SOURCES deliberately rather than "
                "letting the benchmark size drift."
            )
        out.extend(NORMALISE[benchmark](r, dataset) for r in rows)
        print(f"  {dataset} [{config}/{split}]: {len(rows)} problems")

    ids = [p["id"] for p in out]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{benchmark}: duplicate problem ids")
    # Sorted on disk so the file — and therefore its hash — does not depend on
    # the order the API happened to page results back in.
    return sorted(out, key=lambda p: p["id"])


def write_jsonl(path: Path, problems: list[dict]) -> str:
    text = "".join(json.dumps(p, sort_keys=True, ensure_ascii=False) + "\n"
                   for p in problems)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="refetch even if data/ is already populated")
    args = ap.parse_args()

    DATA.mkdir(exist_ok=True)
    manifest_path = DATA / "MANIFEST.json"
    if manifest_path.exists() and not args.force:
        print(f"{manifest_path.relative_to(ROOT)} exists; use --force to refetch",
              file=sys.stderr)
        return 1

    manifest: dict = {"sources": {}, "files": {}}
    for benchmark in SOURCES:
        print(f"{benchmark}:")
        problems = build(benchmark)
        path = DATA / f"{benchmark}.jsonl"
        digest = write_jsonl(path, problems)
        manifest["sources"][benchmark] = [
            {"dataset": d, "config": c, "split": s, "n": n}
            for d, c, s, n in SOURCES[benchmark]
        ]
        manifest["files"][benchmark] = {
            "path": path.name,
            "n_problems": len(problems),
            "sha256": digest,
        }
        print(f"  -> {path}  {len(problems)} problems  {digest[:16]}")

    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"\nwrote {manifest_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

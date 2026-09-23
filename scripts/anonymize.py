#!/usr/bin/env python3
"""Build an anonymised copy of this repository for double-blind submission.

TMLR review is double-blind, so a link to the public repository de-anonymises
the submission. This writes a scrubbed copy to a directory you can push to a
fresh anonymous remote (or upload as supplementary material), and prints what
it changed so the scrubbing itself is reviewable rather than trusted.

It removes: the owner's account name and repository URL, the prior-study names
that identify the author, and the git history (which carries the author's
name and email in every commit). It keeps every measurement, script and test.

    python3 scripts/anonymize.py --out /tmp/sampler-confound-anon
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# literal -> replacement. Extend this list rather than hand-editing the copy.
SUBSTITUTIONS = [
    ("https://github.com/hjyi2027/sampler-confound", "https://anonymous.4open.science/r/sampler-confound"),
    ("github.com/hjyi2027/sampler-confound", "anonymous.4open.science/r/sampler-confound"),
    ("hjyi2027", "ANONYMIZED"),
    ("AIscend", "ANONYMIZED"),
    ("Unauthored by Design", "[prior study, name withheld for review]"),
    ("Does the Decoding Algorithm Have a Voice?", "[prior study, name withheld for review]"),
    ("seed-study-creative", "[prior study]"),
]
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "cache", "node_modules"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".jsonl", ".yml", ".yaml", ".tex", ".bib", ".cff", ".toml", ""}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    out = args.out.resolve()
    if out.exists():
        raise SystemExit(f"{out} exists; remove it first")
    if ROOT in out.parents or out == ROOT:
        raise SystemExit("refusing to write inside the source repository")

    shutil.copytree(ROOT, out, ignore=shutil.ignore_patterns(*SKIP_DIRS))
    shutil.rmtree(out / ".git", ignore_errors=True)

    changed: dict[str, int] = {}
    for p in sorted(out.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            s = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        orig = s
        for old, new in SUBSTITUTIONS:
            if old in s:
                changed[old] = changed.get(old, 0) + s.count(old)
                s = s.replace(old, new)
        if s != orig:
            p.write_text(s, encoding="utf-8")

    # The paper itself switches to the anonymous template variant.
    print(f"wrote {out}")
    for old, n in sorted(changed.items(), key=lambda kv: -kv[1]):
        print(f"  replaced {n:>4}x  {old}")
    leftovers = []
    for p in out.rglob("*"):
        if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES:
            try:
                s = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for old, _ in SUBSTITUTIONS:
                if old in s:
                    leftovers.append((str(p.relative_to(out)), old))
    if leftovers:
        print("\nLEFTOVERS (fix before submitting):")
        for f, old in leftovers:
            print(f"  {f}: {old}")
        return 1
    print("\nno identifying strings remain from the substitution list.")
    print("Still check by hand: figures, PDFs, and anything binary.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

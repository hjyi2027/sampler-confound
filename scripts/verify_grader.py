"""Hand-verify the grader on a stratified sample, then score it against the labels.

Two subcommands.

  sample   draw a review set and write it as a markdown worksheet
  score    read the filled-in worksheet and report grader accuracy

Why stratified rather than random: a uniform sample of a run where 85% of
responses are cleanly parsed correct answers spends 42 of 50 review slots
confirming that "Answer: 42" equals 42. The cases that carry information are the
rare ones — unparseable responses, `last_number` fallbacks, numeric-tolerance
matches, and near misses — so the sample is drawn to over-represent them, and the
reported agreement is computed per stratum rather than pooled. A pooled number
over a deliberately non-uniform sample would be meaningless.

The worksheet is markdown because it has to be read by a person at 1am the day
before a deadline, and a JSON blob will not be.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Strata, in priority order. A record lands in the first one it qualifies for.
# The ordering encodes how much each case can hurt: a wrong parse corrupts the
# unparseable rate, which this study reports as a result in its own right.
STRATA = [
    ("unparseable", lambda r: r["verdict"]["status"] == "unparseable"),
    ("last_number", lambda r: r["verdict"]["method"] == "last_number"),
    ("numeric_tol", lambda r: r["verdict"].get("match") == "numeric_tol"),
    ("heavily_normalised", lambda r: len(r["verdict"].get("rules") or []) >= 4),
    ("incorrect", lambda r: r["verdict"]["status"] == "incorrect"),
    ("correct_plain", lambda r: True),
]

# Deliberately not proportional to the population.
QUOTA = {
    "unparseable": 12,
    "last_number": 10,
    "numeric_tol": 6,
    "heavily_normalised": 8,
    "incorrect": 8,
    "correct_plain": 6,
}


def record_key(r: dict) -> str:
    """Stable identity for one generation, independent of grader version."""
    return f"{r.get('model', '?')}|{r.get('sampler', '?')}|{r.get('problem_id', '?')}"


def stratify(records: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        for name, pred in STRATA:
            if pred(r):
                out[name].append(r)
                break
    return out


def cmd_sample(args) -> int:
    records = [json.loads(l) for l in Path(args.graded).read_text().splitlines() if l.strip()]
    if not records:
        print(f"no records in {args.graded}", file=sys.stderr)
        return 2

    rng = random.Random(args.seed)
    buckets = stratify(records)
    picked: list[tuple[str, dict]] = []
    shortfall = {}
    for name, quota in QUOTA.items():
        pool = buckets.get(name, [])
        take = min(quota, len(pool))
        if take < quota:
            shortfall[name] = (take, quota)
        picked.extend((name, r) for r in rng.sample(pool, take))

    # Top up from the largest remaining pools so the worksheet still reaches the
    # target size when a stratum is thin, rather than silently under-sampling.
    target = args.n or sum(QUOTA.values())
    if len(picked) < target:
        used = {id(r) for _, r in picked}
        rest = [(n, r) for n, rs in buckets.items() for r in rs if id(r) not in used]
        rng.shuffle(rest)
        picked.extend(rest[: target - len(picked)])

    rng.shuffle(picked)

    carried = {}
    if getattr(args, "carry_labels", None):
        prev = Path(args.carry_labels).read_text()
        for m in _ITEM.finditer(prev):
            h = _HUMAN.search(m.group("body"))
            g = _GRADER.search(m.group("body"))
            if h and g:
                label = h.group(1).strip().lower()
                key = json.loads(g.group(1)).get("key")
                if key and label in {"correct", "incorrect", "unparseable"}:
                    carried[key] = label
        reused = sum(record_key(r) in carried for _, r in picked)
        print(f"carrying {len(carried)} labels; {reused}/{len(picked)} items reusable")

    lines = [
        "# Grader verification worksheet",
        "",
        f"Source: `{args.graded}`  |  sample seed: {args.seed}  |  n = {len(picked)}",
        "",
        "For each item, read the response and the gold answer and decide whether the",
        "response is mathematically correct. Write `correct`, `incorrect`, or",
        "`unparseable` on the **Human:** line. Do not look at the grader's verdict",
        "first — it is printed below your line for exactly that reason.",
        "",
        "`unparseable` means: a person could not tell what final answer this response",
        "is claiming. It does not mean the answer is wrong.",
        "",
        "---",
        "",
    ]
    if shortfall:
        lines.insert(
            6,
            "> Note: strata under quota — "
            + ", ".join(f"{k} {a}/{b}" for k, (a, b) in shortfall.items())
            + "\n",
        )

    for i, (stratum, r) in enumerate(picked, 1):
        v = r["verdict"]
        resp = r.get("response", "")
        if len(resp) > args.max_chars:
            # Truncate the MIDDLE, never the tail. Cutting to the first N chars
            # hides the final answer, which is the only part a reviewer needs —
            # long responses are exactly the rambling high-temperature ones the
            # strata over-sample, so the first version of this made the most
            # informative items the unverifiable ones.
            head = args.max_chars // 3
            tail = args.max_chars - head
            resp = (
                resp[:head]
                + f"\n\n... [{len(resp) - args.max_chars} chars elided from the middle]\n\n"
                + resp[-tail:]
            )
        lines += [
            f"## {i}. `{r.get('problem_id', '?')}`",
            "",
            f"**Gold:** `{r.get('gold', '')}`",
            "",
            "**Response:**",
            "",
            "```",
            resp,
            "```",
            "",
            f"**Human:** {carried.get(record_key(r), '')}",
            "",
            "<!-- grader: "
            + json.dumps(
                {
                    # A stable identity for the record, so labels survive a
                    # regrade. Item NUMBER is not stable: changing grade.py moves
                    # records between strata, which changes the draw, and labels
                    # carried over by position then describe different problems.
                    # That silently produced a meaningless 60% agreement once.
                    "key": record_key(r),
                    "status": v["status"],
                    "method": v["method"],
                    "match": v.get("match"),
                    "extracted": v.get("extracted"),
                    "stratum": stratum,
                }
            )
            + " -->",
            "",
            "---",
            "",
        ]

    Path(args.out).write_text("\n".join(lines))
    print(f"wrote {args.out} ({len(picked)} items)")
    print("strata drawn:", dict(Counter(s for s, _ in picked)))
    return 0


_ITEM = re.compile(
    r"^## (\d+)\.(?P<body>.*?)(?=^## \d+\.|\Z)", re.MULTILINE | re.DOTALL
)
_HUMAN = re.compile(r"^\*\*Human:\*\*\s*(.*)$", re.MULTILINE)
_GRADER = re.compile(r"<!-- grader: (\{.*?\}) -->", re.DOTALL)


def cmd_score(args) -> int:
    text = Path(args.worksheet).read_text()
    rows = []
    for m in _ITEM.finditer(text):
        body = m.group("body")
        h = _HUMAN.search(body)
        g = _GRADER.search(body)
        if not h or not g:
            continue
        label = h.group(1).strip().lower()
        rows.append((label, json.loads(g.group(1))))

    filled = [(l, g) for l, g in rows if l in {"correct", "incorrect", "unparseable"}]
    blank = len(rows) - len(filled)
    if not filled:
        print("no items labelled yet", file=sys.stderr)
        return 2

    agree = sum(l == g["status"] for l, g in filled)
    print(f"labelled {len(filled)}/{len(rows)} items ({blank} blank)")
    print(f"overall agreement: {agree}/{len(filled)} = {agree / len(filled):.1%}\n")

    print("per stratum:")
    by = defaultdict(list)
    for l, g in filled:
        by[g["stratum"]].append(l == g["status"])
    for name in QUOTA:
        v = by.get(name, [])
        if v:
            print(f"  {name:20s} {sum(v)}/{len(v)} = {sum(v) / len(v):.0%}")

    disagreements = [(l, g) for l, g in filled if l != g["status"]]
    if disagreements:
        print(f"\n{len(disagreements)} disagreements — each one is a grader bug or a "
              "label error, and both need reading:")
        for l, g in disagreements:
            print(f"  human={l:12s} grader={g['status']:12s} "
                  f"method={g['method']:12s} extracted={g.get('extracted')!r}")

    # The directional breakdown matters more than the total. False positives
    # inflate accuracy; false unparseables inflate the rate this study reports.
    fp = sum(l != "correct" and g["status"] == "correct" for l, g in filled)
    fn = sum(l == "correct" and g["status"] != "correct" for l, g in filled)
    fu = sum(l != "unparseable" and g["status"] == "unparseable" for l, g in filled)
    print(f"\nscored correct but is not:      {fp}   (inflates accuracy)")
    print(f"scored not-correct but is:      {fn}   (deflates accuracy)")
    print(f"scored unparseable but is not:  {fu}   (inflates the unparseable rate)")
    if fp:
        print("\nAny false positive is disqualifying at this sample size — a grader "
              "that credits wrong answers cannot support a claim about small "
              "differences between accuracies. Fix and re-verify.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="draw a stratified review set")
    s.add_argument("graded", help="JSONL with fields: problem_id, gold, response, verdict")
    s.add_argument("--out", default="runs/grader_worksheet.md")
    s.add_argument("--n", type=int, default=50)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--max-chars", type=int, default=2000)
    s.add_argument("--carry-labels", metavar="WORKSHEET",
                   help="reuse human labels from an earlier worksheet, matched by "
                        "record key rather than item number")
    s.set_defaults(func=cmd_sample)

    c = sub.add_parser("score", help="score a filled-in worksheet")
    c.add_argument("worksheet")
    c.set_defaults(func=cmd_score)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

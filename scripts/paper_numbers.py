#!/usr/bin/env python3
"""Every number in the manuscript, recomputed from the committed records.

A paper whose numbers were typed in by hand drifts from its data the first
time a verdict is re-derived. This script emits each claim in `paper/main.tex`
with the value the data currently supports and the file it came from, so the
two can be diffed. It reads only committed records — no API key, no response
cache — and `make numbers` writes paper/NUMBERS.md from it.

    python3 scripts/paper_numbers.py
    python3 scripts/paper_numbers.py --md > paper/NUMBERS.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.documented import claim
from scripts.gap import cells
from scripts.probe_matrix import collect


def rows() -> list[tuple[str, str, str]]:
    """(claim as it appears in the paper, value now, source)"""
    out: list[tuple[str, str, str]] = []
    add = lambda c, v, s: out.append((c, str(v), s))

    matrix = collect()
    gap = cells()
    add("models audited", len(matrix), "runs/matrix/ via scripts/probe_matrix.py")
    prices = [r["usd_out_per_1m"] for r in matrix if r["usd_out_per_1m"] == r["usd_out_per_1m"]]
    add("output price span ($/M)", f"{min(prices):.2f}-{max(prices):.2f}", "samplerconfound/pricing.py")

    labels = Counter(c["label"] for c in gap)
    add("gap cells, total", len(gap), "scripts/gap.py")
    add("cells: as documented", labels["as documented"], "scripts/gap.py")
    add("cells: documented, accepted, ignored", labels["DOCUMENTED, ACCEPTED, IGNORED"], "scripts/gap.py")
    add("cells: UNDOCUMENTED, ACCEPTED, IGNORED", labels[
        "UNDOCUMENTED, ACCEPTED, IGNORED"], "scripts/gap.py")
    add("cells: undocumented, accepted, works", labels["undocumented, accepted, works"], "scripts/gap.py")
    add("cells: accepted then the server fails", labels["accepted, then the server fails"], "scripts/gap.py")

    # Table 1: per-parameter accepted / honoured / inert / undetermined
    by = defaultdict(list)
    for c in gap:
        by[c["parameter"]].append(c)
    for p in ("top_p", "top_k", "min_p", "typical_p", "mirostat", "repetition_penalty",
              "frequency_penalty", "presence_penalty", "seed"):
        cs = by[p]
        add(f"Table 1 {p}",
            f"accepted {sum(c['accepted'] == 'yes' for c in cs)}/{len(cs)}, "
            f"honoured {sum(c['honoured'] == 'yes' for c in cs)}, "
            f"inert {sum(c['honoured'] == 'no' for c in cs)}, "
            f"undet {sum(c['honoured'] in ('undetermined', 'mixed') for c in cs)}",
            "scripts/gap.py")
    add("seed, as documented", repr(claim("fireworks", "seed")["claim"]), "samplerconfound/documented.py")

    # Table 2: the undocumented four
    for p in ("use_beam_search", "skip_special_tokens", "ignore_eos", "best_of"):
        cs = by[p]
        add(f"Table 2 {p}", Counter(c["honoured"] for c in cs).most_common(), "runs/matrix/undocumented.json")

    # determinism
    det = [r for r in matrix if "greedy_match" in r]
    add("models deterministic at T=0", sum(r["greedy_match"] == 1.0 for r in det), "scripts/probe_matrix.py")
    nd = sorted(r["greedy_match"] for r in det if r["greedy_match"] < 1.0)
    add("modal-share range, non-deterministic", f"{nd[0]:.0%}-{nd[-1]:.0%}", "scripts/probe_matrix.py")
    add("models honouring seed", sum(r.get("seed") == "yes" for r in det), "scripts/probe_matrix.py")
    seeded = 0
    total = 0
    det_files = [f for f in sorted((ROOT / "runs" / "matrix").glob("*/*det*.json"))
                 if "sequential" not in f.name]              # different schema, reported below
    for f in [ROOT / "runs" / "determinism.json"] + det_files:
        try:
            d = json.loads(f.read_text())
        except OSError:
            continue
        for r in d.get("rows", []):
            ns = r["conditions"]["no_seed"]
            if ns.get("n") and ns["content"][0] < 1.0:
                total += 1
                seeded += max(r["conditions"]["seed_0"]["content"][0],
                              r["conditions"]["seed_1"]["content"][0]) >= 1.0
    add("(model,prompt) cells non-deterministic without a seed", total, "runs/**/determinism*.json")
    add("  of those, made reproducible by a fixed seed", seeded, "runs/**/determinism*.json")

    # sequential vs concurrent determinism, same day (section 4.3)
    sq = json.loads((ROOT / "runs" / "matrix" / "fireworks" / "determinism_sequential.json").read_text())
    dm = sq["deterministic_models"]
    add("models reproducing on every prompt, sequential / concurrent",
        f"{dm['sequential']}/{dm['of']} / {dm['concurrent']}/{dm['of']}",
        "runs/matrix/fireworks/determinism_sequential.json")
    summ = sq["summary"]
    up_ = [m for m, v in summ.items() if v["sequential_pairwise"] - v["concurrent_pairwise"] >= 0.10]
    add("models whose pairwise match rises >= 10 pts when sequential", f"{len(up_)}: {', '.join(sorted(up_))}",
        "runs/matrix/fireworks/determinism_sequential.json")
    diffs = [r["sequential"]["pairwise"] - r["concurrent"]["pairwise"] for r in sq["rows"]
             if r["sequential"]["n"] == 10 and r["concurrent"]["n"] == 10]
    add("(model,prompt) pairs: sequential more / less / equal agreement",
        f"{sum(d > 0 for d in diffs)} / {sum(d < 0 for d in diffs)} / {sum(d == 0 for d in diffs)}",
        "runs/matrix/fireworks/determinism_sequential.json")
    add("sequential collection date", sq["collected"]["from"][:16], "runs/matrix/fireworks/determinism_sequential.json")

    # stratified test vs majority vote (section 3)
    import glob as _g
    agree = total_ = 0
    flips = []
    for f in [ROOT / "runs" / "distinguish_multiprompt.json"] + [Path(x) for x in _g.glob(str(ROOT / "runs" / "matrix" / "*" / "*.json"))]:
        try:
            d = json.loads(Path(f).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for a in d.get("aggregates", []) if isinstance(d, dict) else []:
            if a.get("verdict_majority") and a.get("stratified_prompts"):
                total_ += 1
                agree += a["verdict"] == a["verdict_majority"]
                if a["verdict"] != a["verdict_majority"]:
                    flips.append(f"{a['model']}/{a['parameter']}/{Path(f).name}: {a['verdict_majority']} -> {a['verdict']}")
    add("stratified test agrees with majority vote", f"{agree}/{total_}", "every report's aggregates")
    kinds = Counter()
    for f_ in flips:
        a_, b_ = f_.split(": ", 1)[1].split(" -> ")
        definite = {"distinguishable", "no effect seen"}
        if a_ in definite and b_ in definite:
            kinds["definite verdict reversed"] += 1
        elif a_ == "underpowered" and b_ in definite:
            kinds["undetermined -> resolved by pooling"] += 1
        elif b_ == "mixed":
            kinds["-> mixed (effect on some prompts only)"] += 1
        else:
            kinds[f"{a_} -> {b_}"] += 1
    add("  disagreements by kind", dict(kinds), "every report's aggregates")
    add("  where they differ", "; ".join(sorted(set(flips))[:12]) + (" ..." if len(set(flips)) > 12 else ""),
        "every report's aggregates")

    # negative control
    neg_dh = neg_n = 0
    for f in sorted((ROOT / "runs" / "matrix").glob("*/*.neg-n40.json")) + [ROOT / "runs" / "negative_control.json"]:
        try:
            d = json.loads(f.read_text())
        except OSError:
            continue
        # Apply the CURRENT degeneracy rule rather than the stored status:
        # runs/negative_control.json predates it and still marks three
        # all-unique pairs "ok", which would inflate the denominator to 40.
        ok = [r for r in d["results"] if r["status"] == "ok"
              and not (r["support_tight"] >= r["n_tight"] - 1
                       and r["support_open"] >= r["n_open"] - 1)]
        neg_n += len(ok)
        neg_dh += sum(r["dh_p"] < 0.05 for r in ok)
    add("negative control: dH false positives / informative pairs",
        f"{neg_dh}/{neg_n}", "runs/**/*.neg-n40.json, runs/negative_control.json")

    # unparseable (section 4.4)
    up = json.loads((ROOT / "runs" / "smoke" / "unparseable.json").read_text())
    for corpus in ("math500", "aime"):
        for m, cell in up[corpus]["cells"].items():
            u = cell["unparseable"]
            if max(u.values()) > 0:
                add(f"unparseable, {corpus}, {m}",
                    " ".join(f"{k} {v:.0%}" for k, v in u.items()), "runs/smoke/unparseable.json")
    add("pairwise comparisons inverted by the scoring rule",
        f"{up['math500']['rank_flips_total'][0]}/{up['math500']['rank_flips_total'][1]}",
        "runs/smoke/unparseable.json")

    # the min_p illustration (section 5)
    mp = json.loads((ROOT / "runs" / "smoke" / "minp_confound.json").read_text())["scenarios"]
    add("interaction share, all honoured", f"{mp['A: all honoured']['interaction_share']:.1%}",
        "runs/smoke/minp_confound.json")
    bs = [v["interaction_share"] for k, v in mp.items() if k.startswith("B")]
    ds = [v["interaction_share"] for k, v in mp.items() if k.startswith("D")]
    add("  one stack ignoring", f"{min(bs):.1%}-{max(bs):.1%}", "runs/smoke/minp_confound.json")
    add("  all ignoring", f"{mp['C: all ignore']['interaction_share']:.1%}", "runs/smoke/minp_confound.json")
    add("  all ignoring but one", f"{min(ds):.1%}-{max(ds):.1%}", "runs/smoke/minp_confound.json")

    # dates
    dates = [r["collected"]["from"] for r in matrix if r.get("collected", {}).get("from")]
    add("collection window", f"{min(dates)[:10]} to {max(r['collected']['to'] for r in matrix if r.get('collected',{}).get('to'))[:10]}",
        "every cell, from its calls' cache timestamps")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true", help="emit markdown for paper/NUMBERS.md")
    args = ap.parse_args()
    rs = rows()
    if args.md:
        print("# Every number in the manuscript, traced\n")
        print("Regenerated by `python3 scripts/paper_numbers.py --md`, which reads only")
        print("committed records — no API key, no response cache. If a value here and a")
        print("value in `paper/main.tex` disagree, the paper is stale.\n")
        print("| claim | value | source |\n|---|---|---|")
        for c, v, s in rs:
            print(f"| {c} | `{v}` | `{s}` |")
    else:
        for c, v, s in rs:
            print(f"{c:<52}{v:<34}{s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""The audit's coverage, in one table: every probed model, on every provider,
with its price tier, its parameter verdicts and its determinism.

Reads the per-model probe outputs under runs/matrix/<provider>/ plus the
original budget-band runs (runs/distinguish_multiprompt.json and
runs/determinism.json, which predate the matrix layout), and prints one row per
(provider, model). Price is the pinned table; "free"/"paid" is a fact about
this provider's tier, and the point of the column is that the table is not
only the free half.

    python3 scripts/probe_matrix.py
    python3 scripts/probe_matrix.py --json runs/matrix/matrix.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.paths import resolve_out, show
from samplerconfound.pricing import PRICES

MATRIX = ROOT / "runs" / "matrix"
LEGACY_DIST = ROOT / "runs" / "distinguish_multiprompt.json"
LEGACY_DET = ROOT / "runs" / "determinism.json"
PARAMS = ("top_p", "top_k", "min_p")
SYM = {"distinguishable": "yes", "no effect seen": "NO", "underpowered": "?",
       "mixed": "mix", "rejected": "rej", "unsupported": "n/a", "insufficient": "n/a"}


def _load(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def distinguish_rows(provider: str, d: dict) -> dict[str, dict]:
    """model -> {param: verdict, "control": fraction, "powered_prompts": n}"""
    out: dict[str, dict] = {}
    for a in d.get("aggregates", []):
        row = out.setdefault(a["model"], {"n_per_arm": d.get("n_per_arm")})
        row[a["parameter"]] = a["verdict"]
        row.setdefault("n_prompts", a["n_prompts"])
    for pid, by_model in d.get("positive_control_by_prompt", {}).items():
        for m, mp in by_model.items():
            row = out.setdefault(m, {})
            row.setdefault("control", []).append(mp["fraction_removed"])
            row["powered_prompts"] = row.get("powered_prompts", 0) + bool(mp["powered"])
    for m, row in out.items():
        row["provider"] = provider
        fr = [x for x in row.pop("control", []) if x == x]
        row["control_removed"] = sum(fr) / len(fr) if fr else float("nan")
    return out


def determinism_rows(d: dict) -> dict[str, dict]:
    out = {}
    for m, s in d.get("summary", {}).items():
        out[m] = {"greedy_match": s["greedy_match"],
                  "seed": "yes" if str(s["seed"]).startswith("yes") else
                          ("n/a" if str(s["seed"]).startswith("n/a") else
                           ("rej" if s["seed"] == "rejected" else "NO"))}
    return out


def collect() -> list[dict]:
    rows: dict[tuple[str, str], dict] = {}

    def merge(provider, dist, det):
        for m, r in distinguish_rows(provider, dist or {}).items():
            rows.setdefault((provider, m), {"provider": provider, "model": m}).update(r)
        for m, r in determinism_rows(det or {}).items():
            rows.setdefault((provider, m), {"provider": provider, "model": m}).update(r)

    merge("fireworks", _load(LEGACY_DIST), _load(LEGACY_DET))
    if MATRIX.exists():
        for pdir in sorted(p for p in MATRIX.iterdir() if p.is_dir()):
            prov = pdir.name
            # legacy: one multi-model determinism file, and <model>.json at n=40
            merge(prov, None, _load(pdir / "determinism.json"))
            best: dict[str, tuple[int, Path]] = {}
            for f in pdir.glob("*.json"):
                name = f.name
                if name in ("determinism.json", "matrix.json") or ".neg-n" in name:
                    continue
                if name.endswith(".det.json"):
                    merge(prov, None, _load(f))
                    continue
                m = re.match(r"(.+)\.dist-n(\d+)\.json$", name)
                model, n = (m.group(1), int(m.group(2))) if m else (name[:-5], 40)
                if model not in best or n > best[model][0]:
                    best[model] = (n, f)           # the thickest run of a model wins
            for model, (n, f) in best.items():
                d = _load(f)
                if d:
                    merge(prov, d, None)
                    rows[(prov, model)]["n_per_arm"] = d.get("n_per_arm", n)

    out = []
    for (prov, m), r in rows.items():
        p_in, p_out = PRICES.get(m, (float("nan"), float("nan")))
        r["usd_out_per_1m"] = p_out
        r["tier"] = "paid" if p_out == p_out else "unpriced"
        out.append(r)
    out.sort(key=lambda r: (r["provider"], r["usd_out_per_1m"] if r["usd_out_per_1m"] == r["usd_out_per_1m"] else 1e9))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    rows = collect()

    print(f"{'provider':<11}{'model':<32}{'$/1M out':>9}{'n':>4}{'ctrl':>6}{'pwr':>5}"
          + "".join(f"{p:>7}" for p in PARAMS) + f"{'greedy':>8}{'seed':>6}")
    for r in rows:
        ctrl = r.get("control_removed", float("nan"))
        print(f"{r['provider']:<11}{r['model']:<32}"
              f"{r['usd_out_per_1m']:>9.2f}"
              f"{str(r.get('n_per_arm', '—')):>4}"
              f"{(f'{ctrl:.0%}' if ctrl == ctrl else '—'):>6}"
              f"{str(r.get('powered_prompts', '—')) + '/' + str(r.get('n_prompts', '—')):>5}"
              + "".join(f"{SYM.get(r.get(p, ''), '—'):>7}" for p in PARAMS)
              + f"{(f'{r['greedy_match']:.0%}' if 'greedy_match' in r else '—'):>8}"
              + f"{r.get('seed', '—'):>6}")
    n_prov = len({r["provider"] for r in rows})
    priced = [r["usd_out_per_1m"] for r in rows if r["usd_out_per_1m"] == r["usd_out_per_1m"]]
    print(f"\n{len(rows)} models on {n_prov} provider(s); output price spans "
          f"${min(priced):.2f}–${max(priced):.2f}/1M")
    print("ctrl = share of open-arm entropy temperature removes (positive control), mean over prompts;"
          " pwr = prompts whose control passed / prompts run;"
          " yes/NO/? = distinguishable / no effect seen with power / underpowered;"
          " greedy = exact-match rate of 10 identical T=0 calls;"
          " seed = fixed seed reproduces a non-deterministic prompt")
    if args.json:
        dest = resolve_out(args.json)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(rows, indent=2, default=str) + "\n")
        print(f"wrote {show(dest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

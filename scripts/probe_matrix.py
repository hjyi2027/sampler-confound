#!/usr/bin/env python3
"""The audit's coverage, in one table: every probed model, on every provider,
with its price tier, a verdict per sampling parameter, and its determinism.

A parameter column reads, per (provider, model):

    yes   accepted and honoured: the two arms are distinguishable. For a
          penalty this is judged on a forced-repetition prompt, where the
          parameter must act if applied; whether it also moves free text is
          the <param>_free_text field in the JSON
    NO    accepted and inert: a passed temperature control says the probe had
          power on this model and prompt, and the parameter still moved nothing
    ?     accepted, verdict withheld: the control failed, or every prompt was
          degenerate (both arms all-unique, so exact-match statistics have no
          range — the usual case for a penalty on a free-text prompt)
    mix   prompts disagree
    rej   the provider refused the parameter (HTTP 4xx) — not accepted
    err   accepted, and the provider then did not finish the request (hung or
          dropped mid-generation) — accepted and unusable at that value
    n/a   the adapter has no wire form for it on this provider

"Accepted" is what the provider's HTTP status says; "honoured" is what the
output distribution says. The gap between the two columns is the finding.

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
PARAMS = ("top_p", "top_k", "min_p", "typical_p", "mirostat",
          "repetition_penalty", "frequency_penalty", "presence_penalty")
SHORT = {"top_p": "top_p", "top_k": "top_k", "min_p": "min_p", "typical_p": "typ_p",
         "mirostat": "miro", "repetition_penalty": "rep", "frequency_penalty": "freq",
         "presence_penalty": "pres"}
SYM = {"distinguishable": "yes", "no effect seen": "NO", "underpowered": "?",
       "mixed": "mix", "rejected": "rej", "unsupported": "n/a", "insufficient": "n/a",
       "transport": "err"}


def _load(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def distinguish_rows(provider: str, d: dict) -> dict[str, dict]:
    """model -> {param: verdict, "control": fraction, "powered_prompts": n}"""
    out: dict[str, dict] = {}
    for a in d.get("aggregates", []):
        if not a.get("n_prompts") and not a.get("verdict_forced"):
            continue                           # parameter not run in this file
        row = out.setdefault(a["model"], {})
        if a.get("verdict_forced"):
            # a penalty: the column is whether the provider applies it (forced
            # repetition); whether it matters on free text is kept alongside
            row[a["parameter"]] = a["verdict_forced"]
            row[a["parameter"] + "_free_text"] = a["verdict"]
        else:
            row[a["parameter"]] = a["verdict"]
        row.setdefault("n_by_param", {})[a["parameter"]] = d.get("n_per_arm")
        row["n_prompts"] = max(row.get("n_prompts", 0), a["n_prompts"])
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
            row = rows.setdefault((provider, m), {"provider": provider, "model": m})
            nbp = {**row.get("n_by_param", {}), **r.pop("n_by_param", {})}
            row.update(r)
            row["n_by_param"] = nbp
        for m, r in determinism_rows(det or {}).items():
            rows.setdefault((provider, m), {"provider": provider, "model": m}).update(r)

    merge("fireworks", _load(LEGACY_DIST), _load(LEGACY_DET))
    if MATRIX.exists():
        for pdir in sorted(p for p in MATRIX.iterdir() if p.is_dir()):
            prov = pdir.name
            # legacy: one multi-model determinism file, and <model>.json at n=40
            merge(prov, None, _load(pdir / "determinism.json"))
            # every distinguishability file for a model, thinnest first, so a
            # thicker run of the same parameter overrides a thinner one and a
            # parameter only the later file has is added rather than lost
            files: list[tuple[int, str, Path]] = []
            for f in pdir.glob("*.json"):
                name = f.name
                if name in ("determinism.json", "matrix.json") or ".neg-n" in name:
                    continue
                if name.endswith(".det.json"):
                    merge(prov, None, _load(f))
                    continue
                m = re.match(r"(.+)\.dist-n(\d+)\.json$", name)
                model, n = (m.group(1), int(m.group(2))) if m else (name[:-5], 40)
                files.append((n, model, f))
            for n, model, f in sorted(files):
                d = _load(f)
                if d:
                    merge(prov, d, None)

    out = []
    for (prov, m), r in rows.items():
        ns = sorted({n for n in r.get("n_by_param", {}).values() if n})
        r["n_per_arm"] = ns[0] if len(ns) == 1 else (f"{ns[0]}-{ns[-1]}" if ns else None)
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

    print(f"{'provider':<11}{'model':<32}{'$/1M out':>9}{'n':>6}{'ctrl':>6}{'pwr':>5}"
          + "".join(f"{SHORT[p]:>7}" for p in PARAMS) + f"{'greedy':>8}{'seed':>6}")
    for r in rows:
        ctrl = r.get("control_removed", float("nan"))
        print(f"{r['provider']:<11}{r['model']:<32}"
              f"{r['usd_out_per_1m']:>9.2f}"
              f"{str(r.get('n_per_arm') or '—'):>6}"
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
          " rej = provider refused the parameter; n/a = adapter has no wire form for it;"
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

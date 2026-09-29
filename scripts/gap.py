#!/usr/bin/env python3
"""Documented versus measured, per (provider, model, parameter).

Three columns that are three different facts:

    documented   what the vendor's reference says (samplerconfound/documented.py,
                 a dated transcription with the URL)
    accepted     what the HTTP status said when the probe sent it
    honoured     what the output distribution did (scripts/probe_matrix.py)

The gap between the first and the third is the paper. Each cell gets one
label, and the worst — accepted by the API, absent from the docs, ignored at
runtime — is listed by name, because a user who sets it gets no error, no
warning, and no effect.

    python3 scripts/gap.py                 # summary per (provider, parameter)
    python3 scripts/gap.py --cells         # every (provider, model, parameter)
    python3 scripts/gap.py --json runs/matrix/gap.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.documented import DOCUMENTED, PARAMS, claim
from samplerconfound.paths import resolve_out, show
from scripts.probe_matrix import collect

# verdict -> honoured
HONOURED = {"distinguishable": "yes", "no effect seen": "no", "mixed": "mixed",
            "underpowered": "undetermined", "insufficient": "undetermined",
            "unsupported": "not sent", "transport": "hung", "rejected": "refused"}

# The seed is tested at temperature 0 (scripts/probe_determinism.py): ten
# identical requests with the same seed, byte-identical or not. At T=0 there
# is no random draw for a seed to fix, so the test cannot show that the seed
# is ignored by the sampler; it shows whether the documented promise,
# "deterministic sampling", is delivered to a caller who sets one. The labels
# say that and nothing stronger.
SEED_NOT_DELIVERED = "not delivered at T=0"
SEED_MOOT = "deterministic without it"
SEED_HONOURED = {"yes": "yes", "NO": SEED_NOT_DELIVERED, "n/a": SEED_MOOT, "rej": "refused"}

# A penalty is tested twice: on free-text prompts (does it change the output
# distribution?) and on a prompt that forces repetition (does it break the
# forced string?). Evidence of an effect from EITHER test means the provider
# applies the parameter. A null on the forced prompt is decisive only where an
# applied penalty must overturn the forced token: frequency_penalty subtracts
# 2.0 per prior occurrence, so by the sixth "yes" the token has lost 10 logits.
# repetition_penalty rescales a logit by 1/2, so its effect depends on the
# logit's absolute level (muse-glimmer-30b changes free text under it while
# forced repetition survives), and presence_penalty is a flat 2 logits. For
# those two a forced null is not evidence that the parameter is ignored.
PENALTIES = ("repetition_penalty", "frequency_penalty", "presence_penalty")
FORCED_DECISIVE = {"frequency_penalty"}


def penalty_honoured(param: str, forced: str | None, free: str | None) -> str:
    """One honoured value from the forced-repetition and free-text verdicts."""
    vs = (forced, free)
    if "distinguishable" in vs:
        return "yes"
    if "rejected" in vs:
        return "refused"
    if "transport" in vs:
        return "hung"
    if "mixed" in vs:
        return "mixed"
    if param in FORCED_DECISIVE and forced == "no effect seen":
        return "no"
    return "undetermined"


WORST = "UNDOCUMENTED, ACCEPTED, IGNORED"


def label(doc: dict | None, accepted: str, honoured: str) -> str:
    """One phrase for the cell. The order of checks is the order of severity."""
    if doc is None:
        return "docs not read"
    if accepted == "not sent":
        return "adapter has no wire form"
    documented = doc["status"] == "documented"
    says_unsupported = documented and "not yet supported" in doc["claim"]
    if accepted == "refused":
        return "documented, refused" if documented else "undocumented, refused (correct)"
    if accepted == "hung":
        return "accepted, then the server fails"
    if honoured == SEED_NOT_DELIVERED:
        return "documented determinism not delivered at T=0"
    if honoured == SEED_MOOT:
        return "deterministic without it"
    if honoured in ("undetermined", "mixed"):
        return ("documented, " if documented else "undocumented, ") + "accepted, undetermined"
    if honoured == "yes":
        return "as documented" if documented else "undocumented, accepted, works"
    # honoured == "no"
    if says_unsupported:
        return "documented as unsupported, and is"
    if documented:
        return "DOCUMENTED, ACCEPTED, IGNORED"
    return WORST


UNDOC_FILE = ROOT / "runs" / "matrix" / "undocumented.json"
UNDOC_PARAMS = ("ignore_eos", "use_beam_search", "skip_special_tokens", "best_of")


def undocumented_cells() -> list[dict]:
    """Rows from scripts/probe_undocumented.py, in the same shape."""
    try:
        rows = json.loads(UNDOC_FILE.read_text())["rows"]
    except (OSError, json.JSONDecodeError, KeyError):
        return []
    out = []
    for r in rows:
        for param in UNDOC_PARAMS:
            v = r[param]["verdict"]
            if v == "rejected":
                accepted, honoured = "refused", "refused"
            elif v == "honoured":
                accepted, honoured = "yes", "yes"
            elif v == "ignored":
                accepted, honoured = "yes", "no"
            elif v.startswith("error"):
                accepted, honoured = "hung", "hung"
            else:
                accepted, honoured = "yes", "undetermined"
            doc = claim(r["provider"], param)
            out.append({"provider": r["provider"], "model": r["model"], "parameter": param,
                        "documented": doc["status"] if doc else None, "claim": doc["claim"] if doc else "",
                        "accepted": accepted, "honoured": honoured, "verdict": v,
                        "free_text": None, "n_per_arm": None, "collected": r.get("collected"),
                        "label": label(doc, accepted, honoured)})
    return out


def cells() -> list[dict]:
    out = undocumented_cells()
    for r in collect():
        prov, model = r["provider"], r["model"]
        for param in PARAMS:
            doc = claim(prov, param)
            if param == "seed":
                v = r.get("seed")
                if v is None:
                    continue
                honoured = SEED_HONOURED.get(v, "undetermined")
                accepted = "refused" if v == "rej" else "yes"
                measured = v
            elif param in PENALTIES:
                v = r.get(param)                        # forced-repetition verdict
                free = r.get(param + "_free_text")
                if v is None and free is None:
                    continue
                honoured = penalty_honoured(param, v, free)
                accepted = {"refused": "refused", "hung": "hung"}.get(honoured, "yes")
                measured = v
            else:
                v = r.get(param)
                if v is None:
                    continue
                honoured = HONOURED.get(v, "undetermined")
                accepted = {"refused": "refused", "not sent": "not sent", "hung": "hung"}.get(honoured, "yes")
                measured = v
            out.append({
                "provider": prov, "model": model, "parameter": param,
                "documented": (doc["status"] if doc else None),
                "claim": (doc["claim"] if doc else ""),
                "accepted": accepted, "honoured": honoured,
                "verdict": measured,
                "free_text": r.get(param + "_free_text"),
                "n_per_arm": r.get("n_by_param", {}).get(param),
                "collected": r.get("collected"),
                "label": label(doc, accepted, honoured),
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells", action="store_true", help="print every (provider, model, parameter)")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    rows = cells()

    # ---- per (provider, parameter) --------------------------------------
    by = defaultdict(list)
    for c in rows:
        by[(c["provider"], c["parameter"])].append(c)
    print(f"{'provider':<11}{'parameter':<20}{'documented':<12}{'accepted':>9}{'honoured':>9}"
          f"{'mixed':>6}{'inert':>6}{'undet.':>7}{'other':>6}  claim")
    order = list(PARAMS) + list(UNDOC_PARAMS)
    for (prov, param), cs in sorted(by.items(), key=lambda kv: (kv[0][0], order.index(kv[0][1]))):
        doc = claim(prov, param)
        d = (doc["status"] if doc else "not read")
        if doc and "not yet supported" in doc["claim"]:
            d = "as unsupported"
        acc = sum(c["accepted"] == "yes" for c in cs)
        hon = sum(c["honoured"] == "yes" for c in cs)
        mix = sum(c["honoured"] == "mixed" for c in cs)
        ign = sum(c["honoured"] == "no" for c in cs)
        und = sum(c["honoured"] == "undetermined" for c in cs)
        oth = len(cs) - hon - mix - ign - und
        print(f"{prov:<11}{param:<20}{d:<12}{acc:>6}/{len(cs):<2}{hon:>9}{mix:>6}{ign:>6}{und:>7}{oth:>6}  "
              f"{(doc['claim'][:60] if doc and doc['claim'] else '—')}")

    # ---- the named cells ------------------------------------------------
    worst = [c for c in rows if c["label"] == WORST]
    bad = [c for c in rows if c["label"] == "DOCUMENTED, ACCEPTED, IGNORED"]
    print(f"\n{WORST}: {len(worst)} cell(s)")
    for c in worst:
        print(f"  {c['provider']}/{c['model']}  {c['parameter']}  collected {(c.get('collected') or {}).get('from', '?')}")
    print(f"\nDOCUMENTED, ACCEPTED, IGNORED: {len(bad)} cell(s)")
    for c in bad:
        print(f"  {c['provider']}/{c['model']}  {c['parameter']}  docs: \"{c['claim'][:70]}\"")
    print("\nlabels:", dict(Counter(c["label"] for c in rows)))
    unread = sorted(p for p in DOCUMENTED if p not in {c["provider"] for c in rows})
    if unread:
        print(f"documented but not yet measured (no key): {', '.join(unread)}")

    if args.cells:
        print(f"\n{'provider':<11}{'model':<32}{'parameter':<20}{'doc':<10}{'acc':<8}{'hon':<13}label")
        for c in rows:
            print(f"{c['provider']:<11}{c['model']:<32}{c['parameter']:<20}{str(c['documented']):<10}"
                  f"{c['accepted']:<8}{c['honoured']:<13}{c['label']}")
    if args.json:
        dest = resolve_out(args.json)
        dest.parent.mkdir(parents=True, exist_ok=True)
        from samplerconfound.paths import iso
        import time
        dest.write_text(json.dumps({"written": iso(time.time()), "documented": DOCUMENTED,
                                    "cells": rows}, indent=2) + "\n")
        print(f"wrote {show(dest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

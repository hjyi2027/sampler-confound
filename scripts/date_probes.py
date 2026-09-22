#!/usr/bin/env python3
"""Date every probe result from the calls it was made of.

Provider behaviour changes. A measurement without its date is an anecdote, and
"when the report was written" is not the date — a report can be rewritten from
old data (`--reassess` does exactly that). The date of a cell is when the
provider answered its calls, and the cache holds that: every stored response
carries `stored_at`. This script reconstructs each cell's requests, reads
those timestamps, and writes a `collected` window onto every cell and every
report that lacks one.

Three sources, in order of trust, each named in the output:

    cache    the calls' own stored_at — exact
    git      for the three runs that predate the cache (2026-09-11 and before),
             the author date of the commit that added the file
    (none)   left blank and listed

Idempotent; safe to rerun. Reports that already carry `collected` from the
probes themselves are left alone unless --force.

    python3 scripts/date_probes.py            # backfill everything
    python3 scripts/date_probes.py --check    # list what is undated, change nothing
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.adapters import ADAPTERS
from samplerconfound.cache import ResponseCache
from samplerconfound.paths import iso, show, window
from scripts.probe_distinguishability import EMPTY_TOKEN, FORCED_PROMPTS, NEGATIVE_SETTING, PROMPT, PROMPTS

MATRIX = ROOT / "runs" / "matrix"
LEGACY = [ROOT / "runs" / p for p in ("distinguish.json", "distinguish_multiprompt.json",
                                       "determinism.json", "negative_control.json")]
CACHE = ResponseCache(offline=True)
ALL_PROMPTS = {**PROMPTS, **FORCED_PROMPTS}


def entry(provider: str, body: dict, replicate: int) -> dict | None:
    wire, _ = ADAPTERS[provider].encode(body)
    return CACHE.get_entry({**wire, "_provider": provider}, replicate)


def stored_at(provider: str, body: dict, replicate: int) -> float | None:
    e = entry(provider, body, replicate)
    return e.get("stored_at") if e else None


def _norm(text: str) -> str:
    """The probe's own normalisation of a completion, so texts compare equal."""
    t = (text or "").strip().lower().rstrip(".!,")
    return t if t else EMPTY_TOKEN


def cell_window(provider, model, prompt_text, setting, n, max_tokens, effort, offset=0,
                expect: list[str] | None = None) -> dict:
    """The collection window of one arm — and ONLY if the cached responses are
    this arm's data. A cache entry for the same request can come from a later
    run (the matrix re-collected the legacy runs' temperature contrasts on
    09-20), and dating old data by a later call's timestamp would be exactly
    the falsification this script exists to prevent. When the cell retained
    its completions, they must match the cached texts as a multiset."""
    body = {"model": model, "messages": [{"role": "user", "content": prompt_text}],
            "max_tokens": max_tokens, **setting}
    if effort:
        body["reasoning_effort"] = effort
    entries = [entry(provider, body, offset + i) for i in range(n)]
    if expect is not None:
        got = sorted(_norm(ADAPTERS[provider].decode(e["response"]).text) for e in entries if e)
        if got != sorted(_norm(x) for x in expect):
            return {"from": "", "to": ""}
    return window(e.get("stored_at") for e in entries if e)


def git_date(path: Path) -> str:
    try:
        out = subprocess.run(["git", "log", "--diff-filter=A", "--format=%aI", "--", str(path)],
                             cwd=ROOT, capture_output=True, text=True).stdout.strip().splitlines()
        return out[-1][:16].replace("+", "Z") if out else ""
    except OSError:
        return ""


# --------------------------------------------------------------------------

def date_distinguish(path: Path, force: bool) -> str:
    d = json.loads(path.read_text())
    if d.get("collected", {}).get("from") and not force:
        return "already dated"
    prov = d.get("provider", "fireworks")
    mt, eff = d.get("max_tokens", 1024), d.get("reasoning_effort", "low")
    n = d["n_per_arm"]
    prompts = {**ALL_PROMPTS, **d.get("prompts", {}), **d.get("forced_prompts", {})}
    windows = []
    for r in d["results"]:
        if r.get("collected_from") and not force:
            windows.append(r["collected_from"]); windows.append(r["collected_to"]); continue
        text = prompts.get(r.get("prompt") or "word_prob", PROMPT)
        ct, co = r.get("completions_tight"), r.get("completions_open")
        if r["parameter"].startswith("null_"):                      # negative control pair k
            k = int(r["parameter"].split("_")[1])
            wa = cell_window(prov, r["model"], text, NEGATIVE_SETTING, n, mt, eff, offset=(2 * k) * n, expect=ct)
            wb = cell_window(prov, r["model"], text, NEGATIVE_SETTING, n, mt, eff, offset=(2 * k + 1) * n, expect=co)
        else:
            wa = cell_window(prov, r["model"], text, r["setting_a"], n, mt, eff, expect=ct)
            wb = cell_window(prov, r["model"], text, r["setting_b"], n, mt, eff, expect=co)
        fr = [w["from"] for w in (wa, wb) if w["from"]]; to = [w["to"] for w in (wa, wb) if w["to"]]
        r["collected_from"], r["collected_to"] = (min(fr) if fr else ""), (max(to) if to else "")
        if fr:
            windows += [r["collected_from"], r["collected_to"]]
    if windows:
        d["collected"] = {"from": min(windows), "to": max(windows), "source": "cache"}
    else:
        g = git_date(path)
        d["collected"] = {"from": g, "to": g, "source": "git (predates the cache)"}
    d.setdefault("written", iso(path.stat().st_mtime))
    path.write_text(json.dumps(d, indent=2, default=str) + "\n")
    # the checkpoint too, so a resume keeps the dates
    ck = path.with_suffix(".cells.jsonl")
    if ck.exists():
        by = {(r["model"], r.get("prompt", ""), r["parameter"]): (r.get("collected_from", ""), r.get("collected_to", ""))
              for r in d["results"]}
        lines = []
        for line in ck.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            k = (rec["model"], rec.get("prompt", ""), rec["parameter"])
            if k in by and (force or not rec.get("collected_from")):
                rec["collected_from"], rec["collected_to"] = by[k]
            lines.append(json.dumps(rec, default=str))
        tmp = ck.with_suffix(".jsonl.tmp"); tmp.write_text("\n".join(lines) + "\n"); os.replace(tmp, ck)
    return f"{d['collected']['from']} .. {d['collected']['to']} ({d['collected']['source']})"


def date_determinism(path: Path, force: bool) -> str:
    d = json.loads(path.read_text())
    if d.get("collected", {}).get("from") and not force:
        return "already dated"
    from scripts.probe_determinism import CONDITIONS, N, prompts
    P = prompts()
    mt, eff = d.get("max_tokens", 1024), d.get("reasoning_effort", "low")
    windows = []
    for r in d["rows"]:
        prov = r.get("provider", "fireworks")
        text = P.get(r["prompt"])
        ws = []
        for cond, extra in CONDITIONS.items():
            c = r["conditions"].setdefault(cond, {})
            body = {"model": r["model"], "messages": [{"role": "user", "content": text}],
                    "max_tokens": mt, "temperature": 0.0, **extra, "reasoning_effort": eff}
            ents = [entry(prov, body, i) for i in range(N)]
            texts = [ADAPTERS[prov].decode(e["response"]).text for e in ents if e]
            # the row keeps only the mode (first 200 chars); the cached mode must equal it
            from collections import Counter
            mode = Counter(texts).most_common(1)[0][0][:200] if texts else None
            w = window(e.get("stored_at") for e in ents if e) if (mode is not None and mode == c.get("modal_content")) \
                else {"from": "", "to": ""}
            c["collected"] = w
            if w["from"]:
                ws += [w["from"], w["to"]]
        r["collected"] = {"from": min(ws), "to": max(ws)} if ws else {"from": "", "to": ""}
        windows += ws
    if windows:
        d["collected"] = {"from": min(windows), "to": max(windows), "source": "cache"}
    else:
        g = git_date(path)
        d["collected"] = {"from": g, "to": g, "source": "git (predates the cache)"}
    d.setdefault("written", iso(path.stat().st_mtime))
    path.write_text(json.dumps(d, indent=2, default=str) + "\n")
    return f"{d['collected']['from']} .. {d['collected']['to']} ({d['collected']['source']})"


def date_from_git(path: Path, force: bool) -> str:
    """The pre-cache runs. Their calls were never cached, and a cache entry for
    the same request from a later run is not their data even when the text is
    identical (a deterministic model at temperature 0 says the same thing on
    09-11 and 09-20). The only honest date is the commit that added the file."""
    d = json.loads(path.read_text())
    if d.get("collected", {}).get("from") and not force:
        return "already dated"
    g = git_date(path)
    d["collected"] = {"from": g, "to": g, "source": "git author date of the adding commit (predates the cache)"}
    d.setdefault("written", g)
    for r in d.get("results", []):
        r["collected_from"] = r["collected_to"] = g
    for r in d.get("rows", []):
        r["collected"] = {"from": g, "to": g}
    path.write_text(json.dumps(d, indent=2, default=str) + "\n")
    return f"{g} (git)"


def date_undocumented(path: Path, force: bool) -> str:
    d = json.loads(path.read_text())
    from scripts.probe_undocumented import N, ONE_WORD, SENTENCE
    changed = []
    for r in d["rows"]:
        if (r.get("collected") or {}).get("from") and not force:
            continue
        prov, m = r["provider"], r["model"]
        base = {"max_tokens": 48, "reasoning_effort": "low"}
        ts = []
        for extra, prompt, n in (({"temperature": 0.0, "ignore_eos": True}, ONE_WORD, 3),
                                 ({"temperature": 0.0}, ONE_WORD, 1),
                                 ({"temperature": 1.0}, SENTENCE, N),
                                 ({"temperature": 1.0, "use_beam_search": True}, SENTENCE, N),
                                 ({"temperature": 0.0, "skip_special_tokens": False}, ONE_WORD, 3),
                                 ({"temperature": 1.0, "best_of": 2}, ONE_WORD, 1)):
            for i in range(n):
                ts.append(stored_at(prov, {"model": m, "messages": [{"role": "user", "content": prompt}],
                                           **base, **extra}, i))
        r["collected"] = window(ts)
        changed.append(f"{m} {r['collected']['from']}")
    fr = [r["collected"]["from"] for r in d["rows"] if (r.get("collected") or {}).get("from")]
    to = [r["collected"]["to"] for r in d["rows"] if (r.get("collected") or {}).get("to")]
    d["collected"] = {"from": min(fr), "to": max(to), "source": "cache"} if fr else {"from": "", "to": ""}
    d.setdefault("written", iso(path.stat().st_mtime))
    path.write_text(json.dumps(d, indent=2) + "\n")
    return "; ".join(changed) or "already dated"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true", help="recompute even where a date exists")
    args = ap.parse_args()
    undated = []

    def report(path, fn):
        if args.check:
            d = json.loads(path.read_text())
            ok = d.get("collected", {}).get("from")
            print(f"{'dated  ' if ok else 'UNDATED'} {show(path)}  {d.get('collected', {}).get('from', '')}")
            if not ok:
                undated.append(path)
            return
        print(f"{show(path):<60} {fn(path, args.force)}")

    for p in sorted(list(MATRIX.glob("*/*.json")) + [MATRIX / "undocumented.json"]):
        if not p.exists():
            continue
        name = p.name
        if name in ("matrix.json", "gap.json"):
            continue
        if name == "undocumented.json":
            report(p, date_undocumented)
        elif name == "determinism.json" or name.endswith(".det.json"):
            report(p, date_determinism)
        elif re.search(r"\.(dist-n\d+|neg-n\d+)\.json$", name) or re.fullmatch(r"[^.]+\.json", name):
            report(p, date_distinguish)
    for p in LEGACY:
        if p.exists():
            report(p, date_from_git)
    if args.check:
        print(f"\n{len(undated)} undated")
        return 1 if undated else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

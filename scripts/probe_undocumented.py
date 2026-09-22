#!/usr/bin/env python3
"""Parameters a provider accepts without documenting: are they honoured?

Fireworks validates request bodies strictly — an unknown field is a 400 "Extra
inputs are not permitted" — yet four vLLM SamplingParams names pass: best_of,
use_beam_search, ignore_eos, skip_special_tokens (checked 2026-09-22, see
documented.py). Accepted and undocumented is half of the worst case; this
probe supplies the other half with one decisive design per parameter:

    ignore_eos            a one-word answer at T=0 with max_tokens=48 must run
                          to the cap (finish_reason "length") if honoured
    use_beam_search       beam search is deterministic: 10 calls at T=1.0 must
                          agree if honoured, on a prompt where plain sampling
                          does not
    skip_special_tokens   =false must expose an end-of-turn marker in the text
    best_of               no observable signature from outside; recorded as
                          accepted and untestable, not as honoured or ignored

Every call goes through complete() and the cache like every other probe.

    python3 scripts/probe_undocumented.py --models glm-5p3-flash gpt-oss-120b kimi-k3
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.adapters import ADAPTERS
from samplerconfound.paths import iso, resolve_out, show, window
from samplerconfound.provider import DEFAULT_PROVIDER, complete, default_cache, load_key

ONE_WORD = "Reply with the single word: cat"
SENTENCE = "Write one short sentence about anything at all. Reply with the sentence only."
SPECIAL = re.compile(r"<\|[^|>]+\|>|</s>|<eos>|<end_of_turn>|\[/INST\]|<\|end\|>")
N = 10


def run(key, provider, model, effort):
    base = {"model": model, "max_tokens": 48, "reasoning_effort": effort} if effort else {"model": model, "max_tokens": 48}
    out = {"model": model, "provider": provider}

    when = []

    def get(prompt, rep, **extra):
        c, err = complete(key, {**base, "messages": [{"role": "user", "content": prompt}], **extra},
                          rep, provider=provider, timeout=120)
        if c is not None:
            when.append(c.collected_at)
        return c, err

    # ignore_eos: does a one-word answer run to the cap?
    res = []
    for rep in range(3):
        c, err = get(ONE_WORD, rep, temperature=0.0, ignore_eos=True)
        res.append(err or (c.finish_reason, c.completion_tokens))
    ctrl, _ = get(ONE_WORD, 0, temperature=0.0)
    out["ignore_eos"] = {"with": res, "control_finish": ctrl.finish_reason if ctrl else None,
                         "control_tokens": ctrl.completion_tokens if ctrl else None}
    hits = sum(1 for r in res if isinstance(r, tuple) and r[0] == "length")
    errs = [r for r in res if isinstance(r, str)]
    out["ignore_eos"]["verdict"] = (
        "rejected" if any(e.startswith("rejected") for e in errs)
        else f"error ({errs[0]})" if errs                        # accepted, then the server failed
        else "undetermined (control already at cap)" if ctrl and ctrl.finish_reason == "length"
        else "honoured" if hits == 3 else "ignored" if hits == 0 else "mixed")

    # use_beam_search: does T=1.0 become deterministic?
    def agreement(**extra):
        texts, errs = [], []
        for rep in range(N):
            c, err = get(SENTENCE, rep, temperature=1.0, **extra)
            (errs if err else texts).append(err or c.text.strip())
        if errs:
            return None, errs[0]
        return Counter(texts).most_common(1)[0][1] / len(texts), None
    plain, _ = agreement()
    beam, err = agreement(use_beam_search=True)
    out["use_beam_search"] = {"plain_agreement": plain, "beam_agreement": beam, "error": err}
    out["use_beam_search"]["verdict"] = ("rejected" if err and err.startswith("rejected")
                                         else "n/a (already deterministic)" if plain == 1.0
                                         else "honoured" if beam == 1.0
                                         else "ignored" if beam is not None and beam <= plain + 0.2
                                         else "undetermined")

    # skip_special_tokens=false: does a marker appear?
    seen = []
    for rep in range(3):
        c, err = get(ONE_WORD, rep, temperature=0.0, skip_special_tokens=False)
        seen.append(err or bool(SPECIAL.search(c.text + c.reasoning)))
    out["skip_special_tokens"] = {"marker_seen": seen}
    out["skip_special_tokens"]["verdict"] = ("rejected" if any(isinstance(x, str) and x.startswith("rejected") for x in seen)
                                             else "honoured" if all(x is True for x in seen)
                                             else "ignored" if all(x is False for x in seen) else "mixed")

    # best_of: accepted?
    c, err = get(ONE_WORD, 0, temperature=1.0, best_of=2)
    out["best_of"] = {"accepted": err is None, "error": err,
                      "verdict": "rejected" if err and err.startswith("rejected") else "accepted, untestable from outside"}
    out["collected"] = window(when)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--provider", default=DEFAULT_PROVIDER, choices=sorted(ADAPTERS))
    ap.add_argument("--effort", default="low")
    ap.add_argument("--out", type=Path, default=ROOT / "runs" / "matrix" / "undocumented.json")
    args = ap.parse_args()
    key = load_key(args.provider)
    rows = []
    print(f"{'model':<30}{'ignore_eos':<22}{'use_beam_search':<30}{'skip_special_tokens':<12}best_of")
    for m in args.models:
        r = run(key, args.provider, m, args.effort)
        rows.append(r)
        print(f"{m:<30}{r['ignore_eos']['verdict']:<22}"
              f"{r['use_beam_search']['verdict']:<30}{r['skip_special_tokens']['verdict']:<12}{r['best_of']['verdict']}")
    print(f"[{default_cache().stats}]")
    dest = resolve_out(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    prior = json.loads(dest.read_text()) if dest.exists() else {"rows": []}
    keep = [x for x in prior["rows"] if (x["provider"], x["model"]) not in {(r["provider"], r["model"]) for r in rows}]
    allrows = keep + rows
    fr = [r["collected"]["from"] for r in allrows if r.get("collected", {}).get("from")]
    to = [r["collected"]["to"] for r in allrows if r.get("collected", {}).get("to")]
    dest.write_text(json.dumps({"written": iso(time.time()),
                                "collected": {"from": min(fr), "to": max(to), "source": "cache"} if fr else {"from": "", "to": ""},
                                "rows": allrows}, indent=2) + "\n")
    print(f"wrote {show(dest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

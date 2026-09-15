#!/usr/bin/env python3
"""Is each decoding parameter honoured? One two-sample test per (model, parameter).

Replaces the yes/no heuristic in `probe_fireworks.py`. For each pair, hold
everything fixed, vary ONE parameter between two settings far apart in its range,
draw N completions at each, and test whether the two output distributions are
distinguishable by permutation. See `samplerconfound/distinguish.py` for why the
null is exactly the failure mode being detected.

    python3 scripts/probe_distinguishability.py --models gpt-oss-120b --n 40
    python3 scripts/probe_distinguishability.py --all --n 40 --out runs/distinguish.json

The prompt is deliberately high-entropy and trivial: power to detect an effect is
highest where there is entropy to lose, and short completions keep the cost of
2 x N x 4 calls per model to cents. This measures whether a parameter does
anything at all, not how much it moves task accuracy — those are different
questions and the second needs the task.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.distinguish import (
    Distinguishability,
    assess_parameter,
    holm_adjust,
    interpret,
    positive_control,
)
from samplerconfound.paths import resolve_out, show

BASE = "https://api.fireworks.ai/inference/v1/chat/completions"
PREFIX = "accounts/fireworks/models/"

# A prompt SET, not a prompt. One prompt is one sample of prompt space: it can be
# degenerate for a particular model, and a one-word noun with a strong mode was
# already shown not to predict task-level sampler sensitivity. Each (model,
# parameter) is tested on every prompt, reported per prompt, and the verdict is a
# majority over the prompts that passed their own positive control — so a single
# degenerate prompt drops out of the denominator instead of casting a vote.
#
# Chosen empirically on 2026-09-11 by measuring open-arm entropy (temperature
# 1.0, 20 samples) on three models with different behaviour. Kept: every prompt
# with at least 1.9 bits on ALL three. Rejected, each a demonstration of why one
# prompt cannot be trusted:
#
#   math_fact  "State one true fact about numbers"  0.57 bits on nemotron (3/20)
#   integer    "Pick an integer 1-1000"             0.00 bits on gpt-oss-120b (1/20)
#   city       "Name a city"                        0.29 bits on deepseek (2/20)
#
# Each looked fine on at least one model and would have silently carried a
# verdict on another. word_prob is both the highest-entropy prompt on every
# model (4.2-4.3 bits) and in the task domain the sweep actually measures.
PROMPTS = {
    "word_prob": "Write a one-sentence arithmetic word problem suitable for a ten-year-old. Reply with the problem only.",
    "sentence":  "Write one short sentence about anything at all. Reply with the sentence only.",
    "opener":    "Write the opening three words of a story. Reply with those three words only.",
    "question":  "Ask one question about anything. Reply with the question only.",
}
PROMPT = PROMPTS["word_prob"]    # default for single-prompt calls (calibration, negative control)

# (parameter, TIGHT setting, OPEN setting). One parameter varies; everything else
# is identical between the arms, including temperature, which must be high enough
# for a truncation parameter to have anything to truncate — at temperature 0 the
# distribution is already a point mass and top_p/top_k/min_p cannot show an effect
# even when honoured. Tight is the one that should narrow
# the distribution if the parameter is honoured; the primary test is one-sided in
# that direction. top_k tops out at 100 on this provider — 200 is rejected — so
# the open arm uses the documented maximum rather than a value that errors.
CONTRASTS = [
    ("temperature", {"temperature": 0.0}, {"temperature": 1.5}),
    ("top_p", {"temperature": 1.0, "top_p": 0.01}, {"temperature": 1.0, "top_p": 1.0}),
    ("top_k", {"temperature": 1.0, "top_k": 1}, {"temperature": 1.0, "top_k": 100}),
    ("min_p", {"temperature": 1.0, "min_p": 0.9}, {"temperature": 1.0, "min_p": 0.0}),
]

MAX_RETRIES = 5
BACKOFF = 2.0
_lock = threading.Lock()


def load_key() -> str:
    key = os.environ.get("FIREWORKS_API_KEY")
    if not key and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("FIREWORKS_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        raise SystemExit("no FIREWORKS_API_KEY (env or .env)")
    return key


def one(key: str, model: str, params: dict, max_tokens: int, effort: str | None,
        prompt: str | None = None):
    """Return (completion, error, usage). Completion is None on failure."""
    body = {"model": PREFIX + model,
            "messages": [{"role": "user", "content": prompt or PROMPT}],
            "max_tokens": max_tokens, **params}
    if effort:
        body["reasoning_effort"] = effort
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(BASE, headers={"Authorization": f"Bearer {key}"},
                              json=body, timeout=180)
        except requests.RequestException:
            if attempt == MAX_RETRIES - 1:
                return None, "network", {}
            time.sleep(BACKOFF * 2 ** attempt)
            continue
        if r.status_code == 200:
            d = r.json()
            txt = (d["choices"][0]["message"].get("content") or "").strip().lower()
            return txt.rstrip(".!,"), None, d.get("usage", {})
        if r.status_code == 400:
            # A rejected parameter is a loud, safe outcome and a different finding
            # from an ignored one. Do not retry it.
            try:
                msg = r.json()["error"]["message"][:140]
            except Exception:
                msg = r.text[:140]
            return None, f"rejected: {msg}", {}
        if r.status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
            time.sleep(BACKOFF * 2 ** attempt)
            continue
        return None, f"http {r.status_code}", {}
    return None, "exhausted", {}


def collect(key, model, params, n, workers, max_tokens, effort, prompt=None):
    """Returns (completions, errors, usage, n_empty).

    Empty completions are counted, not silently dropped. qwen3p7-plus returns
    HTTP 200 with empty content whenever max_tokens cuts it off before it stops
    reasoning — 739 reasoning tokens on this prompt, so 256 yields nothing at
    all. Dropping those quietly produced "n=0, insufficient" with no indication
    that every call had in fact succeeded and been billed.
    """
    out, errs, usage = [], [], []
    n_empty = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(one, key, model, params, max_tokens, effort, prompt)
                for _ in range(n)]
        for f in as_completed(futs):
            txt, err, u = f.result()
            if err:
                errs.append(err)
            elif txt:
                out.append(txt)
                usage.append(u)
            else:
                n_empty += 1
                usage.append(u)
    return out, errs, usage, n_empty


# The setting for the negative control. Temperature 1.0 with nothing else is the
# open arm of every truncation test — the highest-entropy condition, where a
# spurious difference between two identical arms has the most room to appear.
# At temperature 0 a deterministic model gives two constant arms and the test
# reports "insufficient", which calibrates nothing.
NEGATIVE_SETTING = {"temperature": 1.0}


def negative_control_run(key, models, args) -> int:
    """Two arms, identical settings, collected sequentially like the real test.

    Under the null the two samples are exchangeable, so the one-sided dH p-value
    should be uniform and the rejection rate at 0.05 should be 5%. If it is not,
    something in the COLLECTION breaks exchangeability — provider state drifting
    between the first arm and the second, say — and every positive result in the
    main grid inherits that inflation. The simulated calibration in the tests
    used iid categorical draws and cannot see this.

    Arms are collected in the same order as the real test (A fully, then B) on
    purpose: the point is to expose whatever the real test is exposed to.
    """
    import math

    rows, tokens, t0 = [], 0, time.time()
    for model in models:
        print(f"\n=== {model} — negative control, {args.negative} identical pairs ===")
        for k in range(args.negative):
            a, ea, ua, empty_a = collect(key, model, NEGATIVE_SETTING, args.n,
                                         args.workers, args.max_tokens, args.effort)
            b, eb, ub, empty_b = collect(key, model, NEGATIVE_SETTING, args.n,
                                         args.workers, args.max_tokens, args.effort)
            tokens += sum(u.get("completion_tokens", 0) for u in ua + ub)
            r = assess_parameter(model, f"null_{k}", NEGATIVE_SETTING, NEGATIVE_SETTING,
                                 a, b, n_permutations=args.permutations,
                                 random_state=k)
            r.completions_tight, r.completions_open = a, b
            if r.status != "ok":
                print(f"  pair {k}: {r.status}  {r.detail[:70]}")
            else:
                print(f"  pair {k}: dH={r.dh:+.2f} p={r.dh_p:.3f}   "
                      f"TV={r.tv:.3f} p={r.tv_p:.3f}   support {r.support_tight}/{r.support_open}")
            rows.append(r)

    usable = [r for r in rows if r.status == "ok"]
    # A pair where both arms are (near) all-unique has H = log2(n) on both sides,
    # dH identically zero, and p -> 1 by construction. It cannot produce a false
    # positive and so says nothing about the rate; counting it flatters the
    # calibration. nemotron-lightning at temperature 1.0 is this case.
    degenerate = [r for r in usable
                  if r.support_tight >= r.n_tight - 1 and r.support_open >= r.n_open - 1]
    ok = [r for r in usable if r not in degenerate]
    n = len(ok)
    if degenerate:
        print(f"\n  excluded {len(degenerate)} degenerate pair(s) — both arms all-unique, "
              f"statistic has no range: {sorted({r.model for r in degenerate})}")
    if not n:
        print("no informative negative-control pairs")
        return 1

    def wilson(k, n, z=1.96):
        p = k / n
        den = 1 + z * z / n
        c = (p + z * z / (2 * n)) / den
        h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
        return c - h, c + h

    fp_dh = sum(r.dh_p < 0.05 for r in ok)
    fp_tv = sum(r.tv_p < 0.05 for r in ok)
    lo_d, hi_d = wilson(fp_dh, n)
    lo_t, hi_t = wilson(fp_tv, n)
    dhs = np.array([r.dh for r in ok])
    ps = np.array([r.dh_p for r in ok])

    print(f"\n=== NEGATIVE CONTROL SUMMARY: {n} informative pairs at identical settings ===")
    print(f"  dH one-sided  rejections at 0.05: {fp_dh}/{n} = {fp_dh/n:.1%}  "
          f"95% CI [{lo_d:.1%}, {hi_d:.1%}]   nominal 5%")
    print(f"  TV two-sided  rejections at 0.05: {fp_tv}/{n} = {fp_tv/n:.1%}  "
          f"95% CI [{lo_t:.1%}, {hi_t:.1%}]   nominal 5%")
    print(f"  dH under the null: mean {dhs.mean():+.3f}, sd {dhs.std(ddof=1):.3f}  "
          f"(should centre on zero)")
    print(f"  dH p-values: mean {ps.mean():.3f}, min {ps.min():.3f}  "
          f"(uniform => mean 0.5)")
    fp10 = sum(r.dh_p < 0.10 for r in ok)
    print(f"  dH rejections at 0.10: {fp10}/{n} = {fp10/n:.1%}   nominal 10%")
    verdict = ("calibrated" if hi_d >= 0.05 >= lo_d or fp_dh / n <= 0.10
               else "INFLATED — positives in the main grid are suspect")
    print(f"  verdict: {verdict}")
    print(f"  ({n} pairs bound the rate below {hi_d:.0%}; pinning it near 5% needs "
          "several hundred)")
    print(f"\n  {tokens:,} output tokens, {(time.time()-t0)/60:.1f} min")

    if args.out:
        dest = resolve_out(args.out)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps({
            "setting": NEGATIVE_SETTING, "n_per_arm": args.n, "pairs_per_model": args.negative,
            "results": [r.to_dict() for r in rows],
            "false_positive_rate": {"dh": fp_dh / n, "dh_ci": [lo_d, hi_d],
                                    "tv": fp_tv / n, "tv_ci": [lo_t, hi_t], "n": n},
        }, indent=2, default=str) + "\n")
        print(f"wrote {show(dest)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=None)
    ap.add_argument("--all", action="store_true",
                    help="every model in MODEL_CANDIDATES that is still available")
    ap.add_argument("--n", type=int, default=40, help="completions per arm")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-tokens", type=int, default=1024,
                    help="must exceed the model's reasoning budget or content is empty")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--permutations", type=int, default=10_000)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--prompts", nargs="+", default=None,
                    help=f"subset of prompt ids to run; default all of {list(PROMPTS)}")
    ap.add_argument("--negative", type=int, default=0, metavar="K",
                    help="negative control: for each model collect K pairs of arms "
                         "at IDENTICAL settings and test them. Calibrates the "
                         "empirical false-positive rate on real output.")
    args = ap.parse_args()

    if args.all:
        from samplerconfound.config import MODEL_CANDIDATES
        models = [c["id"].split("/")[-1] for c in MODEL_CANDIDATES
                  if c.get("available") is not False]
    else:
        models = args.models or []
    if not models:
        raise SystemExit("give --models or --all")

    key = load_key()
    results, tokens = [], 0
    t0 = time.time()

    if args.negative:
        return negative_control_run(key, models, args)

    prompt_ids = args.prompts or list(PROMPTS)
    per_prompt_results: dict[str, list] = {pid: [] for pid in prompt_ids}

    # Checkpoint every completed cell to JSONL and skip finished cells on
    # restart. The first multi-prompt run was killed by the OS after three hours
    # with every one of its ~7,000 completed calls held in memory and nothing on
    # disk — the identical mistake the sweep runner was built to avoid. A cell is
    # ~80 API calls, so the most a kill can now cost is one cell.
    ckpt = resolve_out(args.out).with_suffix(".cells.jsonl") if args.out else None
    done: dict[tuple, dict] = {}
    if ckpt and ckpt.exists():
        for line in ckpt.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue                      # torn final line: redo that cell
            done[(rec["model"], rec["prompt"], rec["parameter"])] = rec
        print(f"resuming: {len(done)} cells already on disk in {show(ckpt)}")

    def restore(rec: dict):
        r = Distinguishability(model=rec["model"], parameter=rec["parameter"],
                               setting_a=rec["setting_a"], setting_b=rec["setting_b"])
        for k, v in rec.items():
            if hasattr(r, k) and k != "excess":
                setattr(r, k, v)
        return r

    for model in models:
        for pid in prompt_ids:
            print(f"\n=== {model} — prompt '{pid}' ===")
            for param, sa, sb in CONTRASTS:
                if (model, pid, param) in done:
                    r = restore(done[(model, pid, param)])
                    print(f"  {param:<12} (from checkpoint) status={r.status}")
                    per_prompt_results[pid].append(r)
                    results.append(r)
                    continue
                a, ea, ua, empty_a = collect(key, model, sa, args.n, args.workers,
                                             args.max_tokens, args.effort, PROMPTS[pid])
                b, eb, ub, empty_b = collect(key, model, sb, args.n, args.workers,
                                             args.max_tokens, args.effort, PROMPTS[pid])
                tokens += sum(u.get("completion_tokens", 0) for u in ua + ub)

                r = assess_parameter(model, param, sa, sb, a, b,
                                     n_permutations=args.permutations)
                r.completions_tight, r.completions_open = a, b
                r.prompt = pid
                rejected = [e for e in ea + eb if e.startswith("rejected")]
                if rejected:
                    r.status, r.detail = "rejected", rejected[0]
                if (empty_a or empty_b) and r.status == "insufficient":
                    r.detail = (f"{empty_a + empty_b}/{2 * args.n} calls returned empty "
                                f"content — billed, but max_tokens ({args.max_tokens}) "
                                "cut them off before any content. Raise --max-tokens.")

                if r.status == "rejected":
                    print(f"  {param:<12} REJECTED   {r.detail[:76]}")
                elif r.status == "insufficient":
                    print(f"  {param:<12} NO POWER   n={r.n_tight}/{r.n_open}  {r.detail[:56]}")
                else:
                    print(f"  {param:<12} H tight={r.entropy_tight:.2f} open={r.entropy_open:.2f}"
                          f"  dH={r.dh:+.2f} (null {r.dh_null_mean:+.2f}) p={r.dh_p:.4f}"
                          f"   TV={r.tv:.3f} p={r.tv_p:.4f}")
                per_prompt_results[pid].append(r)
                results.append(r)
                if ckpt:
                    ckpt.parent.mkdir(parents=True, exist_ok=True)
                    with ckpt.open("a") as fh:
                        fh.write(json.dumps(r.to_dict(), default=str) + "\n")
                        fh.flush()
                        os.fsync(fh.fileno())

    # ---- per-prompt positive control, then the cross-prompt aggregate -------
    from samplerconfound.distinguish import aggregate

    power_by_prompt = {pid: positive_control(rs) for pid, rs in per_prompt_results.items()}
    adj_by_prompt = {pid: holm_adjust(rs) for pid, rs in per_prompt_results.items()}

    print(f"\nPOSITIVE CONTROL BY PROMPT (fraction of open-arm entropy temperature removes)")
    print(f"{'model':<32}" + "".join(f"{pid:>11}" for pid in prompt_ids))
    for model in models:
        line = f"{model:<32}"
        for pid in prompt_ids:
            mp = power_by_prompt[pid].get(model)
            if mp is None or not np.isfinite(mp.fraction_removed):
                line += f"{'—':>11}"
            else:
                line += f"{mp.fraction_removed:>9.0%}{'' if mp.powered else '!':>2}"
        print(line)
    print("  ! = control failed on that prompt; the prompt drops out of that model's verdicts")

    aggregates = []
    print(f"\nVERDICTS — per prompt, then majority over prompts whose control passed")
    print(f"{'model':<32}{'param':<12}" + "".join(f"{pid:>11}" for pid in prompt_ids)
          + f"{'powered':>9}{'dist':>6}  verdict")
    sym = {"distinguishable": "yes", "no effect seen": "NO", "underpowered": "?",
           "rejected": "rej", "insufficient": "n/a"}
    for model in models:
        for param, _, _ in CONTRASTS:
            if param == "temperature":
                continue                      # it is the control, not a test subject
            pv = {}
            for pid in prompt_ids:
                r = next((x for x in per_prompt_results[pid]
                          if x.model == model and x.parameter == param), None)
                if r is None:
                    continue
                pv[pid] = interpret(r, power_by_prompt[pid],
                                    adj_by_prompt[pid].get((model, param), 1.0))
            agg = aggregate(pv, model, param)
            aggregates.append(agg)
            line = f"{model:<32}{param:<12}"
            for pid in prompt_ids:
                line += f"{sym.get(pv.get(pid, ''), '—'):>11}"
            line += f"{agg.n_powered:>9}{agg.n_distinguishable:>6}  {agg.verdict}"
            print(line)

    print(f"\n{len(results)} tests over {len(prompt_ids)} prompts, {tokens:,} output tokens, "
          f"{(time.time()-t0)/60:.1f} min")
    print("p_holm is Holm-Bonferroni across the whole grid; the claim is per-cell, "
          "so the family-wise rate is the relevant one.")

    if args.out:
        dest = resolve_out(args.out)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(
            {"prompt": PROMPT, "n_per_arm": args.n,
             "permutations": args.permutations,
             "results": [r.to_dict() for r in results],
             "p_holm_by_prompt": {pid: {f"{k[0]}|{k[1]}": v for k, v in a.items()}
                                  for pid, a in adj_by_prompt.items()},
             "prompts": {pid: PROMPTS[pid] for pid in prompt_ids},
             "positive_control_by_prompt": {pid: {m: mp.to_dict() for m, mp in pw.items()}
                                            for pid, pw in power_by_prompt.items()},
             "aggregates": [a.to_dict() for a in aggregates]},
            indent=2, default=str) + "\n")
        print(f"wrote {show(dest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

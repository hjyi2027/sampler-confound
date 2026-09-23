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
from collections import Counter
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

import functools
print = functools.partial(print, flush=True)   # noqa: A001 — progress lands in logs under nohup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.distinguish import (
    Aggregate,
    Distinguishability,
    assess_parameter,
    holm_adjust,
    interpret,
    positive_control,
)
from samplerconfound.paths import iso, resolve_out, show, window
from samplerconfound.adapters import ADAPTERS
from samplerconfound.provider import DEFAULT_PROVIDER, complete, default_cache, load_key
from samplerconfound.ratelimit import pacer

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

# The penalty parameters only act on tokens that have already appeared, and a
# one-sentence reply has few repeats: a null on the prompts above says the
# penalty had nothing to act on, not that it was ignored. So the penalties are
# also tested on a prompt that FORCES repetition. With the penalty off the reply
# is one constant string; with an honoured penalty at its maximum the model
# cannot produce it. This prompt is its own positive control — a null here is
# "the provider is not applying the parameter" — and it is run only for the
# contrasts listed in FORCED_FOR, so it does not change the truncation tests.
FORCED_PROMPTS = {
    "repeat": "Repeat the word 'yes' twenty times, separated by spaces. Reply with only that.",
}
FORCED_FOR = {"repetition_penalty", "frequency_penalty", "presence_penalty"}

# (parameter, TIGHT setting, OPEN setting, primary statistic). One parameter
# varies; everything else is identical between the arms, including temperature,
# which must be high enough for a truncation parameter to have anything to
# truncate — at temperature 0 the distribution is already a point mass and
# top_p/top_k/min_p cannot show an effect even when honoured.
#
# Every sampling parameter the reference provider documents is here: the four
# truncation samplers, typical_p, the three penalties and mirostat. For a
# truncation parameter "tight" is the arm that should narrow the distribution
# and the primary test is the one-sided entropy drop. A penalty has no such
# direction — a strong repetition penalty can raise or lower the entropy of a
# one-sentence reply — so its primary is the two-sided total variation, and the
# arm labels are just "off" and "on". top_k tops out at 100 on this provider —
# 200 is rejected — so the open arm uses the documented maximum rather than a
# value that errors. Whether another provider accepts each of these is not
# assumed: the adapter sends it and the provider's 4xx becomes "rejected".
T = {"temperature": 1.0}
CONTRASTS = [
    ("temperature", {"temperature": 0.0}, {"temperature": 1.5}, "dh"),
    ("top_p", {**T, "top_p": 0.01}, {**T, "top_p": 1.0}, "dh"),
    ("top_k", {**T, "top_k": 1}, {**T, "top_k": 100}, "dh"),
    ("min_p", {**T, "min_p": 0.9}, {**T, "min_p": 0.0}, "dh"),
    ("typical_p", {**T, "typical_p": 0.1}, {**T, "typical_p": 1.0}, "dh"),
    ("mirostat", {**T, "mirostat_target": 1.0, "mirostat_lr": 0.1}, {**T}, "dh"),
    ("repetition_penalty", {**T, "repetition_penalty": 1.0}, {**T, "repetition_penalty": 2.0}, "tv"),
    ("frequency_penalty", {**T, "frequency_penalty": 0.0}, {**T, "frequency_penalty": 2.0}, "tv"),
    ("presence_penalty", {**T, "presence_penalty": 0.0}, {**T, "presence_penalty": 2.0}, "tv"),
]
CONTRAST_NAMES = [c[0] for c in CONTRASTS]

_lock = threading.Lock()


def one(key: str, model: str, params: dict, max_tokens: int, effort: str | None,
        prompt: str | None = None, replicate: int = 0, provider: str = DEFAULT_PROVIDER):
    """Return (completion, error, completion_tokens). Completion is None on failure.

    `replicate` distinguishes the N identical requests an arm sends; without it
    the cache would answer every one from the first and the distribution being
    sampled would collapse to a point.
    """
    body = {"model": model,
            "messages": [{"role": "user", "content": prompt or PROMPT}],
            "max_tokens": max_tokens, **params}
    if effort:
        body["reasoning_effort"] = effort
    c, err = complete(key, body, replicate, provider=provider, timeout=120)
    if err:
        return None, err, 0, None
    # If the adapter had no wire form for a parameter of THIS arm, the arm was
    # not the setting it claims to be; both arms would then be identical and the
    # test would report "no effect" about a parameter that was never sent.
    missing = set(params) & set(c.dropped)
    if missing:
        return None, f"unsupported: {sorted(missing)} has no wire form on {provider}", 0, None
    txt = c.text.strip().lower()
    return txt.rstrip(".!,"), None, c.completion_tokens or 0, c.collected_at


def collect(key, model, params, n, workers, max_tokens, effort, prompt=None,
            replicate_offset=0, provider=DEFAULT_PROVIDER):
    """Returns (completions, errors, usage, n_empty, collected_at list).

    Empty completions are counted, not silently dropped. qwen3p7-plus returns
    HTTP 200 with empty content whenever max_tokens cuts it off before it stops
    reasoning — 739 reasoning tokens on this prompt, so 256 yields nothing at
    all. Dropping those quietly produced "n=0, insufficient" with no indication
    that every call had in fact succeeded and been billed.
    """
    out, errs, usage, when = [], [], [], []
    n_empty = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Replicate indices are part of the cache key. `replicate_offset` lets a
        # caller collect two arms with IDENTICAL bodies — the negative control —
        # without the second being served from the first.
        futs = [pool.submit(one, key, model, params, max_tokens, effort, prompt,
                            replicate_offset + i, provider)
                for i in range(n)]
        for f in as_completed(futs):
            txt, err, u, t = f.result()
            when.append(t)
            if err:
                errs.append(err)
            else:
                # An empty completion is an outcome of the setting, not a failed
                # call, and it is NOT missing at random: at high temperature the
                # reasoning trace rambles past max_tokens and the model emits
                # nothing. Dropping empties censored the open arm by exactly the
                # variable under test — minimax-m3 lost 80 of 160 open-arm
                # samples on the temperature contrast in the first run, and the
                # entropy of what survived was not the entropy of the setting.
                # Empties stay in the sample as a distinguished token.
                out.append(txt if txt else EMPTY_TOKEN)
                if not txt:
                    n_empty += 1
                usage.append(u)
    return out, errs, usage, n_empty, when


# The setting for the negative control. Temperature 1.0 with nothing else is the
# open arm of every truncation test — the highest-entropy condition, where a
# spurious difference between two identical arms has the most room to appear.
# At temperature 0 a deterministic model gives two constant arms and the test
# reports "insufficient", which calibrates nothing.
NEGATIVE_SETTING = {"temperature": 1.0}

# Sentinel for a completion that came back empty. Kept in the sample so that the
# arm's size is the number of CALLS, not the number of non-empty returns.
#
# This is not censoring to be engineered away with a bigger max_tokens. At
# temperature 1.5, minimax-m3 hits WHATEVER cap is set — median completion
# tokens equal to max_tokens at 1024, 2048 and 4096 alike — because the reasoning
# trace does not terminate. And the direction is not even consistent: deepseek at
# top_p=0.01 empties MORE than at top_p=1.0, because near-greedy reasoning loops.
# Either way the empty return is what that setting produces, so it stays in the
# distribution as a token. A test that drops it measures the entropy of the
# completions that happened to finish, which is not the entropy of the setting.
EMPTY_TOKEN = "<empty>"


def _dated(cells) -> dict:
    """The collection window over cells, from their own stamps."""
    froms = [c.collected_from for c in cells if c.collected_from]
    tos = [c.collected_to for c in cells if c.collected_to]
    return {"from": min(froms) if froms else "", "to": max(tos) if tos else ""}


def _prior(out, key):
    """A list field from the report this run is about to overwrite."""
    try:
        return list(json.loads(resolve_out(out).read_text()).get(key) or [])
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def resolve_models(args) -> list[str]:
    """--models as given, or --all from the candidate list.

    The candidate list is the frozen Fireworks grid, so --all is only meaningful
    there; on any other provider the models are named explicitly, since which
    models a provider serves is not something this repo can know in advance.
    """
    if args.all:
        if args.provider != DEFAULT_PROVIDER:
            raise SystemExit(f"--all lists the {DEFAULT_PROVIDER} candidates; "
                             f"give --models for {args.provider}")
        from samplerconfound.config import MODEL_CANDIDATES
        models = [c["id"].split("/")[-1] for c in MODEL_CANDIDATES
                  if c.get("available") is not False]
    else:
        models = args.models or []
    if not models:
        raise SystemExit("give --models or --all")
    return models


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
            # Both arms have the same body, so they MUST use disjoint replicate
            # ranges or the second arm is a cache echo of the first and the
            # control passes trivially.
            a, ea, ua, empty_a, ta = collect(key, model, NEGATIVE_SETTING, args.n,
                                         args.workers, args.max_tokens, args.effort,
                                         replicate_offset=(2 * k) * args.n,
                                         provider=args.provider)
            b, eb, ub, empty_b, tb = collect(key, model, NEGATIVE_SETTING, args.n,
                                         args.workers, args.max_tokens, args.effort,
                                         replicate_offset=(2 * k + 1) * args.n,
                                         provider=args.provider)
            tokens += sum(ua + ub)
            r = assess_parameter(model, f"null_{k}", NEGATIVE_SETTING, NEGATIVE_SETTING,
                                 a, b, n_permutations=args.permutations,
                                 random_state=k)
            r.completions_tight, r.completions_open = a, b
            w = window(ta + tb)
            r.collected_from, r.collected_to = w["from"], w["to"]
            if r.status != "ok":
                print(f"  pair {k}: {r.status}  {r.detail[:70]}")
            else:
                print(f"  pair {k}: dH={r.dh:+.2f} p={r.dh_p:.3f}   "
                      f"TV={r.tv:.3f} p={r.tv_p:.3f}   support {r.support_tight}/{r.support_open}")
            rows.append(r)

    # A pair where both arms are (near) all-unique has H = log2(n) on both sides,
    # dH identically zero, and p -> 1 by construction. It cannot produce a false
    # positive and so says nothing about the rate; counting it flatters the
    # calibration. nemotron-lightning at temperature 1.0 is this case.
    # assess_parameter now reports such a pair as insufficient itself.
    degenerate = [r for r in rows if r.status == "insufficient" and "all-unique" in r.detail]
    ok = [r for r in rows if r.status == "ok"]
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
            "provider": args.provider,
            "max_tokens": args.max_tokens, "reasoning_effort": args.effort,
            "collected": _dated(rows), "written": iso(time.time()),
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
    ap.add_argument("--retry-failed", action="store_true",
                    help="re-run cells checkpointed as 'transport' (calls the provider did not finish)")
    ap.add_argument("--reassess", action="store_true",
                    help="re-run the statistics on every checkpointed cell's retained "
                         "completions and rewrite the checkpoint and report; no API calls. "
                         "For when the test changes after the data were collected.")
    ap.add_argument("--params", nargs="+", choices=CONTRAST_NAMES,
                    help="subset of contrasts to RUN; temperature (the control) is always "
                         "included, and cells already checkpointed are kept whatever this says")
    ap.add_argument("--provider", default=DEFAULT_PROVIDER, choices=sorted(ADAPTERS),
                    help="which provider serves --models; the adapter handles the rest")
    args = ap.parse_args()

    models = resolve_models(args)
    key = load_key(args.provider)
    results, tokens = [], 0
    t0 = time.time()

    if args.negative:
        return negative_control_run(key, models, args)

    prompt_ids = args.prompts or list(PROMPTS)
    forced_ids = [pid for pid in FORCED_PROMPTS if not args.prompts or pid in args.prompts]
    all_prompts = {**PROMPTS, **FORCED_PROMPTS}
    selected = set(args.params or CONTRAST_NAMES) | {"temperature"}
    if args.reassess:
        selected = set()                  # re-derive what is on disk; run nothing
    per_prompt_results: dict[str, list] = {pid: [] for pid in prompt_ids + forced_ids}

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
            done[(rec.get("provider", DEFAULT_PROVIDER), rec["model"],
                  rec["prompt"], rec["parameter"])] = rec
        print(f"resuming: {len(done)} cells already on disk in {show(ckpt)}")

    if args.reassess and ckpt and done:
        # Re-derive every stored cell from its retained completions with the
        # CURRENT test, then rewrite the checkpoint atomically. The completions
        # are the data; the verdicts are derived and may be re-derived.
        primaries = {c[0]: c[3] for c in CONTRASTS}
        fresh = {}
        for k, rec in done.items():
            if rec["status"] in ("rejected", "unsupported"):
                fresh[k] = rec
                continue
            r = assess_parameter(rec["model"], rec["parameter"], rec["setting_a"], rec["setting_b"],
                                 rec["completions_tight"], rec["completions_open"],
                                 n_permutations=args.permutations,
                                 primary=primaries.get(rec["parameter"], "dh"))
            r.completions_tight, r.completions_open = rec["completions_tight"], rec["completions_open"]
            r.prompt, r.provider = rec["prompt"], rec.get("provider", DEFAULT_PROVIDER)
            r.empty_tight, r.empty_open = rec.get("empty_tight", 0), rec.get("empty_open", 0)
            r.collected_from = rec.get("collected_from", "")   # the data's date, not today's
            r.collected_to = rec.get("collected_to", "")
            fresh[k] = r.to_dict()
        changed = sum(1 for k in done if done[k]["status"] != fresh[k]["status"])
        tmp = ckpt.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(v, default=str) + "\n" for v in fresh.values()))
        os.replace(tmp, ckpt)
        done = fresh
        print(f"reassessed {len(done)} cells; {changed} changed status")

    def restore(rec: dict):
        r = Distinguishability(model=rec["model"], parameter=rec["parameter"],
                               setting_a=rec["setting_a"], setting_b=rec["setting_b"])
        for k, v in rec.items():
            if hasattr(r, k) and k != "excess":
                setattr(r, k, v)
        return r

    for model in models:
        for pid in prompt_ids + forced_ids:
            print(f"\n=== {model} — prompt '{pid}' ===")
            for param, sa, sb, primary in CONTRASTS:
                if pid in FORCED_PROMPTS and param not in FORCED_FOR:
                    continue
                if (args.provider, model, pid, param) in done and not (
                        args.retry_failed and done[(args.provider, model, pid, param)]["status"] == "transport"):
                    r = restore(done[(args.provider, model, pid, param)])
                    print(f"  {param:<20} (from checkpoint) status={r.status}")
                    per_prompt_results[pid].append(r)
                    results.append(r)
                    continue
                if param not in selected:
                    continue
                a, ea, ua, empty_a, ta = collect(key, model, sa, args.n, args.workers,
                                             args.max_tokens, args.effort, all_prompts[pid],
                                             provider=args.provider)
                b, eb, ub, empty_b, tb = collect(key, model, sb, args.n, args.workers,
                                             args.max_tokens, args.effort, all_prompts[pid],
                                             provider=args.provider)
                tokens += sum(ua + ub)

                r = assess_parameter(model, param, sa, sb, a, b,
                                     n_permutations=args.permutations, primary=primary)
                r.completions_tight, r.completions_open = a, b
                r.prompt = pid
                r.provider = args.provider
                w = window(ta + tb)
                r.collected_from, r.collected_to = w["from"], w["to"]
                r.empty_tight = sum(x == EMPTY_TOKEN for x in a)
                r.empty_open = sum(x == EMPTY_TOKEN for x in b)
                rejected = [e for e in ea + eb if e.startswith("rejected")]
                unsupported = [e for e in ea + eb if e.startswith("unsupported")]
                if rejected:
                    r.status, r.detail = "rejected", rejected[0]
                elif unsupported:
                    # Three things that look alike and are not: the provider
                    # refused it (rejected), the provider took it and it did
                    # nothing (no effect seen), and the API has no way to say
                    # it at all (unsupported). Only the middle one is about the
                    # sampler.
                    r.status, r.detail = "unsupported", unsupported[0]
                if (empty_a or empty_b) and r.status == "insufficient":
                    r.detail = (f"{empty_a + empty_b}/{2 * args.n} calls returned empty "
                                f"content — billed, but max_tokens ({args.max_tokens}) "
                                "cut them off before any content. Raise --max-tokens.")

                if r.status in ("rejected", "unsupported", "transport"):
                    print(f"  {param:<20} {r.status.upper():<10} {r.detail[:76]}")
                elif r.status == "insufficient":
                    print(f"  {param:<20} NO POWER   n={r.n_tight}/{r.n_open}  {r.detail[:56]}")
                else:
                    print(f"  {param:<18} H tight={r.entropy_tight:.2f} open={r.entropy_open:.2f}"
                          f"  dH={r.dh:+.2f} (null {r.dh_null_mean:+.2f}) p={r.dh_p:.4f}"
                          f"   TV={r.tv:.3f} p={r.tv_p:.4f}   primary={r.primary}")
                per_prompt_results[pid].append(r)
                results.append(r)
                # A cell that is short because calls FAILED IN TRANSPORT — a 429
                # storm, an exhausted balance, a network drop — is not a result
                # and must not be checkpointed, or a resume would keep it instead
                # of retrying. Rejected and unsupported are findings; those stay.
                transport = [e for e in ea + eb
                             if not e.startswith(("rejected", "unsupported"))]
                if transport and r.status == "insufficient":
                    # The provider accepted the request and did not finish it.
                    # Recorded as its own status with the evidence, so a resume
                    # does not spend another hour re-timing-out, and the table
                    # can show "accepted, unusable" — which for a documented
                    # parameter value is a finding. --retry-failed reruns them.
                    r.status = "transport"
                    r.detail = (f"{len(transport)}/{2 * args.n} calls did not complete: "
                                f"{Counter(e.split(':')[0] for e in transport).most_common(2)}; "
                                f"e.g. {transport[0][:80]}")
                    print(f"  {param:<20} TRANSPORT  {r.detail[:90]}")
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
    print(f"{'model':<32}{'param':<20}" + "".join(f"{pid:>11}" for pid in prompt_ids)
          + f"{'ctrl ok':>8}{'verdict':>8}{'dist':>6}  verdict")
    sym = {"distinguishable": "yes", "no effect seen": "NO", "underpowered": "?",
           "rejected": "rej", "unsupported": "n/a", "insufficient": "n/a", "transport": "err"}
    for model in models:
        for param, _, _, _ in CONTRASTS:
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
            fv = {}
            if param in FORCED_FOR:
                for pid in forced_ids:
                    r = next((x for x in per_prompt_results[pid]
                              if x.model == model and x.parameter == param), None)
                    if r is not None:
                        fv[pid] = interpret(r, power_by_prompt.get(pid, {}),
                                            adj_by_prompt[pid].get((model, param), 1.0),
                                            forced=True)
            if not pv and not fv:
                continue                      # not run in this file; no verdict to report
            ctrl_ok = {pid: bool(mp.powered) for pid, pw in power_by_prompt.items()
                       for m2, mp in pw.items() if m2 == model}
            agg = (aggregate(pv, model, param, control_passed=ctrl_ok) if pv
                   else Aggregate(model=model, parameter=param))
            if fv:
                agg.forced = fv
                # One forced prompt is its own verdict; aggregate() wants two
                # powered prompts for a majority and would call it underpowered.
                agg.verdict_forced = (next(iter(fv.values())) if len(fv) == 1
                                      else aggregate(fv, model, f"{param}[forced]").verdict)
            aggregates.append(agg)
            line = f"{model:<32}{param:<20}"
            for pid in prompt_ids:
                line += f"{sym.get(pv.get(pid, ''), '—'):>11}"
            line += f"{agg.n_control_passed:>8}{agg.n_verdict:>8}{agg.n_distinguishable:>6}  {agg.verdict}"
            if fv:
                line += f"   forced: {sym.get(agg.verdict_forced, agg.verdict_forced)}"
            print(line)

    print(f"\n{len(results)} tests over {len(prompt_ids)} prompts, {tokens:,} output tokens, "
          f"{(time.time()-t0)/60:.1f} min   [{default_cache().stats}; {pacer(args.provider).status()}]")
    print("p_holm is Holm-Bonferroni across the whole grid; the claim is per-cell, "
          "so the family-wise rate is the relevant one.")

    if args.out:
        dest = resolve_out(args.out)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(
            {"provider": args.provider, "prompt": PROMPT, "n_per_arm": args.n,
             "max_tokens": args.max_tokens, "reasoning_effort": args.effort,
             "permutations": args.permutations,
             # collected = when the provider answered (from the cells);
             # written = when this file was produced. They differ after --reassess.
             "collected": _dated(results),
             "written": iso(time.time()),
             "reassessed_at": _prior(args.out, "reassessed_at") + ([iso(time.time())] if args.reassess else []),
             "results": [r.to_dict() for r in results],
             "p_holm_by_prompt": {pid: {f"{k[0]}|{k[1]}": v for k, v in a.items()}
                                  for pid, a in adj_by_prompt.items()},
             "prompts": {pid: PROMPTS[pid] for pid in prompt_ids},
             "forced_prompts": {pid: FORCED_PROMPTS[pid] for pid in forced_ids},
             "positive_control_by_prompt": {pid: {m: mp.to_dict() for m, mp in pw.items()}
                                            for pid, pw in power_by_prompt.items()},
             "aggregates": [a.to_dict() for a in aggregates]},
            indent=2, default=str) + "\n")
        print(f"wrote {show(dest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

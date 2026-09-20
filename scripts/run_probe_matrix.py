#!/usr/bin/env python3
"""Run the probe matrix breadth-first under a dollar cap.

The priority when the balance runs short, in order: breadth of models, breadth
of providers, N per cell. A thin result across forty models beats a thick one
across eight. So the matrix is run in PASSES, each pass covering every
(provider, model) before the next goes deeper:

    1. determinism      10 identical T=0 calls x 3 seed conditions x 5 prompts
    2. distinguish thin  every contrast, both controls, --n THIN per arm
    3. negative control  one pair of identical arms at --n FULL
    4. distinguish full  --n FULL per arm

The negative control is not thinned: at temperature 1.0 ten samples are nearly
always all-unique, the pair is degenerate, and the probe reports it as
uninformative. Eighty calls a model is the price of a control that controls.

Models come from each keyed provider's live catalogue, not a list in this repo,
because the catalogue is what moves. Chat models only; embedding, reranker,
audio and image models are filtered by name where the listing does not say.
Cheapest first within a pass, so a cap admits the most models. A model with no
published price is bounded at its provider's highest listed price; a provider
with no price table at all is run with spend counted as zero and said so.

Spend is measured, not estimated: the sum over cache entries stored since this
run began (scripts/probe_spend.py's arithmetic). Each job is admitted only if
measured spend plus that job's estimate fits under --budget-usd. The estimate
uses the model's own mean output length where the cache has one and a
conservative default where it does not.

Every job is a subprocess of the existing probe scripts, so its checkpoints,
cache hits and output files are exactly what a hand-run would produce, and a
job that was killed resumes from its checkpoint for free.

    python3 scripts/run_probe_matrix.py --dry-run
    python3 scripts/run_probe_matrix.py --budget-usd 5
    python3 scripts/run_probe_matrix.py --providers groq cerebras --budget-usd 0
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import functools
print = functools.partial(print, flush=True)   # noqa: A001 — progress lands in logs under nohup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samplerconfound.adapters import ADAPTERS
from samplerconfound.cache import DEFAULT_DIR
from samplerconfound.paths import show
from samplerconfound.pricing import PRICES
from samplerconfound.provider import Rejected, Transient, call, list_models, load_key
from scripts.probe_spend import usage_of

MATRIX = ROOT / "runs" / "matrix"
PY = sys.executable
NOT_CHAT = re.compile(r"embed|rerank|whisper|tts|speech|guard|moderation|ocr|audio|image|"
                      r"flux|diffusion|clip|encoder|transcri", re.I)

# calls per job, from the probe scripts' own arithmetic
N_PROMPTS, N_CONTRASTS, N_DET_COND, N_DET, N_DET_PROMPTS = 4, 4, 3, 10, 5
DEFAULT_OUT_TOKENS = 300          # per call, for a model the cache has never seen
IN_TOKENS = 45


def calls_for(pass_name: str, n: int, k: int) -> int:
    if pass_name == "determinism":
        return N_DET_COND * N_DET * N_DET_PROMPTS
    if pass_name == "negative":
        return 2 * n * k
    return N_PROMPTS * N_CONTRASTS * 2 * n


# --------------------------------------------------------------------------
# spend, measured
# --------------------------------------------------------------------------

def mean_out_tokens() -> dict[tuple[str, str], float]:
    """Mean output tokens per call by (provider, model), from every cached response."""
    calls, out_tok = defaultdict(int), defaultdict(int)
    for f in DEFAULT_DIR.rglob("*.json"):
        try:
            e = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        b = e["body"]
        key = (b.get("_provider", "fireworks"), b["model"].split("/")[-1])
        calls[key] += 1
        out_tok[key] += usage_of(e)[1]
    return {k: out_tok[k] / calls[k] for k in calls}


def spent_since(since: float, price_of) -> float:
    total = 0.0
    for f in DEFAULT_DIR.rglob("*.json"):
        try:
            e = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if e.get("stored_at", 0) < since:
            continue
        b = e["body"]
        p_in, p_out = price_of(b.get("_provider", "fireworks"), b["model"].split("/")[-1])
        i, o = usage_of(e)
        total += (i * p_in + o * p_out) / 1e6
    return total


# --------------------------------------------------------------------------
# the plan
# --------------------------------------------------------------------------

def discover(providers: list[str], only: list[str] | None) -> dict[str, list[str]]:
    found = {}
    for prov in providers:
        key = load_key(prov, required=False)
        if not key:
            print(f"{prov:<11} no key — skipped (get one at {ADAPTERS[prov].signup})")
            continue
        try:
            cat = list_models(key, prov)
        except Transient as e:
            print(f"{prov:<11} catalogue unreachable: {e}")
            continue
        models, dead = [], []
        for m in cat:
            mid = m["id"]
            if "/" in mid or not mid:
                continue                                  # routers, other accounts
            # The name filter applies whatever the listing claims: Fireworks
            # flags its embedding and reranker models supports_chat=true.
            if m["chat"] is False or NOT_CHAT.search(mid):
                continue
            if only and mid not in only:
                continue
            if not alive(key, prov, mid):
                dead.append(mid)
                continue
            models.append(mid)
        found[prov] = sorted(set(models))
        print(f"{prov:<11} {len(found[prov])} chat models serving"
              + (f"; listed but not serving: {', '.join(dead)}" if dead else ""))
    return found


def alive(key: str, prov: str, model: str) -> bool:
    """One uncached one-token call. Three listed models 404 at inference today;
    a probe on one of those is 1,300 rejected calls and a row of noise."""
    body = {"model": model, "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1, "temperature": 0.0}
    try:
        call(key, body, provider=prov, timeout=30, retries=1)   # ask once; this is not a measurement
        return True
    except Rejected:
        return False
    except Transient:
        return True                                       # do not drop a model for a blip


def make_price_of(found: dict[str, list[str]]):
    """Price lookup with the bounding rule for unpriced models."""
    by_prov_max = {}
    for prov, models in found.items():
        priced = [PRICES[m] for m in models if m in PRICES]
        by_prov_max[prov] = max(priced, key=lambda p: p[1]) if priced else None

    def price_of(prov: str, model: str) -> tuple[float, float]:
        if model in PRICES:
            return PRICES[model]
        return by_prov_max.get(prov) or (0.0, 0.0)
    return price_of, by_prov_max


def out_path(prov: str, model: str, pass_name: str, n: int) -> Path:
    d = MATRIX / prov
    if pass_name == "determinism":
        return d / f"{model}.det.json"
    if pass_name == "negative":
        return d / f"{model}.neg-n{n}.json"
    return d / f"{model}.dist-n{n}.json"


def already_done(prov: str, model: str, pass_name: str, n: int) -> bool:
    if out_path(prov, model, pass_name, n).exists():
        return True
    if pass_name == "distinguish":
        # a thicker run of the same model satisfies a thinner pass; the
        # paid-tier run of 2026-09-20 predates the naming scheme (<model>.json, n=40)
        if n <= 40 and (MATRIX / prov / f"{model}.json").exists():
            return True
        for f in (MATRIX / prov).glob(f"{model}.dist-n*.json"):
            if int(f.name.rsplit("-n", 1)[1][:-5]) >= n:
                return True
    if pass_name == "determinism":
        legacy = MATRIX / prov / "determinism.json"
        if legacy.exists():
            try:
                if model in json.loads(legacy.read_text()).get("summary", {}):
                    return True
            except (json.JSONDecodeError, OSError):
                pass
        if prov == "fireworks":
            try:
                if model in json.loads((ROOT / "runs" / "determinism.json").read_text())["summary"]:
                    return True
            except (OSError, json.JSONDecodeError, KeyError):
                pass
    if pass_name == "distinguish" and prov == "fireworks" and n <= 40:
        try:
            d = json.loads((ROOT / "runs" / "distinguish_multiprompt.json").read_text())
            if any(a["model"] == model for a in d["aggregates"]):
                return True
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    return False


def command(prov: str, model: str, pass_name: str, n: int, k: int, workers: int) -> list[str]:
    out = out_path(prov, model, pass_name, n)
    if pass_name == "determinism":
        return [PY, "scripts/probe_determinism.py", "--provider", prov, "--models", model,
                "--workers", str(workers), "--out", str(out)]
    cmd = [PY, "scripts/probe_distinguishability.py", "--provider", prov, "--models", model,
           "--n", str(n), "--workers", str(workers), "--out", str(out)]
    if pass_name == "negative":
        cmd += ["--negative", str(k)]
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", nargs="+", default=sorted(ADAPTERS))
    ap.add_argument("--models", nargs="+", help="restrict to these model ids (any provider)")
    ap.add_argument("--budget-usd", type=float, default=5.0,
                    help="stop admitting jobs once measured spend + next estimate exceeds this")
    ap.add_argument("--thin-n", type=int, default=10)
    ap.add_argument("--full-n", type=int, default=40)
    ap.add_argument("--negative-k", type=int, default=1)
    ap.add_argument("--passes", nargs="+", default=["determinism", "distinguish-thin", "negative", "distinguish-full"])
    ap.add_argument("--workers", type=int, default=4, help="threads per probe subprocess")
    ap.add_argument("--jobs", type=int, default=4, help="probe subprocesses at once")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    found = discover(args.providers, args.models)
    if not found:
        print("nothing to run: no provider with a key")
        return 1
    price_of, prov_max = make_price_of(found)
    for prov, mx in prov_max.items():
        if mx is None:
            print(f"{prov:<11} NO PRICE TABLE — spend on this provider is counted as $0")
        else:
            unpriced = [m for m in found[prov] if m not in PRICES]
            if unpriced:
                print(f"{prov:<11} {len(unpriced)} unpriced model(s) bounded at ${mx[1]:.2f}/1M out: "
                      f"{', '.join(unpriced)}")
    mean_out = mean_out_tokens()

    passes = []
    for p in args.passes:
        name, n = {"determinism": ("determinism", N_DET),
                   "distinguish-thin": ("distinguish", args.thin_n),
                   "negative": ("negative", args.full_n),
                   "distinguish-full": ("distinguish", args.full_n)}[p]
        passes.append((p, name, n))

    def estimate(prov, m, name, n, mean_out):
        calls = calls_for(name, n, args.negative_k)
        p_in, p_out = price_of(prov, m)
        o = mean_out.get((prov, m), DEFAULT_OUT_TOKENS)
        return calls * (IN_TOKENS * p_in + o * p_out) / 1e6

    plan = []
    for label, name, n in passes:
        jobs = []
        for prov, models in found.items():
            for m in models:
                if already_done(prov, m, name, n):
                    continue
                jobs.append((estimate(prov, m, name, n, mean_out), prov, m, label, name, n))
        jobs.sort()
        plan.extend(jobs)

    done_now = sum(1 for prov, ms in found.items() for m in ms
                   for _, name, n in passes if already_done(prov, m, name, n))
    print(f"\n{len(plan)} jobs to run ({done_now} pass-cells already on disk), "
          f"estimated ${sum(j[0] for j in plan):.2f} total, cap ${args.budget_usd:.2f}")
    print(f"{'pass':<18}{'provider':<11}{'model':<32}{'est $':>7}")
    for est, prov, m, label, _, _ in plan:
        print(f"{label:<18}{prov:<11}{m:<32}{est:>7.3f}")
    if args.dry_run:
        return 0

    # ---- run, pass by pass, admitting jobs against measured spend --------
    skipped, ran, failed, uninformative = [], [], [], []
    for label, name, n in passes:
        jobs = [j for j in plan if j[3] == label]
        if not jobs:
            continue
        print(f"\n== pass {label}: {len(jobs)} jobs")
        # Re-estimate with what the cache knows NOW: after the thin pass, a
        # model's real output length replaces the conservative default.
        mean_out = mean_out_tokens()
        jobs = sorted((estimate(prov, m, pname, nn, mean_out), prov, m, label, pname, nn)
                      for _, prov, m, label, pname, nn in jobs)
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futs = {}
            for est, prov, m, _, pname, nn in jobs:
                spent = spent_since(t0, price_of)
                if spent + est > args.budget_usd:
                    skipped.append((label, prov, m, est))
                    print(f"  skip {prov}/{m}: ${spent:.2f} spent + ${est:.2f} would exceed cap")
                    continue
                cmd = command(prov, m, pname, nn, args.negative_k, args.workers)
                out_path(prov, m, pname, nn).parent.mkdir(parents=True, exist_ok=True)
                futs[pool.submit(subprocess.run, cmd, cwd=ROOT, capture_output=True, text=True)] = (prov, m)
            for fut in as_completed(futs):
                prov, m = futs[fut]
                r = fut.result()
                if r.returncode == 0:
                    ran.append((label, prov, m))
                    tail = [l for l in r.stdout.splitlines() if l.strip()][-1:]
                    print(f"  done {prov}/{m}  {tail[0][:80] if tail else ''}")
                elif "no informative negative-control pairs" in r.stdout:
                    uninformative.append((label, prov, m))
                    print(f"  n/a  {prov}/{m}: negative control degenerate (both arms all-unique)")
                else:
                    failed.append((label, prov, m))
                    print(f"  FAIL {prov}/{m}: {(r.stderr or r.stdout).strip().splitlines()[-1][:120]}")
    spent = spent_since(t0, price_of)
    print(f"\nran {len(ran)}, failed {len(failed)}, uninformative {len(uninformative)}, "
          f"skipped for budget {len(skipped)}; "
          f"measured spend this run ${spent:.2f} of cap ${args.budget_usd:.2f}, "
          f"{(time.time() - t0) / 60:.1f} min")
    if skipped:
        print("skipped (raise --budget-usd to run):")
        for label, prov, m, est in skipped:
            print(f"  {label:<18}{prov:<11}{m:<32}${est:.2f}")
    print(f"coverage table: {PY} scripts/probe_matrix.py")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

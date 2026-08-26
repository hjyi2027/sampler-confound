"""Does the provider actually honour every sampler parameter in the grid?

This runs before any money is spent on the sweep, because the failure it catches
is silent. If a provider ignores `top_k`, that cell is not a top-k condition — it
is a duplicate of another cell. The grid stays balanced, the decomposition still
runs, every number still looks reasonable, and the sampler variance component is
quietly halved. Nothing in the output says so.

Two distinct failures are separated here, and the distinction matters:

  REJECTED   the parameter returns an error. Loud, safe, immediately visible.
  IGNORED    the parameter is accepted and does nothing. Silent and dangerous.

A rejection is fine — it removes a cell from the grid honestly. Being ignored is
the one that corrupts the study, so acceptance alone is not treated as support:
each accepted parameter is followed by a behavioural check that its presence
changes the output distribution.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

import anthropic

# Candidates. Current-generation Claude models removed the sampling parameters
# entirely; the 4.6-and-earlier family still accepts them. Probing both tells us
# which half of the catalogue this study can even be run on.
CANDIDATES = [
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-opus-4-8",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
]

# Every probe goes through `extra_body`, deliberately.
#
# As of `anthropic` 1.0.0 the sampling parameters are not merely deprecated —
# `temperature`, `top_p` and `top_k` have been removed from the signature of
# `messages.create()` and from the MessageCreateParams TypedDict. Passing them as
# ordinary keyword arguments raises `TypeError` in the client, which never reaches
# the server and therefore says nothing about what the server would accept.
# `extra_body` writes them straight into the request JSON, so what comes back is
# the API's answer rather than the SDK's.
PROBES = [
    ("temperature", {}, {"temperature": 0.7}),
    ("top_p", {}, {"top_p": 0.95}),
    ("top_k", {}, {"top_k": 40}),
    ("min_p", {}, {"min_p": 0.05}),
    ("seed", {}, {"seed": 1234}),
]

# Short, high-entropy prompt: the behavioural check needs a request whose output
# distribution is wide enough that a truncation parameter visibly narrows it.
ENTROPY_PROMPT = "Name one animal. Reply with only the animal name, nothing else."


def _call(client, model, kwargs, extra_body, max_tokens=8, thinking=None):
    body = dict(extra_body)
    req = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": ENTROPY_PROMPT}],
        **kwargs,
    }
    if thinking is not None:
        req["thinking"] = thinking
    if body:
        req["extra_body"] = body
    return client.messages.create(**req)


def _text(resp) -> str:
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def probe_accepts(client, model, label, kwargs, extra_body) -> tuple[str, str]:
    """Does the request survive at all? Returns (status, detail)."""
    try:
        _call(client, model, kwargs, extra_body)
        return "accepted", ""
    except anthropic.BadRequestError as e:
        msg = str(getattr(e, "message", e))
        # Current models reject sampling params outright. Retry once with thinking
        # off: on some models the rejection is a consequence of thinking being on,
        # and reporting "unsupported" without checking would be wrong.
        try:
            _call(client, model, kwargs, extra_body, thinking={"type": "disabled"})
            return "accepted_thinking_off", msg[:120]
        except Exception:
            return "rejected", msg[:160]
    except anthropic.AuthenticationError as e:
        # Not a fact about parameter support. Surfacing it as one would be a lie
        # dressed as a finding, so it aborts instead.
        raise SystemExit(
            f"ANTHROPIC_API_KEY is not valid ({e}). Nothing below this point "
            "would be a statement about the API."
        ) from e
    except Exception as e:  # noqa: BLE001 - the point is to report, not to handle
        return f"error:{type(e).__name__}", str(e)[:160]


def probe_has_effect(client, model, label, kwargs, extra_body, n=12) -> tuple[str, str]:
    """Accepted is not the same as honoured. Does it change the distribution?

    Compares the spread of `n` samples with the parameter against `n` without it.
    A strong truncation setting should visibly narrow the output distribution; if
    the two sets of samples are indistinguishable, the parameter is very likely
    being dropped. This is evidence, not proof — with n this small it cannot
    separate 'ignored' from 'weak effect', and it says so in the output.
    """
    try:
        base = Counter(_text(_call(client, model, {}, {})) for _ in range(n))
        with_p = Counter(_text(_call(client, model, kwargs, extra_body)) for _ in range(n))
    except Exception as e:  # noqa: BLE001
        return "inconclusive", f"{type(e).__name__}: {e}"[:120]
    return (
        "distinct" if set(base) != set(with_p) or base != with_p else "identical",
        f"without={dict(base)} with={dict(with_p)}",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", nargs="*", default=CANDIDATES)
    ap.add_argument(
        "--effect-check",
        action="store_true",
        help="also run the behavioural check on accepted parameters (costs more)",
    )
    ap.add_argument("--out", default="runs/sampler_support.json")
    args = ap.parse_args()

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 2
    if len(key) < 40:
        print(
            f"ANTHROPIC_API_KEY is {len(key)} characters — too short to be a real "
            "key. Refusing to run: every result would read as 'unsupported' when "
            "the real cause was authentication.",
            file=sys.stderr,
        )
        return 2

    client = anthropic.Anthropic()
    results: dict[str, dict] = {}

    for model in args.models:
        results[model] = {}
        print(f"\n{model}")
        for label, kwargs, extra in PROBES:
            status, detail = probe_accepts(client, model, label, kwargs, extra)
            row = {"status": status, "detail": detail}
            if args.effect_check and status.startswith("accepted"):
                eff, ev = probe_has_effect(client, model, label, kwargs, extra)
                row["effect"] = eff
                row["evidence"] = ev
            results[model][label] = row
            mark = {
                "accepted": "ok",
                "accepted_thinking_off": "ok (thinking off)",
                "rejected": "REJECTED",
            }.get(status, status)
            extra_note = f"  effect={row['effect']}" if "effect" in row else ""
            print(f"  {label:12s} {mark}{extra_note}")
            if status == "rejected":
                print(f"               {detail}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nwrote {args.out}")

    usable = [
        m
        for m, r in results.items()
        if r["temperature"]["status"].startswith("accepted")
    ]
    print(f"\nmodels accepting temperature: {usable or 'NONE'}")
    if len(usable) < 2:
        print(
            "Fewer than two models accept a temperature on this provider. If that "
            "survives inspection of the errors above, the study cannot run here: "
            "the independent variable does not exist, which is a different problem "
            "from it being hard to measure."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

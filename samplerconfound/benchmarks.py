"""Problem sets, and the draws taken from them.

Two benchmarks, decomposed separately (see `config.BENCHMARKS`):

  math500   500 problems, of which the sweep uses a stratified 200
  aime      all 60 problems of AIME 2024 + 2025

On contamination. It does not threaten this design, and the reason is worth
stating precisely rather than waving at: every quantity the paper reports is a
*within-benchmark* variance component. Contamination inflates the level of
accuracy, and levels cancel out of a decomposition — the model main effect is
computed from deviations around the grand mean, so a benchmark that every model
has partly memorised shifts the mean and leaves the components where they were.
Both `math500` and `aime` are old enough to be in most pretraining corpora, and
for this paper that is fine.

The one case that does bite is *differential* contamination: if one model has
memorised the set and the others have not, that model sits at ceiling, its cells
lose the variance that decoding would otherwise produce, and the sampler
component falls for a reason unrelated to samplers. The model selection band in
`config.PILOT_BAND` is what catches this — a memorising model prices itself out
of the grid by scoring above 0.90 in the pilot. AIME 2025 is included partly for
this reason: it postdates the training cutoff of several candidates, so a model
that scores far better on the 2024 half than the 2025 half is visible.

Draws are deterministic without depending on `random`. Ordering is by
sha256(seed:problem_id), so the same seed gives the same problems on any
interpreter, any platform, any Python version — which `random.Random.sample` does
not actually guarantee across releases. The sweep has to be reproducible from the
config alone, and a draw that silently shifts under a Python upgrade would move
the problem component without moving anything visible.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .config import BENCHMARKS, PILOT_N_PROBLEMS, PILOT_PROBLEM_SEED

DATA = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class Problem:
    id: str
    problem: str
    answer: str
    level: int | None = None
    subject: str | None = None


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------
def load(benchmark: str, *, verify: bool = True) -> list[Problem]:
    """Load a pinned benchmark from disk, checking it against the manifest.

    The hash check is not paranoia. The problem set is a factor in the three-way
    decomposition, so a dataset revised upstream between the pilot and the sweep
    would change the problem component and the residual while every line of code
    stayed identical.
    """
    if benchmark not in BENCHMARKS:
        raise ValueError(f"unknown benchmark {benchmark!r}; known: {sorted(BENCHMARKS)}")

    manifest_path = DATA / "MANIFEST.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"{manifest_path} not found — run scripts/fetch_benchmarks.py first"
        )
    manifest = json.loads(manifest_path.read_text())
    entry = manifest["files"][benchmark]
    path = DATA / entry["path"]
    text = path.read_text(encoding="utf-8")

    if verify:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != entry["sha256"]:
            raise ValueError(
                f"{path.name} does not match the manifest ({digest[:16]} != "
                f"{entry['sha256'][:16]}). The pinned problem set changed. Do not "
                "re-hash to make this pass — any results already generated were "
                "scored against different problems."
            )

    problems = [Problem(**json.loads(line)) for line in text.splitlines() if line]
    if len(problems) != entry["n_problems"]:
        raise ValueError(
            f"{path.name} has {len(problems)} problems, manifest says "
            f"{entry['n_problems']}"
        )
    return problems


# --------------------------------------------------------------------------
# deterministic draws
# --------------------------------------------------------------------------
def _rank(seed: int, problem_id: str) -> str:
    return hashlib.sha256(f"{seed}:{problem_id}".encode("utf-8")).hexdigest()


def select(
    problems: list[Problem],
    n: int,
    seed: int,
    *,
    stratify: bool = True,
) -> list[Problem]:
    """Draw n problems deterministically, keeping the difficulty mix.

    Stratifies proportionally on `level` when every problem has one. A uniform
    draw of 200 from 500 leaves the level mix to chance, and difficulty is what
    decides whether a cell sits near the ceiling or the floor — both of which
    flatten the very variance the paper is measuring. It also keeps the 200-problem
    accuracy comparable to a published MATH-500 number, which the paper needs,
    since the whole argument is about cross-paper comparison.

    AIME carries no level label, so it falls back to a single stratum.

    Returned in a stable order (by id) so the sweep's work list is reproducible.
    """
    if n > len(problems):
        raise ValueError(f"asked for {n} problems, only {len(problems)} available")
    if n < 0:
        raise ValueError("n must be non-negative")
    if n == len(problems):
        return sorted(problems, key=lambda p: p.id)

    levels = {p.level for p in problems}
    if not stratify or None in levels:
        chosen = sorted(problems, key=lambda p: _rank(seed, p.id))[:n]
        return sorted(chosen, key=lambda p: p.id)

    strata: dict[int, list[Problem]] = {}
    for p in problems:
        strata.setdefault(p.level, []).append(p)

    # Largest-remainder allocation, so the per-level counts sum to exactly n and
    # the rounding does not systematically favour the same levels every time.
    total = len(problems)
    exact = {lv: n * len(ps) / total for lv, ps in strata.items()}
    quota = {lv: int(v) for lv, v in exact.items()}
    short = n - sum(quota.values())
    for lv in sorted(exact, key=lambda lv: (-(exact[lv] - quota[lv]), lv))[:short]:
        quota[lv] += 1

    chosen: list[Problem] = []
    for lv in sorted(strata):
        ranked = sorted(strata[lv], key=lambda p: _rank(seed, p.id))
        chosen.extend(ranked[: quota[lv]])
    return sorted(chosen, key=lambda p: p.id)


# --------------------------------------------------------------------------
# the study's splits
# --------------------------------------------------------------------------
def sweep_split(benchmark: str, *, verify: bool = True) -> list[Problem]:
    """The problems the main sweep runs on, exactly as frozen in configs/."""
    spec = BENCHMARKS[benchmark]
    return select(load(benchmark, verify=verify), spec["n_problems"], spec["problem_seed"])


def pilot_split(benchmark: str = "math500", *, verify: bool = True) -> list[Problem]:
    """The problems the model-selection pilot runs on — disjoint from the sweep.

    Drawn from what the sweep left behind, not from the full set. Choosing the
    model levels on the same items they are then scored on pushes selection noise
    into the model component, which is the denominator of the paper's headline
    ratio: the sampler share would be understated by an amount nobody could
    estimate after the fact.
    """
    everything = load(benchmark, verify=verify)
    used = {p.id for p in sweep_split(benchmark, verify=False)}
    remaining = [p for p in everything if p.id not in used]
    if len(remaining) < PILOT_N_PROBLEMS:
        raise ValueError(
            f"{benchmark} leaves only {len(remaining)} problems after the sweep "
            f"draw, need {PILOT_N_PROBLEMS} for the pilot"
        )
    return select(remaining, PILOT_N_PROBLEMS, PILOT_PROBLEM_SEED)

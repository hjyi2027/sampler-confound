"""The grid. What is swept, what is held fixed, and what must be verified first.

Balance is not a style preference here: `variance.py` assumes every
(model, sampler) cell holds the same number of replicates, which is what makes
the sums of squares orthogonal and the components exactly estimable. A config
that produces an unbalanced grid is a config that produces wrong numbers, so the
loader checks it rather than trusting it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# sampler configurations
# --------------------------------------------------------------------------
# Six configs chosen to span the space practitioners actually use, not to be
# exhaustive. `standard` is the de facto default that goes unreported in most
# papers; `greedy` is what evaluation harnesses usually claim to use. The gap
# between those two alone is the paper's motivating case.
#
# WARNING, and it must be checked before the sweep: not every provider honours
# `top_k` and `min_p` on an OpenAI-compatible endpoint, and none of them document
# dropping it. If they are silently ignored, `topk` and `minp` collapse into
# duplicates of `standard`/`hightemp` — the grid stays balanced, the decomposition
# still runs, and the sampler component is quietly halved. That failure is
# invisible in the output. `scripts/probe_sampler_support.py` exists to catch it.

SAMPLER_CONFIGS = [
    {"id": "greedy", "temperature": 0.0},
    {"id": "lowtemp", "temperature": 0.3, "top_p": 1.0},
    {"id": "standard", "temperature": 0.7, "top_p": 0.95},
    {"id": "hightemp", "temperature": 1.0, "top_p": 1.0},
    {"id": "topk", "temperature": 0.7, "top_k": 40},
    {"id": "minp", "temperature": 1.0, "min_p": 0.05},
]

# Models: unresolved. Needs 3-4 open-weight instruct models that score well clear
# of the floor on MATH-500, served by one provider so the comparison is not
# confounded by serving stack. Left empty on purpose — a plausible-looking default
# here would get run by accident and silently define the study.
DEFAULT_MODELS: list[str] = []

# Held fixed across every cell. Each of these is a variable this paper is
# accusing the field of leaving free, so leaving one free here would be fatal.
FIXED = {
    "prompt_template": (
        "Solve the problem. Reason step by step, then give the final answer on "
        "its own last line in the form: Answer: <answer>"
    ),
    "max_tokens": 1024,
    "stop": None,
    "system_prompt": None,
}


@dataclass
class Design:
    models: list[str] = field(default_factory=lambda: list(DEFAULT_MODELS))
    samplers: list[dict] = field(default_factory=lambda: [dict(s) for s in SAMPLER_CONFIGS])
    n_replicates: int = 5
    benchmark: str = "math500"
    n_problems: int = 200
    problem_seed: int = 0          # selects WHICH problems; unrelated to decoding
    provider: str = "fireworks"
    fixed: dict = field(default_factory=lambda: dict(FIXED))

    @property
    def n_generations(self) -> int:
        return len(self.models) * len(self.samplers) * self.n_replicates * self.n_problems

    def validate(self) -> None:
        if len(self.models) < 2:
            raise ValueError(
                "need >= 2 models; the headline compares sampler variance against "
                "model variance and there is no model variance with one model"
            )
        if len(self.samplers) < 2:
            raise ValueError("need >= 2 sampler configs")
        if self.n_replicates < 2:
            raise ValueError(
                "need >= 2 replicates per cell; with one, resampling variance is "
                "not separable from the cell mean"
            )
        ids = [s["id"] for s in self.samplers]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate sampler ids: {ids}")
        if len(set(self.models)) != len(self.models):
            raise ValueError(f"duplicate models: {self.models}")
        for s in self.samplers:
            if "temperature" not in s:
                raise ValueError(f"sampler {s.get('id')!r} has no temperature")
            if s["temperature"] == 0 and ("top_p" in s or "top_k" in s or "min_p" in s):
                raise ValueError(
                    f"sampler {s['id']!r} sets a truncation parameter alongside "
                    "temperature 0, which is meaningless and reads as a mistake"
                )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def load(cls, path: str | Path) -> "Design":
        d = cls(**json.loads(Path(path).read_text()))
        d.validate()
        return d

    def save(self, path: str | Path) -> None:
        self.validate()
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n")

# Session handoff — 2026-08-26

Paste this into a new session to resume. Living status is in `TASKS.md`;
this file is the narrative a fresh session needs and does not duplicate it.

## Project

**sampler-confound** — "The Sampler Is a Confound: Decoding Configuration Rivals
Model Choice in LLM Mathematical Reasoning." MATH-AI 2026 (NeurIPS workshop),
4 pages, deadline **Sept 6**, target submit **Sept 5**.

- Local: `~/AIscend/sampler-confound`
- Remote: `https://github.com/hjyi2027/sampler-confound` (**private, personal
  account `hjyi2027`** — deliberately not under AIscend)
- 4 commits on `main`, all pushed. `.venv/` has anthropic, numpy, pytest.
- **85 tests passing**: `.venv/bin/python -m pytest tests/ -q`

Extends *Unauthored by Design* (`~/AIscend/seed-study-creative`) by swapping the
dependent variable from style features to correctness. The point of picking it is
that the machinery already exists — it is a seven-day project, not a six-week one,
and that margin is why it fits before the Nov 1 ED deadline.

## Deadline context

| Deadline | Date | Project |
|---|---|---|
| ai4math | **Aug 29** | LiteFNO Phase 4-5 — comes first, not this repo |
| MATH-AI | **Sept 6** | this repo; formally starts Aug 30 |
| IAAI-27 | **Sept 8** | repro-report, experience track, no new experiments |
| ED | Nov 1 | hard stop; Sept 8 onward is essays only |

## What is built

| File | What it does |
|---|---|
| `samplerconfound/variance.py` | Three decompositions, one per dependent variable |
| `samplerconfound/inversion.py` | Comparison-inversion rate, raw vs decisive |
| `samplerconfound/grade.py` | Answer extraction, normalisation, 3-valued grading |
| `samplerconfound/config.py` | `Design` grid, 6 sampler configs, balance validation |
| `scripts/probe_sampler_support.py` | Does the provider honour each sampler param |
| `scripts/verify_grader.py` | Stratified 50-sample hand-verification worksheet |

**variance.py** — `decompose_accuracy` (two-way model x sampler on benchmark
accuracy; the headline, in the units papers publish in), `decompose_items`
(three-way with problem crossed, binary correctness), `solve_rates` +
`decompose_solve_rate` (three-way on continuous per-problem solve rate).

Changes from the original port, both deliberate: standardisation was dropped so
components stay in accuracy points (`sqrt` of a component reads as "the sampler
moves the reported number by +/- N points"), and the three-way decompositions are
new EMS algebra written here, not ported.

**grade.py** — verdicts are `correct` / `incorrect` / `unparseable`, and
`Verdict.correct` returns `None` for the third, not `False`.

## Decisions, and why

- **No GPU.** MacBook Air M4, 16 GB, ~18 GB free disk. Local inference caps near
  1-1.5B, too weak for correctness variance to mean anything. Sweep is API-only.
- **Anthropic API is ruled out — the independent variable does not exist there.**
  In `anthropic` 1.0.0, `temperature` / `top_p` / `top_k` are absent from the
  signature of `messages.create()` and from `MessageCreateParams`; `min_p` and
  `seed` never existed. Sampling control was replaced by `output_config.effort`.
  Verify with `inspect.signature(anthropic.resources.messages.Messages.create)`.
  Keep the observation for the Discussion — the largest commercial provider
  removed decoding parameters entirely — but it does not rescue the experiment.
- **Provider decided: Fireworks or Together** (open-weight, OpenAI-compatible).
  **No key for either is on this machine.** The `ANTHROPIC_API_KEY` in
  `~/.zprofile` is 10 characters and returns `401 API key is invalid`.
- **The replicate dimension is not "seed."** Fireworks silently ignores `seed` on
  text. The design never needed it: seed was always the replicate stratum, so
  within-cell variance is resampling variance regardless. Call it **sampling
  variance at fixed configuration**. Expect greedy cells to show near-zero
  within-cell variance; if they do not, the provider is non-deterministic at T=0,
  which is a reportable finding about every greedy-decoding paper.
- **Verdicts are three-valued.** Scoring an unparseable response as incorrect
  merges "got the maths wrong" with "formatted the answer oddly" — and those come
  apart along the independent variable, because high temperature produces messier
  output. A binary grader routes formatting variance into the sampler component
  and reports it as the finding. `summarise()` gives `accuracy_strict`,
  `accuracy_parsed`, and the unparseable rate per cell.
- **Integer answers require exact numeric equality.** A relative tolerance was
  matching 1000000 against 1000001 — the grader got more forgiving exactly where
  the arithmetic got harder. Non-integers still get 1e-6 relative, so 0.333333
  counts for 1/3. Caught by a test.
- **Solve rate gets a binomial-noise correction.** A rate from R replicates
  carries p(1-p)/R of noise that lands entirely in the residual; uncorrected,
  every variance share depends on the replicate budget rather than the data.

## Traps that would quietly kill the headline

1. **Model levels must be near-peer.** A wide capability ladder makes model
   variance enormous and collapses the sampler/model ratio toward zero. The
   headline would die on a grid choice, not a fact. Near-peer is also the
   comparison papers actually argue about.
2. **MATH-500 will likely ceiling.** Strong models sit near the top; a ceiling
   floors variance exactly like a floor does. AIME probably carries the headline,
   MATH-500 becomes the control.
3. **`top_k` / `min_p` may be silently ignored.** Then those cells duplicate other
   configs, the grid stays balanced, the numbers look fine, and the sampler
   component is quietly halved. `scripts/probe_sampler_support.py` separates
   REJECTED (loud, safe) from IGNORED (silent, corrupting) — needs porting to the
   OpenAI-compatible surface once the provider is fixed.
4. **Few factor levels make single-draw recovery tests meaningless.** Realised
   level variance scatters by ~sqrt(2/(k-1)) — 53% at k=8. This bit twice while
   writing the tests. With 3-4 models the model component is estimated from very
   few levels and its interval is wide; that belongs in Limitations, stated
   plainly, not hidden inside a reassuring bootstrap.

## Next steps

1. **Benchmark loading** — MATH-500 + AIME subset, problem selection pinned by an
   explicit seed. Provider-independent, can start immediately.
2. **Generation harness** — cached and resumable, OpenAI-compatible. Needs the key.
3. **Freeze the grid** in `configs/main.json`; `DEFAULT_MODELS` is deliberately
   empty so a plausible default cannot get run by accident and silently define
   the study.
4. **Smoke-run end to end at 1/20 scale** before committing budget.
5. **Run the 50-sample hand-verification** once there is sweep output.

Budget: 3-4 models x 6 samplers x 5 reps x 200 problems = ~18,000 generations,
~10.8M output tokens. Low single-digit USD at 7-8B class, tens at 70B+.

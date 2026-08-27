# Tasks

Deadline **Sept 6**, submit **Sept 5**. Today is Aug 26; ai4math (Aug 29) comes
first and this project formally starts Aug 30.

## Open questions that block Phase 1

- [x] **Provider key.** Fireworks, key in `.env` (gitignored). $25 of credit.
      Anthropic remains ruled out — its API no longer exposes any decoding
      parameters (see README).
- [x] **Model shortlist.** Resolved as a *rule*, not a list. Seven non-reasoning
      instruct candidates across five vendors in `MODEL_CANDIDATES`; the four
      levels are chosen by `select_models()` from measured pilot accuracy, not
      from published numbers. Model IDs still need checking against the live
      Fireworks catalog — a candidate that 404s drops out of the design silently
      unless noticed, so `select_models.py` warns on any missing pilot result.
- [x] **Near-peer, not a capability ladder.** Enforced by the selection rule:
      keep candidates inside a pre-registered accuracy band, then take the
      4-subset with the smallest spread. Band excludes ceiling and floor
      separately. Original note below.
- [ ] **Near-peer, not a capability ladder.** If the model factor spans a wide
      capability range, model variance is enormous and the sampler/model ratio
      collapses toward zero — the headline dies on a design choice rather than a
      fact. The comparison papers actually argue about is between near-peer
      models, and the grid should reflect that. Report the ladder version as a
      secondary analysis if it is cheap.
- [ ] **Benchmark ceiling.** Strong models sit near-ceiling on MATH-500, and a
      ceiling floors variance exactly like a floor does. AIME is likely to carry
      the headline; MATH-500 may end up the control.
- [x] **Does the provider honour `top_k` / `min_p`?** Probed 2026-08-27 with
      `scripts/probe_fireworks.py` across eight models. `top_k` 8/8, `top_p` 7/8,
      `temperature` 6/8, `min_p` 3/8. Support is per-MODEL, not per-provider.
      The `minp` cell was dropped as a result; see the decisions log.

## Phase 1 — Design lock (Aug 30)

- [x] Repo scaffold
- [x] Port variance decomposition from *Unauthored by Design*
- [x] Dependent variable -> binary correctness (`decompose_items`)
- [x] Dependent variable -> continuous per-problem solve rate
      (`solve_rates` + `decompose_solve_rate`), with binomial-noise correction
- [x] Answer normalisation + exact-match grader
- [x] Hand-verification harness (`scripts/verify_grader.py`, stratified)
- [ ] Actually run the 50-sample hand-verification (needs sweep output)
- [x] Benchmark loading: MATH-500 + AIME (`samplerconfound/benchmarks.py`,
      `scripts/fetch_benchmarks.py`), pinned by sha256 and stratified draws
- [ ] Generation harness with disk cache
- [x] Freeze the grid (`configs/*.template.json`, `scripts/freeze_grid.py`)
- [ ] Run the model pilot, then `scripts/select_models.py` -> `configs/main.json`
- [ ] Smoke-run end to end at 1/20 scale
- [x] Probe Fireworks sampler support and per-generation token cost

## Phase 2 — Main sweep (Sept 2)

- [ ] Run the full grid; log every generation, not just the verdict
- [ ] Accuracy-level decomposition -> sampler share vs model share
- [ ] Comparison-inversion rate
- [ ] Item-level three-way decomposition
- [ ] Bootstrap CIs on everything

## Phase 3 — Agentic extension (Sept 3)

- [ ] Multi-step condition: 1, 3, 5 steps
- [ ] Does sampler-attributable variance grow with step count?
- [ ] Report the null plainly if it does not

## Phase 4 — Writing (Sept 4-5)

- [ ] Results, Method, Intro, Related Work, Limitations, Abstract last
- [ ] Fig 1: variance decomposition stacked bar
- [ ] Fig 2: variance share by step count
- [ ] NeurIPS template, 4 pages, fully anonymised
- [ ] Submit Sept 5

## Decisions log

- **Replicate, not seed.** See README. Provider seed handling is undocumented and
  Fireworks is known to ignore it for text. The design does not depend on it.
- **API-only.** No GPU on this machine; local inference caps near 1-1.5B.
- **Accuracy-level is the headline, item-level is the supplement.** The ratio has
  to be stated in the units papers publish in, or reviewers will not accept that
  it bears on cross-paper comparison.
- **Verdicts are three-valued.** `correct` / `incorrect` / `unparseable`, and
  `Verdict.correct` returns `None` for the third rather than `False`. Standard
  harnesses score an unparseable response as wrong, which merges "got the maths
  wrong" with "formatted the answer oddly." Those come apart under decoding
  change — high temperature produces messier output — so a binary grader would
  route formatting variance straight into the sampler component and report it as
  the finding. Accuracy is reported both ways (`accuracy_strict`,
  `accuracy_parsed`) plus the unparseable rate per cell.
- **Solve rate gets a binomial-noise correction.** A rate measured from R
  replicates carries p(1-p)/R of sampling noise even when the underlying rate is
  fixed, and it all lands in the residual. Uncorrected, every variance share —
  the sampler share included — is deflated by a factor that depends on R, so the
  headline ratio would move when the sweep was rerun with a different replicate
  budget. That is a property of the budget, not of the world.
- **Integer answers require exact numeric equality.** A relative tolerance made
  1000000 and 1000001 match, so the grader got more forgiving exactly where the
  arithmetic got harder. Caught by a test, not by inspection.

- **The grid is frozen; the model levels are not chosen yet, on purpose.**
  Everything else — six sampler configs, 5 replicates, 200 MATH-500 problems,
  all 60 AIME 2024+2025 problems, max_tokens 2048, one prompt template — is
  fixed in `configs/main.template.json` and `configs/aime.template.json`. The
  model slot is empty and `Design.validate()` rejects an empty model list, so a
  template cannot be run by accident. Levels come from `select_models()`, a
  pre-registered rule written before any pilot data exists: keep candidates
  whose *measured* pilot accuracy falls in [0.55, 0.90], then take the 4-subset
  with the smallest spread, ties to family diversity. Selecting on published
  leaderboard numbers was rejected — each was measured under its own
  undisclosed decoding config, which is the defect this paper documents, so the
  grid would refute the paper.
- **Two configs, not one benchmark factor.** Benchmark is never a factor inside
  a single ANOVA: problem difficulty is not commensurable across MATH-500 and
  AIME and the cell counts differ. Each is decomposed separately.
- **max_tokens 2048, non-reasoning models only.** Reasoning models expose an
  effort / thinking-budget knob that moves accuracy on its own; it would sit
  inside the model factor and confound it with the decoding factor. Budget at 4
  models: 24,000 + 7,200 = 31,200 generations.
- **The pilot uses a disjoint problem draw** (`PILOT_PROBLEM_SEED = 1729`).
  Choosing models on the same items they are then scored on puts selection
  noise into the model component — the headline ratio's denominator.
- **Benchmarks are pinned, not fetched at run time.** `scripts/fetch_benchmarks.py`
  downloads MATH-500 (500) and AIME 2024+2025 (30+30) once into `data/*.jsonl`
  and records a sha256 per file in `data/MANIFEST.json`; `benchmarks.load()`
  verifies it on every load. The problem set is a factor in the three-way
  decomposition, so a dataset revised upstream between the pilot and the sweep
  would move the problem component and the residual with no code change to
  explain it.
- **Draws are hash-ordered, not `random`-ordered.** Problems are ranked by
  `sha256(f"{seed}:{id}")`, so a draw is identical on any interpreter and any
  platform. `random.Random.sample` is not contractually stable across CPython
  releases, and a draw that shifted under a Python upgrade would silently
  disagree with published numbers. The three splits are pinned by digest in
  `tests/test_benchmarks.py`.
- **The MATH-500 draw is stratified by level.** A uniform 200-of-500 leaves the
  difficulty mix to chance, and difficulty decides whether a cell sits near
  ceiling or floor — both of which flatten the variance being measured. Largest-
  remainder allocation keeps the realised mix within one problem of the full
  benchmark's, which also keeps the 200-problem accuracy comparable to published
  MATH-500 numbers.
- **Contamination does not threaten the design, with one exception.** Every
  reported quantity is a within-benchmark variance component, and contamination
  shifts the *level* of accuracy, which cancels out of a decomposition taken
  around the grand mean. The exception is *differential* contamination: a model
  that alone has memorised the set sits at ceiling, loses the variance decoding
  would produce, and deflates the sampler component. `PILOT_BAND`'s 0.90 upper
  edge prices such a model out of the grid, and including AIME 2025 — which
  postdates several candidates' cutoff — makes a 2024-vs-2025 gap visible.

## 2026-08-27 — the provider survey changed the design

- **No GPU.** Confirmed: Apple M4, 8 GPU cores, 16GB unified, no CUDA. Local
  inference caps near 1-1.5B, far under the band the grid needs. The bulk is API.
- **The frozen shortlist did not exist.** Every Qwen2.5, Llama-3.x,
  Mistral-Small and Gemma id returns 404. Fireworks serverless is now eighteen
  chat models and all are reasoning models, so the "non-reasoning instruct"
  criterion is unsatisfiable here at any price. Re-registered before any pilot
  data existed, which is the only moment that is legitimate.
- **`min_p` is honoured by 3 of 8 models, and it is per-model.** That is worse
  than a uniform failure: the `minp` cell would be a real condition for some
  levels and an exact duplicate of `hightemp` for others, manufacturing a
  model x sampler interaction indistinguishable from the finding the two-way
  decomposition reports. The cell was dropped; the grid is five configs. The
  inconsistency itself goes in the Discussion — a widely used decoding parameter
  accepted and discarded, silently and undocumented, is this paper's own thesis.
- **`top_p` is honoured by 7 of 8.** muse-glimmer-30b ignores it, which would
  break `standard` — the de facto default and the paper's motivating case.
  minimax-m2p7 rejects `temperature > 1.0` outright, so the grid's range is
  unreachable there. Both are excluded by `supports_grid()`; unprobed counts as
  unsupported, because an unverified parameter is indistinguishable from a
  working one until the numbers are already wrong.
- **Four of eight models are non-deterministic at temperature 0.** Recorded per
  candidate rather than averaged over. Per the earlier decision this is
  reportable, not disqualifying: it is genuine variance at a fixed configuration,
  which is what the replicate dimension measures.
- **`reasoning_effort` is pinned to "low" in FIXED.** At "medium" a single AIME
  generation exceeded a 180s read timeout, which across 26,000 generations is not
  a budget problem but an impossibility. It is a decoding-adjacent knob that
  moves accuracy on its own, so pinning and reporting it is the only honest
  option — and it belongs in the Discussion as the 2026 version of the same
  defect.
- **`max_tokens` 2048 -> 8192.** Truncation grades as unparseable rather than
  wrong, and truncation frequency rises with temperature, so a tight cap would
  manufacture sampler-correlated unparseability and report it as the finding.
  Billing is by tokens emitted, so headroom is free.
- **Budget is a constraint on the chosen SET, not on each candidate.** An even
  per-model division rejects a model costing slightly over its share even when
  the others are cheap enough to cover it. Five of fifteen 4-subsets are
  affordable, ranging $10.91 to $17.05 at 1.5x safety against $25.
- **Format compliance varies run to run.** The same AIME problem at T=0.7
  sometimes ends with `Answer: 204` and sometimes finishes cleanly without the
  required line. Early support for the three-valued grader, and a reason to
  expect the unparseable rate to be a live dependent variable rather than a
  rounding error.

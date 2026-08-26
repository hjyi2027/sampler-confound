# Tasks

Deadline **Sept 6**, submit **Sept 5**. Today is Aug 26; ai4math (Aug 29) comes
first and this project formally starts Aug 30.

## Open questions that block Phase 1

- [ ] **Provider key.** Decided: an open-weight provider (Fireworks or Together).
      Anthropic is ruled out — its API no longer exposes any decoding parameters
      (see README). No key for either provider is on this machine yet, and
      nothing past the smoke run can proceed without one.
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
- [ ] **Does the provider honour `top_k` / `min_p`?** Two of the six sampler
      configs depend on it. `scripts/probe_sampler_support.py` covers this and
      distinguishes REJECTED (loud, safe) from IGNORED (silent, corrupting).
      Needs porting to the OpenAI-compatible surface once the provider is fixed.

## Phase 1 — Design lock (Aug 30)

- [x] Repo scaffold
- [x] Port variance decomposition from *Unauthored by Design*
- [x] Dependent variable -> binary correctness (`decompose_items`)
- [x] Dependent variable -> continuous per-problem solve rate
      (`solve_rates` + `decompose_solve_rate`), with binomial-noise correction
- [x] Answer normalisation + exact-match grader
- [x] Hand-verification harness (`scripts/verify_grader.py`, stratified)
- [ ] Actually run the 50-sample hand-verification (needs sweep output)
- [ ] Benchmark loading: MATH-500 + AIME subset
- [ ] Generation harness with disk cache
- [x] Freeze the grid (`configs/*.template.json`, `scripts/freeze_grid.py`)
- [ ] Run the model pilot, then `scripts/select_models.py` -> `configs/main.json`
- [ ] Smoke-run end to end at 1/20 scale

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

# Tasks

Deadline **Sept 6**, submit **Sept 5**. Today is Aug 26; ai4math (Aug 29) comes
first and this project formally starts Aug 30.

## Open questions that block Phase 1

- [ ] **Provider key.** Decided: an open-weight provider (Fireworks or Together).
      Anthropic is ruled out — its API no longer exposes any decoding parameters
      (see README). No key for either provider is on this machine yet, and
      nothing past the smoke run can proceed without one.
- [ ] **Model shortlist.** Needs 3-4 open-weight instruct models scoring well
      clear of the floor. Prior work found only gpt-oss-120b survived heavily
      constrained prompts on Fireworks — a different task, but a warning about
      small-model brittleness.
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
- [x] Answer normalisation + exact-match grader
- [x] Hand-verification harness (`scripts/verify_grader.py`, stratified)
- [ ] Actually run the 50-sample hand-verification (needs sweep output)
- [ ] Benchmark loading: MATH-500 + AIME subset
- [ ] Generation harness with disk cache
- [ ] Freeze the grid in `configs/main.json`
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
- **Integer answers require exact numeric equality.** A relative tolerance made
  1000000 and 1000001 match, so the grader got more forgiving exactly where the
  arithmetic got harder. Caught by a test, not by inspection.

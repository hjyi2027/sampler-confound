# Tasks

Deadline **Sept 6**, submit **Sept 5**. Today is Aug 26; ai4math (Aug 29) comes
first and this project formally starts Aug 30.

## Open questions that block Phase 1

- [ ] **Provider + key.** `FIREWORKS_API_KEY` is not in the shell profile — only
      `ANTHROPIC_API_KEY` is. Confirm which provider serves the sweep before the
      grid is frozen; the client is OpenAI-compatible so Fireworks / Together /
      DeepInfra are all one config change, but the model list is not portable.
- [ ] **Model shortlist.** Needs 3-4 open-weight instruct models that actually
      score non-trivially on MATH-500, so the correctness signal is not floored.
      Prior work found only gpt-oss-120b survived heavily constrained prompts on
      Fireworks — different task, but a warning about small-model brittleness.
- [ ] **Does the provider honour `top_k` / `min_p`?** Two of the six sampler
      configs depend on it. If they are silently dropped, those cells are
      duplicates of the standard config and the grid must change. Verify with a
      degenerate-prompt probe before spending on the sweep.

## Phase 1 — Design lock (Aug 30)

- [x] Repo scaffold
- [x] Port variance decomposition from *Unauthored by Design*
- [ ] Answer normalisation + exact-match grader
- [ ] Hand-verify grader on 50 samples
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

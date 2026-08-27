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

## 2026-08-27 — grader hand-verification, 50 samples

Ran at last, against 500 real generations (2 models x 5 samplers x 50 problems,
pilot MATH-500 + AIME). **50/50 agreement, 100% in every stratum, zero false
positives.** Worksheet and labels in `runs/grader_check/`.

It did not start there. The first pass scored **30/50**, and the sample found
five distinct grader bugs, each a false negative:

1. **`\( ... \)` not stripped.** Models wrap answers in inline math far more
   often than in `$ ... $`. Nine of fifty items. Also `\displaystyle`, `\tfrac`.
2. **`(3)/(5)` vs `3/5`.** `frac_to_slash` parenthesises unconditionally so
   `\frac{a+b}{c}` stays correct, which then failed to match a model that wrote
   the slash form directly. Redundant parens around a bare token are spelling.
3. **`last_number` on truncated responses.** Eleven of fifty ran out of tokens
   mid-derivation. The fallback returned whatever number the model was last
   manipulating, and in ONE case that number was the gold answer — a response
   that never stated an answer was scored **correct**. `grade(truncated=True)`
   now refuses the fallback; `answer_line` and `boxed` still survive truncation.
4. **Markdown answer lines.** `**Answer: 204**` and `**Answer:** 204` defeated
   the `^\s*answer` anchor and fell through to `last_number`, which then picked
   "13" out of `\frac13`. All four residual disagreements were this.
5. **The worksheet truncated the wrong end.** Long responses were cut to the
   first 2000 chars, hiding the final answer — so the eleven most informative
   items were literally unverifiable as printed. Truncates the middle now.

Two process bugs, both of which produced a *wrong verification result* rather
than an error:

- **Labels were carried across a regrade by item number.** Fixing the grader
  moves records between strata, which changes the draw, so position-matched
  labels described different problems and reported a meaningless 60%. Worksheet
  items now carry a stable `key` (model|sampler|problem_id) and `--carry-labels`
  matches on it.
- **Verdicts written at generation time go stale** the moment `grade.py` changes.
  `--regrade` re-scores from stored responses with no API calls.

What the corpus says about the grader as a measuring instrument:

| sampler  | acc_strict | acc_parsed | unparseable | truncated | last_number |
|----------|-----------:|-----------:|------------:|----------:|------------:|
| greedy   |      78.0% |      88.6% |       12.0% |     11.0% |        0.0% |
| lowtemp  |      81.0% |      90.0% |       10.0% |     10.0% |        0.0% |
| standard |      80.0% |      88.9% |       10.0% |     10.0% |        0.0% |
| hightemp |      76.0% |      87.4% |       13.0% |     13.0% |        0.0% |
| topk     |      75.0% |      85.2% |       12.0% |     12.0% |        0.0% |

- **The `last_number` fallback is now used 0% of the time.** Before the fixes it
  ran at 12-17% and *rose with temperature* — the lenient extraction path being
  taken more often in exactly the direction that would have put grader leniency
  into the sampler component. Fixing the answer-line regex removed it entirely,
  which is a far better outcome than reporting it as a caveat.
- **Unparseable is now exactly truncation** (unparse% tracks truncated% to
  within a point). That is honest: those responses genuinely have no answer.
- **Truncation runs 10-13% and is highest at hightemp and topk.** A real effect,
  not a grader artifact, and concentrated in AIME. Worth watching: at 8192
  max_tokens roughly one AIME generation in eight yields no answer at all.

## 2026-08-27 — 1/20 smoke run, end to end

Ran `run_pilot.py -> select_models.py -> run_sweep.py -> analyse.py` at 1/20
scale. Only problems are scaled (200 -> 10, 60 -> 3); models, samplers and
replicates stay at full 4 x 5 x 5, because the design's shape is what a smoke run
exists to exercise. 1,300 generations, exactly 1/20 of 26,000.

**Mechanically it passed.** 1000/1000 and 300/300, zero failures, both grids
balanced. Retry-with-backoff and round-robin-by-model held: not one 429 survived,
against 35% losses before those were added.

It found four things worth the $1.50 it cost.

**1. The budget estimate was wrong by 3.4x, and wrong in a way an average hid.**
`pricing.py` assumed 300 output tokens per generation. Measured: 1,021 on
MATH-500, 2,252 on AIME. The full grid costs **$30.05 against a $25 budget**
before any safety factor. Worse, output length varies four-fold ACROSS MODELS and
correlates with price — minimax-m3 emits 2,057 tokens where gpt-oss-120b emits
519, and costs four times as much per token. Its grid share alone is $20.16; the
other three levels together are $9.79. A single per-benchmark average hid exactly
the model that breaks the budget. `MODEL_TOKENS` now carries measured per-model
profiles and `grid_cost_usd` prefers them, falling back to the global mean for
unmeasured candidates — which are therefore costed optimistically, so anything
new must be smoke-run before it is trusted.

**2. A bootstrap CI that excluded its own point estimate.** The run reported
`sampler:model ratio = 0.000, 95% CI [0.013, inf]`. `_boot_ratio_two_way` was
discarding every bootstrap replicate whose ratio was zero, but those zeros are
data: the components are method-of-moments estimates clamped at zero, so the
ratio's sampling distribution has a genuine atom there. Dropping them biases the
interval upward and, at an observed zero, produces an interval a reader would
take as significantly nonzero. Fixed; now reports `[0.000, 2.319]`.

**3. Only one affordable 4-subset now exists**, so the budget — not the near-peer
rule — determines the grid: nemotron-lightning + gpt-oss-20b + gpt-oss-120b +
deepseek-v4-flash, $17.45, pilot spread 0.08 across three vendors. That is
acceptable near-peer-wise but it means `select_models` is no longer choosing.
Worth stating in Limitations, or buying back headroom by cutting replicates.

**4. minimax-m3 has a large, model-specific unparseable rate** — 10-18% on
MATH-500 and 26-33% on AIME, against ~0% for every other model, and it rises with
temperature. Its strict and parsed accuracies differ by 10 to 33 points where the
others are identical. That single model was driving most of the model:sampler
interaction term. It is now priced out of the grid, but the effect is real and
worth a sentence: unparseability is not uniform across models, so a harness that
scores unparseable as wrong penalises some models far more than others.

**Wall clock, measured:** MATH-500 ran at 1.0 gen/s with 8 workers; AIME at
0.1-0.5. Extrapolated, the full sweep is roughly **6 hours for MATH-500 and 17+
for AIME**. It needs to run overnight, and resumability is not optional.

**On the numbers themselves: they mean nothing yet.** MATH-500 sits at 80-98% —
ceilinged, as predicted. Resampling variance is 55-68% of the total at this
scale, and the sampler component clamps to zero everywhere. Ten problems and
three problems cannot support a variance decomposition. The smoke run tested the
pipeline, not the hypothesis.

## 2026-08-27 — final grid decision

Budget headroom granted (a little over $25 acceptable). Decisions, in order of
how much they matter:

**minimax-m3 is excluded on scientific grounds, not cost.** Its accuracy is
dominated by a formatting artifact: 26-33% unparseable on AIME and 10-18% on
MATH-500, against ~0% for every other model, with 100% *parsed* accuracy on AIME.
Including it injects a large model main effect that inflates the headline ratio's
DENOMINATOR for a reason unrelated to model capability, plus a model:sampler
interaction that is about formatting rather than correctness. That is the same
class of error as the capability-ladder trap. It also doubles the near-peer
spread, 0.08 -> 0.16. Cost ($28.22 of a $30 budget) merely agrees.

**Four model levels is what the catalogue affords, and no budget fixes it.**
There is no fifth affordable near-peer candidate: muse-glimmer ignores top_p,
minimax-m2p7 rejects temperature > 1.0, kimi-k2p6 is $49 and degenerates at
T=1.5, and the unprobed frontier models price at $50-200 each at measured token
volumes. So the model component's precision is capped by the provider, not the
wallet — and that goes in Limitations.

**The headroom was spent on sampler levels instead, 5 -> 7.** Trap #4 says a
factor with k levels has realised level variance scattering by roughly
sqrt(2/(k-1)) — 71% at five, 63% at seven. Model levels cannot be bought;
sampler levels can, and the sampler component is the headline's numerator. Added
`midtemp` (T=0.5, top_p=0.9) and `tightnucleus` (T=1.0, top_p=0.8): both use only
parameters measured as honoured by all four levels, both are configurations
practitioners actually ship, and both were chosen before any sweep data existed.
This also restores the grid to seven conditions after min_p cost it one.

**nemotron-lightning's "narrow output distribution" flag does not transfer.**
The probe found 2/8 distinct completions at T=1.5, but that was a one-word noun
prompt with a strong mode. On the real task its accuracy range across samplers is
8% — identical to gpt-oss-20b's. Recorded because it nearly cost the grid a
level: a diversity probe on a degenerate prompt does not predict task-level
sampler sensitivity.

**But nemotron was being costed optimistically**, exactly as the fallback was
designed to warn: no measured profile, so it used the global mean. Measured, it
is the most verbose model in the catalogue — 1,576 output tokens on MATH-500 and
5,980 on AIME — though cheap enough per token ($0.20/M) that its grid share is
$4.80.

**Final grid:** 4 models (nemotron-lightning, gpt-oss-20b, gpt-oss-120b,
deepseek-v4-flash) x 7 samplers x 5 replicates x (200 MATH-500 + 60 AIME) =
**36,400 generations, $18.63 raw / $23.28 at 1.25x safety.**

`BUDGET_SAFETY` drops 1.5 -> 1.25: the blunt factor existed because token counts
were guessed from three problems at one temperature, and they now come from 1,300
generations spanning every sampler and model level.

**Wall clock is the real constraint, not money.** Measured 1.0 gen/s on MATH-500
and 0.1-0.5 on AIME at 8 workers, so 36,400 generations is roughly 16-20 hours.
It runs overnight and resumability is load-bearing. If that proves too long,
dropping back to five sampler configs is a one-line change that returns ~30% of
the wall clock at the cost of numerator precision.

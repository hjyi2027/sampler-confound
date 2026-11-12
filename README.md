# sampler-confound

**Documented, accepted, honoured: an audit of decoding parameters on a hosted
LLM inference API**

A standalone research project by Hyunjun Yi. The write-up is a technical
report in this repository, [paper/main.pdf](paper/main.pdf). It is not
submitted to any venue. The project was closed in November 2026, and
[Status](#status) lists what was left out.

When an evaluation sets `temperature`, `top_p` or `seed` in a request to a
hosted model, it assumes the provider applies them. This repository tests that
assumption on one provider, Fireworks AI: all 18 chat models in its serverless
catalogue, every sampling parameter its API reference documents, and four
parameters it accepts without documenting. Each (model, parameter) cell gets
three separate facts. *Documented* is what the reference says. *Accepted* is
whether a request that sets the parameter succeeds. *Honoured* is whether the
output distribution changes, decided by a two-sample permutation test on
completions, with a positive control that measures the test's power and a
negative control on identical arms. The data were collected 17 to 23 September
2026, and every result carries its collection date.

## Results

- The API accepts every documented parameter on every model: 162 of 162
  (model, parameter) pairs, `seed` included.
- For the eight parameters that shape the sampling distribution, 104 of 144
  cells show an effect, 7 are mixed (an effect on some prompts only), 5 are
  inert and 28 have no verdict. Inert is a strict label. The test had at least
  0.8 power against a greedy-strength effect, and the 95% upper bound on the
  effect is below the weakest effect the parameter has on any model that
  honours it.
- `top_p`, `top_k` and `min_p` show an effect in 47 of 54 cells and are inert
  in none.
- Whether `mirostat` does anything depends on the model. It is honoured on six
  models and inert on four behind the same endpoint (deepseek-v4-flash-0731,
  glm-5p2, muse-glimmer-30b, qwen3p8-max). The split runs inside model
  families: deepseek-v4p1-flash honours it and deepseek-v4-flash-0731 does
  not. `typical_p` is inert on muse-glimmer-30b.
- `frequency_penalty=2.0` breaks forced repetition on all 18 models. On short
  free-text replies it changes nothing detectable on 8 of them.
- Four vLLM parameters pass Fireworks' request validation and appear nowhere
  in its reference. `use_beam_search` and `skip_special_tokens` change nothing
  (9 cells that are accepted, undocumented and ignored). `ignore_eos` works on
  three models and returns raw control tokens in the text.
- At temperature 0, one model in 18 returns byte-identical text to ten
  identical concurrent requests, and two do when the requests go one at a
  time. On the others the most common reply is 20% to 78% of replies. A fixed
  seed, documented as "Random seed for deterministic sampling", does not raise
  agreement (permutation p = 0.52).

[FINDINGS.md](FINDINGS.md) has the detail, with the producing script for each
number. [paper/NUMBERS.md](paper/NUMBERS.md) traces every number in the report
to the committed record it comes from.

## Reproducing the numbers

Nothing below needs an API key or the network. The records under `runs/` keep
every completion behind every parameter verdict, so each verdict can be
recomputed under a changed test.

```bash
pip install -r requirements.txt
make test
make numbers                     # writes paper/NUMBERS.md
python3 scripts/paper_assets.py  # tables and figures for the report
make paper                       # builds paper/main.pdf, needs latexmk
```

Running a probe against the live API again gives a new dated observation, not
a reproduction. Temperature 0 does not reproduce on 17 of 18 models, and three
models were withdrawn from the catalogue while this project ran. The response
cache is not committed (too large, and not regenerable for that reason).

## What building the instrument taught

The first measurement of whether a parameter is honoured was wrong. An early
heuristic (`scripts/probe_fireworks.py`: eight samples, count the distinct
outputs) called `min_p` ignored on 3 of 8 models and `top_p` ignored on one,
and two design decisions were taken on it. A two-sample test with 40
completions per arm, a permutation null, a positive control and a negative
control on identical arms overturned every cell it could retest. A verdict
without a null and a power estimate cannot tell an ignored parameter from one
the probe could not see. The heuristic is kept as `make probe-heuristic`, for
the record.

What the documentation says is recorded apart from what the probe measures.
`samplerconfound/documented.py` is a dated transcription of each provider's
reference, with a quote and URL per parameter and an explicit `absent` where
the page does not mention it. `scripts/gap.py` puts it beside the
measurements. Fireworks rejects fifteen sampler names from other stacks
(`top_a`, `tfs`, `dry_multiplier` and others) with HTTP 400, which is why the
four vLLM names that pass get a table of their own.

A model in the frozen grid vanished mid-study. `gpt-oss-20b` began returning
404 within hours of the grid being frozen with it as a level, while the public
pricing page still listed it. It stays in `MODEL_CANDIDATES` with
`available: False` rather than being deleted.

Hand-verifying the grader on 50 stratified samples moved it from 30/50 to
50/50 agreement and found five bugs: inline `\(...\)` math never stripped,
`(3)/(5)` not matching `3/5`, markdown answer lines defeating the anchor, the
worksheet truncating the wrong end of long responses, and a `last_number`
fallback that credited a truncated response as correct because the number it
stopped on was the gold answer. Several of these tracked temperature, so they
would have put sampler-correlated error into the very component the study was
meant to measure.

Two intervals did not cover what they claimed. The bootstrap for the variance
ratio resampled replicates within cells and covered a known truth 22% of the
time against a nominal 95%. A binomial interval for the inversion rate covered
84%. Both now resample the unit the claim is about.

## Where this started: the variance study (future work)

The project began as a different paper, *The Sampler Is a Confound: Decoding
Configuration Rivals Model Choice in LLM Mathematical Reasoning*. Its claim
was that a reported math-reasoning score depends on model, prompt, benchmark
and decoding configuration, and that the last is rarely reported or held fixed
across papers. If sampler-attributable variance is within an order of
magnitude of model-attributable variance, a cross-paper comparison can invert
under a change nobody documented.

Two numbers were to carry it:

1. The comparison-inversion rate: across model pairs, how often the ranking
   flips when only the sampler changes. `inversion.quotable()` renders the
   strongest flips as sentences naming both accuracies and both configs. The
   interval comes from a bootstrap over problems. A binomial over comparisons
   covers 84% against a nominal 95% here, because each cell feeds about twelve
   comparisons.
2. The ratio of sampler variance to model variance from a fully crossed
   design, against a threshold of 0.1.

Neither has been measured, and compute is not the reason. A variance ratio
estimated from *k* model levels has a relative standard error of about
`sqrt(2/(k−1))`, which is 100% at three levels and 58% at seven. The frozen
grid (three near-peer models, seven samplers, five replicates, 260 problems,
about $16) was affordable. It was not run because, with sampler and model
variance truly equal, an interval for the ratio clears 0.1 in only 57% of
simulated grids (`scripts/power_check.py`). More problems and more replicates
do not change that. More model levels do, and one provider's band of near-peer
models does not have enough of them. The audit above started as the check on
the infrastructure the study would run on, and it became the project.

A planned agentic extension, testing whether sampler variance compounds over
multi-step reasoning, was never built.

### What is ready for it

Benchmarks pinned by sha256, the grader hand-verified, the grid frozen, a
resumable runner that checks balance and logs full generations (including
`reasoning_content`, a separate field that was being discarded), and both
metrics with their power characterised. The sweep as frozen:

```bash
python3 scripts/run_sweep.py --config configs/main.json
python3 scripts/run_sweep.py --config configs/aime.json
python3 scripts/analyse.py runs/main/math500.jsonl --n-boot 2000
```

### The design

Fully crossed model × sampler, with problems as a crossed blocking factor and
repeated sampling as the replicate dimension.

```
model (3)  x  sampler (7)  x  problem (200 MATH-500 + 60 AIME)  x  replicate (5)
```

Frozen in `configs/main.json` and `configs/aime.json`, 27,300 generations.
There are three models rather than four because `gpt-oss-20b` was withdrawn
from the provider hours after the grid was frozen with it as a level. There
are seven samplers rather than six because the early heuristic made `min_p`
look unusable and two temperature/top-p configurations were added to buy back
precision on the ratio's numerator.

The primary analysis is at the accuracy level. It collapses to the number a
paper would report, benchmark accuracy per (model, sampler, replicate). That
is a balanced two-way design with 5 replicates per cell:

```
SS_total = SS_model + SS_sampler + SS_model:sampler + SS_within
                                                      ^^^^^^^^^
                                                      resampling
```

The same 200 problems appear in every cell, so problem drops out here. The
ratio comes from this analysis because it is stated in the units papers
publish in.

The secondary analysis is a three-way random-effects decomposition with
problem as a third crossed factor, run on two responses. Binary correctness,
one 0/1 per generation, shows where the variance lives. The per-problem solve
rate, the fraction of replicates that solved each problem, breaks the coupling
between a Bernoulli's variance p(1-p) and where the cell means sit. Its cost
is the replicate stratum: one rate per cell means the three-way interaction
becomes the error term. If the two responses agree, the result is a fact about
the data rather than the response scale. If they disagree, that is the
finding. Solve rates carry binomial noise of p(1-p)/R from running R
replicates, all of it in the residual, so it is estimated and subtracted.
Uncorrected, every variance share would depend on the replicate budget.

### The replicate dimension is not "seed"

Within-cell variance is sampling variance at fixed configuration, whether or
not a seed was honoured. Seed was never a main effect here, since its levels
carry no meaning across cells. That matters because Fireworks does not honour
`seed` on text completions, which the audit measures on all 18 models
(FINDINGS §2). Calling the replicate dimension "seed variance" would be a
claim about determinism the provider does not support. It is also the more
useful quantity: the variance a practitioner gets when they rerun an eval.

The plan expected greedy cells to show near-zero within-cell variance and said
that if they did not, that would be a finding. The audit found exactly that.
Greedy decoding on this provider is a draw from a narrow distribution.

### Machine and provider constraints

There is no GPU. This is a 16 GB laptop, local inference tops out near 1 to
1.5B parameters, and models at that scale cannot do MATH-500 well enough for
correctness variance to mean anything. So the study is API-only, on an
open-weight provider.

The Anthropic API was ruled out by measurement. As of `anthropic` 1.0.0,
`temperature`, `top_p` and `top_k` are gone from the signature of
`messages.create()` and from `MessageCreateParams`, and `min_p` and `seed`
were never there. Sampling control on current Claude models moved to
`output_config.effort`. Passing `temperature` raises `TypeError` client-side
before a request is made (`runs/sampler_support.json`,
`scripts/probe_sampler_support.py`). The independent variable of this study
does not exist on that provider.

Budget, measured on the 1/20 smoke run:

| | |
|---|---|
| Generations | 3 models x 7 samplers x 5 reps x 260 problems = 27,300 |
| Output tokens | 1,021 mean on MATH-500, 2,252 on AIME |
| Cost | $16.32 raw, $20.40 at 1.25x safety |
| Wall clock | ~15 hours at 1.0 gen/s (MATH-500) and 0.1-0.5 (AIME), 8 workers |

Output length varies four-fold across models and correlates with price, so
`config.MODEL_TOKENS` holds a measured per-model profile. A model with no
measured profile is costed optimistically and must be smoke-run before it is
trusted.

### Lineage

The variance design reuses machinery from two earlier unpublished studies by
the same author. Neither is public, so nothing here rests on them. They are
named for provenance only:

- *Unauthored by Design* (`seed-study-creative`): the fully crossed
  variance-decomposition design and the EMS-corrected component estimator,
  ported here in `variance.py`.
- *Does the Decoding Algorithm Have a Voice?*: the finding that the sampler
  leaves a recoverable signature in style.

## Providers

Every provider sits behind an adapter (`samplerconfound/adapters.py`). The
probes build one canonical request and read one `Completion`, and never see a
vendor's wire shape. Seven providers are wired up: Fireworks, Groq, Cerebras,
OpenRouter, NVIDIA and Mistral speak the OpenAI chat shape and differ only in
facts (URL, key header, a rename: Mistral says `random_seed`), and Google
speaks its native `generateContent`, because that is the surface with `topK`
on it. `--provider groq` on either probe is the whole change. Keys go in
`.env` (gitignored, slots in `.env.example`), and `make keys` checks them.
Only Fireworks has been run.

## Rate limits and resumability

Rate limits bite at hour six, so both halves are built in and tested against
a fake throttled provider (`tests/test_ratelimit.py`). Each rule below was met
in production before it was a rule.

Pacing starts before the 429. Every response carries the account's remaining
budget. The adapter normalises those headers, and a per-provider pacer
(`samplerconfound/ratelimit.py`) makes callers wait toward the window's reset
once headroom drops below 10%. Three throttled responses in a row open a
breaker that pauses the whole process once, not one sleep per worker.
`Retry-After` is honoured exactly. Fireworks' binding limit is 72,000
generated tokens a minute, account-wide.

Retries are bounded. Timeouts and dropped connections get two retries, a
recurring 5xx gets four tries, and a 429 gets short, capped, jittered waits.
An exponential ladder put twenty workers to sleep through the refill and had
them burst again, so it is gone.

Everything resumes. Every response is cached under its request hash and
replicate, so a rerun bills nothing it already has. The distinguishability
probe checkpoints per cell and skips finished cells on restart. A cell the
provider did not finish is recorded as `transport` and rerun only with
`--retry-failed`. The sweep checkpoints per record with fsync and was
hard-killed and resumed to prove it. The matrix runner skips covered cells,
runs only missing contrasts, continues its budget window across relaunches,
and kills its child probes on SIGINT/SIGTERM. Rerun the same command to
resume.

Full decision log in [TASKS.md](TASKS.md).

## Status

Closed as of 12 November 2026. The audit is complete for the one provider it
covers, and the report, the records and the code agree with each other. A
fresh clone, with only `requirements.txt` installed, passes the test suite and
regenerates the same tables and the same `paper/NUMBERS.md`.

Four things were left out. A second provider would show whether the findings
are specific to Fireworks, and it needs accounts and keys for the other six
adapters. The seed was tested at temperature 0 only. A re-run of the probes
would be a new dated snapshot. The variance study needs more model levels than
one provider offers. [TASKS.md](TASKS.md) is the full log, including the
decisions that were reversed.

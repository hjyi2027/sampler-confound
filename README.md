# sampler-confound

**The Sampler Is a Confound: Decoding Configuration Rivals Model Choice in LLM
Mathematical Reasoning**

Target was MATH-AI 2026 (NeurIPS workshop), 4 pages, Sept 6. **That deadline
passed on 2026-09-06 and the sweep was never run** — see [Status](#status). The
infrastructure is complete and validated; what is missing is one command and
about $16 of API credit. Everything below describes the design as frozen, and
the numbers quoted from `runs/` are from a 1/20 smoke run unless stated.

## The claim

Every reported math-reasoning number is a function of model, prompt, benchmark,
*and* decoding configuration. The last one is almost never reported and almost
never held constant across papers. If sampler-attributable variance is within an
order of magnitude of model-attributable variance, then cross-paper comparisons
on math benchmarks are unsound — not noisy, unsound, because the comparison can
invert under a change nobody documented.

Two numbers carry the paper. Their order was swapped on 2026-08-27 after a power
analysis, and the reason is in `scripts/power_check.py`:

1. **Comparison-inversion rate** (the headline). Across model pairs, how often
   the ranking flips when only the sampler changes. One inverted comparison is a
   concrete, quotable harm; a rate is a finding. `inversion.quotable()` renders
   the strongest ones as sentences naming both accuracies and both configs.
   Interval from a bootstrap over *problems* — a binomial over comparisons is
   anti-conservative here, covering 84% against a nominal 95%, because each cell
   feeds about twelve comparisons.
2. **Sampler share vs model share** — variance components from a fully crossed
   design, stated as a ratio against a threshold of 0.1 ("within an order of
   magnitude"). Reported as a *measurement*, not a test: with three model levels
   the interval clears 0.1 only 57% of the time even when sampler and model
   variance are truly equal. That is a property of the level count, and no
   budget fixes it.

The agentic extension is what makes this MATH-AI rather than generic: sampler
variance compounds across multi-step reasoning, so agentic evaluation is noisier
than single-shot evaluation by an amount nobody has measured.

## Lineage

This extends two published AIscend studies and deliberately reuses their
machinery rather than building a method:

- *Unauthored by Design* (`seed-study-creative`) — the fully crossed
  variance-decomposition design and the EMS-corrected component estimator. Ported
  here in `variance.py`.
- *Does the Decoding Algorithm Have a Voice?* — the finding that the sampler
  leaves a recoverable signature in style.

The contribution here is changing the dependent variable from style features to
**correctness**. That is a seven-day project. Building a method would not be.

## The design

Fully crossed **model × sampler**, with **problems** as a crossed blocking factor
and **repeated sampling** as the replicate dimension.

```
model (3)  x  sampler (7)  x  problem (200 MATH-500 + 60 AIME)  x  replicate (5)
```

Frozen in `configs/main.json` and `configs/aime.json`; **27,300 generations**.
Three models rather than four because `gpt-oss-20b` was withdrawn from the
provider hours after the grid was frozen with it as a level. Seven samplers
rather than six because `min_p` proved unusable (below) and two temperature/top-p
configurations were added to buy back precision on the headline's numerator.

Two analyses, answering different questions.

**Primary — accuracy level.** Collapse to the number a paper would actually
report: benchmark accuracy per (model, sampler, replicate). That is a balanced
two-way design with 5 replicates per cell, and it maps exactly onto the ported
estimator:

```
SS_total = SS_model + SS_sampler + SS_model:sampler + SS_within
                                                      ^^^^^^^^^
                                                      resampling
```

The same 200 problems appear in every cell, so problem is a within-subject
constant here and drops out. This is the analysis the headline ratio comes from,
because it is stated in the units papers publish in.

**Secondary — item level.** Three-way random-effects decomposition with problem
as a third crossed factor, run on two different responses:

- **binary correctness**, one 0/1 per generation. Shows where the variance lives.
- **continuous per-problem solve rate**, the fraction of replicates that solved
  each problem. A Bernoulli's variance is p(1-p), pinned to zero at both ends, so
  a component estimated from 0/1 data partly reflects where the cell means sit
  rather than how far the factor moves them. Averaging over replicates breaks that
  coupling. The cost is the replicate stratum: one rate per cell means no
  replication, so the three-way interaction becomes the error term.

Reporting both is the point. If they agree, the result is a fact about the data
rather than about the response scale. If they disagree, that is the finding.

Solve rates carry binomial noise of p(1-p)/R from having run R replicates rather
than infinitely many, and it lands entirely in the residual. It is estimated and
subtracted; uncorrected, every variance share would depend on the replicate
budget.

### On the replicate dimension

The roadmap calls this "seed." It is not, and the paper must not call it that.

Fireworks silently ignores the `seed` parameter on text completions — established
the hard way in `seed-study-creative`, and the reason that study's text arm was
reframed. Any hosted provider may do the same, and none of them document it.

This design does not need honoured seeds. Seed was never a main effect here: its
levels carry no meaning across cells, so it is the **replicate** dimension, and
within-cell variance *is* resampling variance whether or not a seed was honoured.
Calling it "seed variance" would be a claim about determinism the provider will
not support. Calling it **sampling variance at fixed configuration** is what the
design actually measures, and it is the more useful quantity anyway: it is the
variance a practitioner eats when they rerun an eval.

One consequence to expect and report: **greedy cells should show near-zero
within-cell variance.** If they do not, the provider is non-deterministic even at
T=0 (batching, kernel non-determinism), which is itself a reportable finding and
a validity check on every greedy-decoding paper.

## Machine constraints

The roadmap says "open weights on your GPU for the bulk." There is no GPU. This
is a MacBook Air M4, 16 GB unified memory, ~18 GB free disk. Local inference tops
out near 1-1.5B parameters and swap eats any long run — established in prior
work. Models at that scale cannot do MATH-500 well enough for correctness
variance to mean anything.

**Therefore the sweep is API-only, on an open-weight provider.**

The Anthropic API was ruled out empirically, not on preference. As of `anthropic`
1.0.0, `temperature`, `top_p` and `top_k` are gone from the signature of
`messages.create()` and from `MessageCreateParams`; `min_p` and `seed` were never
there. Sampling control was removed from current Claude models in favour of
`output_config.effort`. Passing `temperature` raises `TypeError` client-side
before a request is made — see `runs/sampler_support.json` and
`scripts/probe_sampler_support.py`.

That is not a limitation to note in a Limitations section. This study's
independent variable is decoding configuration, and on that provider it does not
exist.

The observation is worth keeping for the Discussion: the largest commercial
provider has removed decoding parameters from its API entirely, which is evidence
the field half-knows this matters. It does not rescue the experiment.

Budget matters and is planned for:

Measured, not estimated — the 1/20 smoke run replaced every guess here:

| | |
|---|---|
| Generations | 3 models x 7 samplers x 5 reps x 260 problems = **27,300** |
| Output tokens | **1,021** mean on MATH-500, **2,252** on AIME (not the ~600 first assumed) |
| Cost | **$16.32** raw, $20.40 at 1.25x safety |
| Wall clock | ~15 hours at 1.0 gen/s (MATH-500) and 0.1-0.5 (AIME), 8 workers |
| Agentic extension | multiplies by step count — budget separately |

Output length varies four-fold *across models* and correlates with price, so
`config.MODEL_TOKENS` holds a measured per-model profile; a model with no
measured profile is costed optimistically and must be smoke-run before it is
trusted. Results append to JSONL keyed by (model, sampler, replicate, problem),
so an interrupted sweep resumes without re-spending, and `--verify` refuses to
report an unbalanced grid rather than averaging over the gaps.

## What the build established, independent of the sweep

Written up in full in [FINDINGS.md](FINDINGS.md). In brief:

These came out of getting the instrument working and stand on their own. Several
are perishable — the provider catalogue moved twice during one week.

**Decoding parameters are honoured inconsistently, per model, silently.** Probing
eight models on one provider (`scripts/probe_fireworks.py`, behavioural checks
rather than trusting acceptance): `top_k` 8/8, `top_p` 7/8, `temperature` 6/8,
`min_p` **3/8**. A parameter accepted and discarded turns its grid cell into a
duplicate of another for that model only, fabricating a model x sampler
interaction indistinguishable from a finding. `min_p` was dropped from the design
for this reason. `muse-glimmer-30b` ignores `top_p`, which would silently break
the `standard` config every harness claims to use.

**Four of eight models are non-deterministic at temperature 0**, so greedy
decoding is not reproducible even in principle on those.

**A benchmark model vanished mid-study.** `gpt-oss-20b` began returning 404 from
the inference API within hours of the grid being frozen with it as a level, while
still listed on the public pricing page. Kept in `MODEL_CANDIDATES` with
`available: False` rather than deleted.

**Hand-verifying the grader on 50 stratified samples moved it from 30/50 to
50/50 agreement**, finding five distinct bugs — inline `\(...\)` math never
stripped, `(3)/(5)` not matching `3/5`, markdown answer lines defeating the
anchor, the worksheet truncating the wrong end of long responses, and a
`last_number` fallback that credited a truncated response as correct because the
number it happened to stop on was the gold answer. Grader bugs are not
symmetrical here: several of them tracked temperature, so they would have
delivered sampler-correlated error straight into the component being reported.

**Two reported intervals did not cover what they claimed.** The bootstrap for the
variance ratio resampled replicates within cell and covered a known truth 22% of
the time against a nominal 95%; a binomial interval for the inversion rate
covered 84%. Both are replaced by resampling the unit the claim is actually
about.

## Status

The sweep has not run. `runs/` holds the pilot, a 1/20 smoke run, and the
provider probes. Ready and validated: benchmarks pinned by sha256, grader
hand-verified, grid frozen, runner resumable and balance-checked with full
generation logging (including `reasoning_content`, which is a separate field and
was being discarded), both metrics implemented with their power characterised.

Run it with:

```
python3 scripts/run_sweep.py --config configs/main.json
python3 scripts/run_sweep.py --config configs/aime.json
python3 scripts/analyse.py runs/main/math500.jsonl --n-boot 2000
```

Full decision log in [TASKS.md](TASKS.md).

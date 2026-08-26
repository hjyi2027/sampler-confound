# sampler-confound

**The Sampler Is a Confound: Decoding Configuration Rivals Model Choice in LLM
Mathematical Reasoning**

Target: MATH-AI 2026 (NeurIPS workshop), 4 pages, **Sept 6**. Submit Sept 5.

## The claim

Every reported math-reasoning number is a function of model, prompt, benchmark,
*and* decoding configuration. The last one is almost never reported and almost
never held constant across papers. If sampler-attributable variance is within an
order of magnitude of model-attributable variance, then cross-paper comparisons
on math benchmarks are unsound — not noisy, unsound, because the comparison can
invert under a change nobody documented.

Two numbers carry the paper:

1. **Sampler share vs model share** — variance components from a fully crossed
   design, stated as a ratio with a bootstrap interval.
2. **Comparison-inversion rate** — across model pairs, how often the ranking
   flips when only the sampler changes. One inverted comparison is a concrete,
   quotable harm; a rate is a finding.

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
model (3-4)  x  sampler (6)  x  problem (200)  x  replicate (5)
```

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

**Secondary — item level.** Three-way random-effects decomposition on the binary
per-problem outcome, with problem as a third crossed factor. This shows *where*
the variance lives and supports the compounding analysis. It is a supplement, not
the headline: binary outcomes have mean-dependent variance and the components are
harder to defend in four pages.

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

| | |
|---|---|
| Generations | 3 models x 6 samplers x 5 reps x 200 problems = **18,000** |
| Output tokens | ~600 each ≈ **10.8M** |
| Cost, 7-8B class | low single-digit USD |
| Cost, 70B+ / frontier | tens of USD |
| Agentic extension | multiplies by step count — budget separately |

Every response is cached on disk under a hash of the request, so an interrupted
sweep resumes without re-spending and re-analysis never re-generates.

## Status

See [TASKS.md](TASKS.md).

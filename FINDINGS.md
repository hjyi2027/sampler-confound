# What the instrument found

Notes from building a measurement apparatus for *The Sampler Is a Confound*, a
study that has not run. Every finding below came out of getting the instrument
working, and every one of them is separable from the study's own hypothesis: they
are facts about **the infrastructure that LLM evaluation papers depend on**, not
about whether decoding configuration rivals model choice.

They are recorded separately because several are perishable. The provider
catalogue changed twice in the eight days it took to build this, and one of the
findings is that change itself.

All figures are measured, with the producing script named. Dates matter here and
are given.

---

## 1. Decoding parameters are honoured inconsistently, per model, and silently

Ten models on one provider (Fireworks serverless), probed 2026-08-27 and
2026-08-28 by `scripts/probe_fireworks.py`:

| model | temperature | top_p | top_k | min_p | deterministic at T=0 |
|---|:--:|:--:|:--:|:--:|:--:|
| nemotron-lightning-3p5-30b-a3b | yes | yes | yes | **no** | yes |
| gpt-oss-20b | yes | yes | yes | yes | yes |
| gpt-oss-120b | yes | yes | yes | yes | yes |
| deepseek-v4-flash-0731 | yes | yes | yes | **no** | **no** |
| minimax-m3 | yes | yes | yes | yes | yes |
| muse-glimmer-30b | yes | **no** | yes | **no** | **no** |
| minimax-m2p7 | **no** (rejects > 1.0) | — | — | — | **no** |
| qwen3p7-plus | yes | yes | yes | yes | **no** |
| nemotron-3-ultra-nvfp4 | yes | yes | yes | yes | yes |
| kimi-k2p6 | yes | yes | yes | **no** | **no** |

**`min_p` is honoured by 5 of 10. `top_p` by 8 of 10.** Support is per *model*,
not per provider, and it is not documented anywhere.

The failure is silent. An ignored parameter does not error; the request succeeds,
the response looks normal, and the experimental cell it defines becomes a
duplicate of some other cell — for that model only. In a crossed design that
fabricates a model × sampler interaction out of nothing, and the interaction term
is a quantity such studies report. The artifact is indistinguishable from the
finding.

`muse-glimmer-30b` ignoring `top_p` is the sharpest case: `temperature 0.7,
top_p 0.95` is the configuration most evaluation harnesses claim to use, so on
that model the default condition silently is not the default condition.

**How to detect it.** Acceptance proves nothing. The probe sets each parameter to
a value so extreme that honouring it *must* collapse the output distribution —
`top_p = 0.01`, `top_k = 1`, `min_p = 0.9` at temperature 1.5 — and samples eight
completions of a high-entropy prompt. If diversity survives, the parameter was
discarded. This costs a few cents and should precede any study whose independent
variable is a decoding parameter.

## 2. Half the models are non-deterministic at temperature 0

Five of ten returned more than one distinct completion at `temperature = 0` over
eight samples of an identical prompt. Greedy decoding is not reproducible even in
principle on those models, which bears on every paper that reports a single
greedy number and treats it as a fixed property of the model.

This also breaks a natural statistical shortcut, discussed in §5.

## 3. A benchmark model was withdrawn mid-study

`gpt-oss-20b` was one of four model levels when the grid was frozen on
2026-08-27. Within hours it began returning HTTP 404 from the inference API. It
was **still 404 on 2026-09-07, eleven days later**, and remained listed on the
public pricing page throughout.

No affordable near-peer replacement existed on the catalogue: the two candidates
that honoured every parameter cost $52.70 and more for their share of the grid,
against a whole-study budget of about $16. The design dropped to three model
levels, which is not a cosmetic loss — see §6.

The general point for reproducibility: a served model is not a fixed artifact.
A paper that reports "gpt-oss-20b scores X" is reporting on something a reader
may be unable to obtain, with no version, no deprecation notice, and a pricing
page that still advertises it.

## 4. Grader failure modes, and why they are not symmetric

An exact-match grader with a normalisation pass was hand-verified against 50
stratified samples drawn from 500 real generations
(`scripts/verify_grader.py`). It scored **30/50 agreement on the first pass and
50/50 after five fixes**, with zero false positives at the end.

The bugs:

1. **Inline math delimiters never stripped.** Models write `\(\frac{1}{2}\)` far
   more often than `$\frac{1}{2}$`. With `\tfrac` and the fraction bug below,
   this family accounted for **all nine** of the first pass's false negatives —
   correct answers scored wrong purely on spelling.
2. **`(3)/(5)` not matching `3/5`.** Fraction normalisation parenthesised
   unconditionally, so a model writing the slash form never matched a gold
   answer written as `\frac{3}{5}`.
3. **Markdown answer lines.** `**Answer: 204**` defeated a `^\s*answer` anchor
   and fell through to a last-number fallback, which then extracted `13` from
   `\frac13`.
4. **A last-number fallback crediting truncated responses.** Eleven of fifty
   responses ran out of tokens mid-derivation. The fallback returned whatever
   number the model was last manipulating, and in one case that number *was* the
   gold answer — a response that never stated an answer was scored **correct**.
5. **The verification worksheet truncated the wrong end.** Long responses were
   cut to their first 2000 characters, hiding the final answer, so the eleven
   most informative items were literally unverifiable as printed.

**The asymmetry that matters.** Several of these track temperature. High
temperature produces longer, messier output: more truncation, more markdown
decoration, more fallback extraction. A grader whose error rate rises with
temperature delivers sampler-correlated error directly into the component such a
study reports, and would be read as the finding. Before the fixes, the lenient
fallback path was used 12–17% of the time, highest at the two hottest
configurations (17% at temperature 1.0 and at top-k, against 12% at temperature
0.3 — the trend is real but not monotonic, greedy sitting at 14%). After the
fixes it is used **0% of the time, in every configuration**, which removes the
correlation at its source rather than correcting for it.

Two process bugs are worth naming because each produced a *wrong answer* rather
than an error. Labels were carried across a re-grade by item number — but fixing
the grader moves records between strata, changing the sample, so the labels then
described different problems and reported a confident, meaningless 60%. And
verdicts stored at generation time go stale the moment the grader changes, so a
verification run silently measures a grader that no longer exists.

## 5. Two reported intervals did not cover what they claimed

Both were the obvious choice, and both were wrong in the same way: they resampled
a unit other than the one the claim was about.

**Variance-ratio interval: 22% coverage against a nominal 95%.** The
sampler-to-model variance ratio was given a bootstrap over replicates within
cell. Measured over 60 simulated grids of the study's exact shape with a known
true ratio, that interval contained the truth 22% of the time. It propagates
measurement noise and none of the uncertainty from having few factor *levels*,
which is what dominates a variance component. Replaced by a parametric bootstrap
that regenerates whole grids from the fitted components: 83% coverage — better,
still optimistic, and reported as approximate.

**Inversion-rate interval: 84% coverage.** The comparison-inversion rate is a
proportion over model-pair × sampler-pair comparisons, so a binomial interval is
the natural reach. But with 3 models and 7 samplers there are 63 comparisons
drawn from 21 cells — each cell feeds about twelve of them. Treating them as
independent draws is anti-conservative. Replaced by a bootstrap over *problems*,
which is the sampling unit the claim concerns: 100% coverage in the same test.

The sharpest way to see the second one: **a binomial width depends only on the
comparison count, so halving the problems leaves it unchanged** — even though
half the evidence is gone.

There is a general rule here, and it is not novel, only easy to violate:
bootstrap the unit your claim generalises over. For "would a rerun differ",
resample replicates. For "would a different benchmark sample differ", resample
problems. For "would different models differ", resample models — and if you have
three, say so instead of reporting an interval that cannot know.

Note also that §2 breaks the shortcut of judging a difference against
within-cell replicate spread: at temperature 0 on a deterministic model that
spread is exactly zero, so any nonzero difference passes any threshold. Every
greedy comparison becomes "significant" by construction.

## 6. Variance-ratio claims need many factor levels, and no budget buys them

A variance component estimated from *k* factor levels has relative scatter of
roughly `sqrt(2/(k-1))`: **100% at three levels, 82% at four, 71% at five, 58% at
seven.** This is a property of the level count alone. More problems, more
replicates and more API spend leave it untouched.

Simulated at the study's grid shape with realistic parameters
(`scripts/power_check.py`), asking how often a level-aware interval would clear a
threshold of 0.1 — "sampler variance within an order of magnitude of model
variance":

| true ratio | point estimate clears | interval clears (3 models) | (4 models) |
|---:|---:|---:|---:|
| 0.05 | 40% | 0% | 0% |
| 0.10 | 52% | 0% | 0% |
| 0.25 | 72% | 10% | 18% |
| 0.50 | 82% | 35% | 32% |
| 1.00 | 95% | **57%** | 60% |
| 2.00 | 98% | 78% | 82% |

**Even when sampler and model variance are exactly equal, the interval clears the
threshold only 57% of the time.** A CI-backed claim of this shape is out of reach
at three or four model levels, and adding the fourth barely moves it.

The practical consequence: a variance-decomposition study over a handful of
models can *measure* a ratio but cannot *test* one, and the distinction should be
stated rather than hidden inside a bootstrap interval that happens to look
narrow. Where a claim must be testable, a statistic that counts outcomes over
observed cells — such as how often a ranking inverts — needs no few-level
extrapolation and is far better powered.

---

## Checklist for anyone running a decoding-configuration study

1. Probe every parameter behaviourally, per model, with values extreme enough
   that being honoured is visible. Do not trust acceptance, and do not assume
   support is uniform across a provider's catalogue.
2. Check determinism at temperature 0 explicitly.
3. Pin the problem set by hash; record the resolved request parameters with every
   generation, not just the config file.
4. Keep the full generation, including any separate reasoning field. On these
   models that field carried the majority of the tokens and was easy to discard
   unrecoverably.
5. Hand-verify the grader on a stratified sample that over-weights the rare
   cases, and check whether its error rate correlates with your independent
   variable.
6. Score unparseable responses as a third outcome, not as wrong.
7. Bootstrap the unit your claim generalises over.
8. Compute the power of the claim before spending on the run.

## Provenance

| finding | script | data |
|---|---|---|
| §1, §2 | `scripts/probe_fireworks.py` | `runs/probe_params_*.json`, `runs/probe_replacement.json` |
| §3 | — | `MODEL_CANDIDATES` in `samplerconfound/config.py` |
| §4 | `scripts/verify_grader.py`, `scripts/sample_for_grader_check.py` | `runs/grader_check/` |
| §5 | `tests/test_variance.py`, `tests/test_inversion.py` | simulation |
| §6 | `scripts/power_check.py` | simulation |

The study these came from is described in [README.md](README.md); the dated
decision log is [TASKS.md](TASKS.md).

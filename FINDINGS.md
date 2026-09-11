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

## 1. Decoding parameters: what a properly powered test says

**This section replaces an earlier claim of my own that did not survive
testing.** The first version reported that `min_p` was honoured by 5 of 10 models
and `top_p` by 8 of 10, from a probe that sampled eight completions at an extreme
setting and called the parameter ignored if three or more were distinct. That is
a yes/no with an arbitrary cutoff and no null, and it was wrong in the more
dangerous direction: it was not detecting ignored parameters, it was failing to
detect honoured ones.

The proper test holds everything fixed, varies one parameter between two settings
far apart in its range, draws 40 completions at each, and asks whether the two
output distributions are distinguishable. **The null is exactly the failure
mode** — if the parameter is ignored, both arms are the same configuration, the
2N completions are iid from one distribution, and the labelled samples are
exchangeable. A permutation test is then exact at any N with no distributional
assumptions.

Primary statistic: the one-sided entropy drop `dH = H(open) - H(tight)`, in bits,
matching the mechanism (truncation narrows the distribution). Total variation
distance is reported alongside as a two-sided check. Both are biased away from
zero at finite N, so both are read against their own permutation null rather than
against zero. Calibration before use: false-positive rate 5.5% at nominal 5%,
power above 95% against a collapse effect.

**A null result needs a positive control before it means anything.** "Not
distinguishable" is produced identically by a parameter the provider ignores and
by a probe that has no power on that model, and the test cannot tell them apart
from the inside. So for every model, on the same prompt, the parameter known to
work — temperature, 0 vs 1.5, the widest contrast available — must show a large
effect first. Its entropy drop is the ceiling on what any other test can show
there.

| model | H open | H at T=0 | distinct at T=0 | control removed | status |
|---|--:|--:|--:|--:|---|
| deepseek-v4-flash-0731 | 5.25 | 2.61 | 12/40 | 50% | powered |
| gpt-oss-120b | 5.32 | -0.00 | 1/40 | 100% | powered |
| kimi-k2p6 | 4.81 | 3.27 | 13/34 | 32% | **weak** |
| minimax-m3 | 4.89 | -0.00 | 1/40 | 100% | powered |
| muse-glimmer-30b | 4.83 | 0.95 | 2/40 | 80% | powered |
| nemotron-3-ultra-nvfp4 | 5.05 | 0.93 | 2/40 | 82% | powered |
| nemotron-lightning-3p5-30b-a3b | 5.29 | 4.47 | 29/40 | 15% | **weak** |
| qwen3p7-plus | 4.52 | 1.87 | 6/38 | 59% | powered |

Two of eight models fail it. On **nemotron-lightning, temperature 0 leaves 4.47 of
5.29 bits in place — 29 of 40 completions distinct at temperature 0.** That is
not a low-entropy model; it is the highest open-arm entropy in the grid. Something
other than the sampler is generating most of its variation, no truncation
parameter can remove it, and every null on that model is therefore
uninterpretable. It is also one of the three frozen model levels, and its greedy
cells will carry within-cell variance the design would otherwise read as
sampling. The earlier 8-sample one-word probe recorded this model as
deterministic at temperature 0. It was wrong; a one-word prompt has no entropy
to reveal the problem with.

Values below are the fraction of open-arm entropy the tight setting removed.
**Bold** is distinguishable (Holm p < 0.05). `∅` is *no effect seen* — the control
passed, the probe had power, and it saw nothing; this is the only outcome that is
evidence of an ignored parameter. `?` is *underpowered* — the control failed, so
the null is evidence of nothing.

| model | control | top_p | top_k | min_p |
|---|--:|--:|--:|--:|
| deepseek-v4-flash-0731 | 50% | **48%** | **59%** | **50%** |
| gpt-oss-120b | 100% | **100%** | **100%** | **100%** |
| kimi-k2p6 | 32% | 14% ? | 16% ? | **24%** |
| minimax-m3 | 100% | **100%** | **100%** | **100%** |
| muse-glimmer-30b | 80% | **86%** | **79%** | **70%** |
| nemotron-3-ultra-nvfp4 | 82% | **77%** | **77%** | **77%** |
| nemotron-lightning-3p5-30b-a3b | 15% | 2% ? | **10%** | **4%** |
| qwen3p7-plus | 59% | 6% ∅ | **43%** | 24% ∅ |

**And a negative control, so the false-positive rate is measured rather than
assumed.** Two arms at *identical* settings (temperature 1.0, nothing else — the
highest-entropy condition, where a spurious difference has the most room to
appear), collected sequentially exactly as the real test collects its arms, four
pairs per model. Under the null they are exchangeable, so the rejection rate at
0.05 should be 5%. Measured live on 2026-09-11: **0 of 25 informative pairs
rejected at 0.05, 1 of 25 at 0.10**, dH mean −0.08 (sd 0.29), p-values mean 0.60.
The 95% upper bound on the rate is 13%, so the inflation that would matter is
excluded; a rate of exactly 5% is consistent but 25 pairs cannot pin it, and
would need several hundred to. Three further pairs from nemotron-lightning were
excluded as degenerate — both arms all-unique, dH identically zero, p = 1 by
construction — because a pair that cannot reject says nothing about the rate and
counting it would flatter the calibration.

This matters because the simulated calibration in the tests uses iid categorical
draws and cannot see the failure the live control is for: a provider whose state
drifts between the first arm and the second, which would make identical settings
non-exchangeable and inflate every positive in the grid. It did not.

Read that way, the grid contains exactly two cells that look like an ignored
parameter: `top_p` and `min_p` on `qwen3p7-plus`, where temperature removed 59%
of the entropy and `top_p` removed 6%. Every other null sits on a model whose
positive control failed and says nothing either way.

**Every cell the old heuristic called IGNORED and that could be retested came
back distinguishable**: `min_p` on nemotron-lightning, deepseek-v4-flash,
muse-glimmer-30b and kimi-k2p6, and `top_p` on muse-glimmer-30b (86% of entropy
removed, p < 0.003). Two design decisions in this project rested on those
verdicts — dropping the `minp` cell and excluding muse-glimmer-30b — and both were
made on bad evidence. The exclusion is reversed; restoring the cell is a cost
decision and is left open.

**Three models have now vanished from the catalogue since this project began**
— gpt-oss-20b on 2026-08-27, minimax-m2p7 by 2026-09-09, and qwen3p7-plus on
2026-09-11, two days after it was probed successfully. The last one carried the
only two "no effect seen" cells in the grid, and they can no longer be
rechecked. Three of ten probed models in fifteen days.

Two collection failures worth recording, because both produce a confident-looking
zero. `minimax-m2p7` is **404 — the second model withdrawn** mid-project.
`qwen3p7-plus` returns HTTP 200 with **empty content** whenever `max_tokens` cuts
it off before it stops reasoning (739 reasoning tokens on this prompt), so at
`max_tokens=256` every call succeeded, was billed, and yielded nothing; a
collector that drops empty strings reports that as `n=0, insufficient` with no
sign that anything was wrong.

## 2. Determinism at temperature 0, measured properly

The positive-control table above is also the determinism measurement, and it
supersedes the earlier one: 40 completions of a one-sentence prompt at
temperature 0, per model. Three of eight are deterministic (one distinct
completion in 40), two nearly so, and three are not — deepseek-v4-flash at 12/40,
kimi-k2p6 at 13/34, and nemotron-lightning at 29/40.

The earlier figure of "five of ten non-deterministic" came from eight samples of
a one-word prompt. It disagreed with this measurement on three models in both
directions, because a prompt with no entropy cannot reveal non-determinism and
eight samples cannot bound it. Greedy decoding is not reproducible even in
principle on the three models at the bottom of that table, which bears on every
paper that reports a single greedy number as a fixed property of the model.

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
| §1, §2 | `scripts/probe_distinguishability.py`, `samplerconfound/distinguish.py` | `runs/distinguish.json`, `runs/negative_control.json` |
| §3 | — | `MODEL_CANDIDATES` in `samplerconfound/config.py` |
| §4 | `scripts/verify_grader.py`, `scripts/sample_for_grader_check.py` | `runs/grader_check/` |
| §5 | `tests/test_variance.py`, `tests/test_inversion.py` | simulation |
| §6 | `scripts/power_check.py` | simulation |

The study these came from is described in [README.md](README.md); the dated
decision log is [TASKS.md](TASKS.md).

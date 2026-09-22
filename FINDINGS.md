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

**Then a prompt set, because one prompt is one sample of prompt space.** Four
prompts with ≥1.9 bits of open-arm entropy on every model surveyed, each
(model, parameter) tested on all four, reported per prompt, and the verdict
taken as a majority over prompts whose own positive control passed. Three
candidate prompts were rejected by the survey, each a demonstration of why: "state
a fact about numbers" gives 0.57 bits on nemotron-lightning, "pick an integer
1–1000" gives 0.00 on gpt-oss-120b, "name a city" gives 0.29 on deepseek. Each
looked fine on one model and would have silently carried a verdict on another.

Positive control per prompt (fraction of open-arm entropy that temperature 0 → 1.5
removes; ✗ = control failed, prompt drops out of that model's verdicts):

| model | word_prob | sentence | opener | question |
|---|--:|--:|--:|--:|
| deepseek-v4-flash-0731 | 13% ✗ | 72% | 28% ✗ | 16% ✗ |
| gpt-oss-120b | 100% | 100% | 100% | 100% |
| kimi-k2p6 | -84% ✗ | 21% ✗ | -129% ✗ | -22% ✗ |
| minimax-m3 | -20% ✗ | 100% | 42% ✗ | 92% |
| muse-glimmer-30b | 57% | 84% | 100% | 81% |
| nemotron-3-ultra-nvfp4 | 85% | 79% | 66% | 82% |
| nemotron-lightning-3p5-30b-a3b | 3% ✗ | 13% ✗ | 2% ✗ | 9% ✗ |

**Negative fractions are real and they mean something specific.** On kimi-k2p6
the open arm has *less* entropy than the tight arm on three of four prompts —
temperature 1.5 removes −84%, −129%, −22%. That is not a bug in the statistic: at
temperature 1.5 kimi's reasoning trace does not terminate within any token budget
(median completion tokens equal the cap at 1024, 2048 and 4096 alike), the model
emits empty content, and on the `opener` prompt **33 of 40** open-arm completions
are the empty token — a point mass. Entropy collapses toward zero because the
setting produced one outcome forty times over.

The first collector dropped empties, which censored the open arm by exactly the
variable under test and produced arms of 40 versus 13. Keeping them as an outcome
token is correct — the empty return *is* what the setting produces — but it
exposes that on these models the temperature contrast is not measuring sampler
narrowing at all. It is measuring whether temperature 1.5 breaks the reasoning
loop. The positive-control gate does the right thing with that: those cells go
to *underpowered*, evidence of nothing, rather than to *no effect*.

Verdicts per prompt and aggregate:

| model | param | word_prob | sentence | opener | question | powered | verdict |
|---|---|--:|--:|--:|--:|--:|---|
| nemotron-lightning-3p5-30b-a3b | top_p | ? | ? | ? | ? | 0/4 | underpowered |
| nemotron-lightning-3p5-30b-a3b | top_k | ? | **yes** | ? | ? | 1/4 | underpowered |
| nemotron-lightning-3p5-30b-a3b | min_p | ? | ? | ? | ? | 0/4 | underpowered |
| gpt-oss-120b | top_p | **yes** | **yes** | **yes** | **yes** | 4/4 | distinguishable |
| gpt-oss-120b | top_k | **yes** | **yes** | **yes** | **yes** | 4/4 | distinguishable |
| gpt-oss-120b | min_p | **yes** | **yes** | **yes** | **yes** | 4/4 | distinguishable |
| deepseek-v4-flash-0731 | top_p | **yes** | **yes** | **yes** | **yes** | 4/4 | distinguishable |
| deepseek-v4-flash-0731 | top_k | **yes** | **yes** | **yes** | ? | 3/4 | distinguishable |
| deepseek-v4-flash-0731 | min_p | **yes** | **yes** | **yes** | **yes** | 4/4 | distinguishable |
| minimax-m3 | top_p | **yes** | **yes** | **yes** | **yes** | 4/4 | distinguishable |
| minimax-m3 | top_k | **yes** | **yes** | **yes** | **yes** | 4/4 | distinguishable |
| minimax-m3 | min_p | **yes** | **yes** | **yes** | **yes** | 4/4 | distinguishable |
| muse-glimmer-30b | top_p | **yes** | **yes** | **yes** | **yes** | 4/4 | distinguishable |
| muse-glimmer-30b | top_k | **yes** | **yes** | **yes** | **yes** | 4/4 | distinguishable |
| muse-glimmer-30b | min_p | **yes** | **yes** | **yes** | **yes** | 4/4 | distinguishable |
| nemotron-3-ultra-nvfp4 | top_p | **yes** | **yes** | **yes** | **yes** | 4/4 | distinguishable |
| nemotron-3-ultra-nvfp4 | top_k | **yes** | **yes** | **yes** | **yes** | 4/4 | distinguishable |
| nemotron-3-ultra-nvfp4 | min_p | **yes** | **yes** | **NO** | **yes** | 4/4 | distinguishable |
| kimi-k2p6 | top_p | ? | **yes** | ? | ? | 1/4 | underpowered |
| kimi-k2p6 | top_k | ? | ? | ? | ? | 0/4 | underpowered |
| kimi-k2p6 | min_p | ? | ? | ? | **yes** | 1/4 | underpowered |

Five of seven models are cleanly distinguishable on every parameter across every
powered prompt. The other two — nemotron-lightning and kimi-k2p6 — fail the
positive control on nearly every prompt and are reported as underpowered
throughout, which is what they are: on nemotron the residual entropy at
temperature 0 is 4.5 bits, and on kimi the open arm collapses to empties. Neither
result says the parameters are ignored there. The single "NO" cell —
nemotron-3-ultra `min_p` on `opener` — is one prompt of four and is outvoted, as
the design intends.

Read that way, the grid contains exactly two cells that look like an ignored
parameter:**Every cell the old heuristic called IGNORED and that could be retested came
back distinguishable**: `min_p` on nemotron-lightning, deepseek-v4-flash,
muse-glimmer-30b and kimi-k2p6, and `top_p` on muse-glimmer-30b (86% of entropy
removed, p < 0.003). Two design decisions in this project rested on those
verdicts — dropping the `minp` cell and excluding muse-glimmer-30b — and both were
made on bad evidence. The exclusion is reversed; restoring the cell is a cost
decision and is left open.

### The same test on the paid tier

The seven models above were the ones the budget band admitted — cheap ones, by
construction. A free or entry tier serves whichever models a provider chose to
give away, which is a biased sample, so on 2026-09-20 the identical probe (four
prompts, 40 per arm, the same contrasts and controls) ran on every other priced
chat model Fireworks serves: eight models from $0.50 to $15.00 per million
output tokens, 1,390 calls each, $7.35 in total measured from the cached usage
(`scripts/probe_spend.py`). Three served models were left out with reasons:
`qwen3p8-2p4t-a95b` and `inkling` have no published price and cannot be
budgeted; `deepseek-v4-flash-vision-exp` is an experimental variant of a family
already covered. `deepseek-v4-pro` (unversioned) is now 404; its dated
successor `-0813` ran instead.

| model | $/1M out | control removed (word / sentence / opener / question) | top_p | top_k | min_p |
|---|--:|---|---|---|---|
| glm-5p3-flash | 0.50 | 73% / 97% / 78% / 87% | 4/4 | 4/4 | 4/4 |
| deepseek-v4p1-flash | 1.20 | 91% / 54% / 60% / 38%! | 4/4 | 4/4 | 4/4 |
| deepseek-v4-pro-0813 | 3.96 | 38%! / 78% / 56% / 41%! | 4/4 | 4/4 | 4/4 |
| kimi-k2p7-code | 4.00 | 4%! / 70% / −40%! / −16%! | 4/4 | 3/3 | 3/3 |
| glm-5p3 | 4.40 | 73% / 89% / 100% / 82% | 4/4 | 4/4 | 4/4 |
| glm-5p2 | 4.40 | 34%! / 50%! / 45%! / 38%! | 4/4 | 4/4 | 4/4 |
| qwen3p8-max | 6.00 | 57% / 62% / 100% / 82% | 4/4 | 4/4 | 3/4 |
| kimi-k3 | 15.00 | 82% / 62% / 97% / 65% | 4/4 | 4/4 | 4/4 |

`!` marks a prompt where the positive control failed; the parameter columns are
prompts distinguishable / prompts with a verdict. Every parameter is
distinguishable on every paid model on every prompt where the test had a
verdict, with one exception (`qwen3p8-max` `min_p` on one prompt of four,
outvoted). `glm-5p2` fails the control on all four prompts and still shows
every parameter, because a detected effect needs no control to be believed —
the control exists to interpret nulls, and there are none.

Two things this extension establishes. First, **the price axis does not
separate anything**: from $0.20 to $15.00 per million, a 75× range, the
truncation parameters are honoured wherever the probe has power, and the one
model that reproduces greedy decoding (§2) is a $0.60 one. A reviewer asking
"did you only test the models they give away" has the answer in the table.
Second, and the reason the matrix has a provider column at all: **on a single
provider, parameter-honouring is uniform and determinism is not**. Whether
`top_k` reaches the sampler is a property of the serving stack, and fifteen
models behind one stack agree. The dimension that can differ is the provider,
which is why the remaining probes wait on keys rather than on models.

What the paid tier does add is more of the kimi pattern. `kimi-k2p7-code`
fails the control on three prompts of four the same way `kimi-k2p6` did —
temperature 1.5 lowers measured entropy on two prompts, because the open arm
runs to the token cap and returns 93 empties across the grid — and is the
model on which every truncation parameter still shows through. That is not a
parameter finding; it is a finding about what "temperature 1.5" means on a
reasoning model at a fixed budget.

**Thin rows, under a stated priority.** The owner's rule for a short balance
is breadth of models first, breadth of providers second, N per cell third: a
thin result across forty models beats a thick one across eight. So the three
served models with no published price — `qwen3p8-2p4t-a95b`, `inkling`,
`deepseek-v4-flash-vision-exp` — ran at n=10 per arm rather than 40, with
their spend bounded at the provider's top listed price ($15/M; 442k tokens,
so at most $5.93 and about $0.48 if they are priced like their siblings). All
three: `top_k` distinguishable on every powered prompt, non-deterministic at
temperature 0 (72%, 52%, 50%), no seed makes any of them reproducible on more
than one prompt. Thin is visibly thinner —
fewer prompts pass the control at n=10, so `top_p` and `min_p` come back
underpowered or mixed on two of them — and the coverage table carries the n
so a thin row cannot pass as a thick one. That brings the provider to
**eighteen served chat models, all probed.**

### Every parameter the API accepts

Fireworks documents nine sampling parameters. The test above covered four. On
2026-09-21/22 the other five — `typical_p`, `mirostat`, `repetition_penalty`,
`frequency_penalty`, `presence_penalty` — ran on all eighteen models, at n=40
on the fourteen the cap admitted and n=10 on the rest, with both controls per
model as before. Two changes to the test were needed, and each is a lesson
about what "parameter honoured" means.

*A penalty has no direction.* The primary statistic for a truncation
parameter is the one-sided entropy drop, because a tight arm that is honoured
has less entropy. A penalty reshapes the distribution in no fixed direction:
on kimi-k2p6 `frequency_penalty=2.0` *emptied* the open arm (dH = −3.3,
one-sided p = 1.0) while the two-sided total-variation test saw it at
p = 0.0002. Penalty contrasts use TV as their primary.

*A penalty needs something to act on.* A penalty acts on tokens that have
already appeared, and a one-sentence reply has few. A null on the free-text
prompts therefore says the penalty had nothing to do, not that it was ignored
— and a passed temperature control does not help, because it certifies that
the probe can see a shift, not that a shift was possible. So the three
penalties were also run on a prompt that *forces* repetition ("Repeat the word
'yes' twenty times"). With the penalty off the reply is one constant string;
an applied `frequency_penalty` at its maximum cannot produce it. That cell is
its own control, and the table below reports **applied** (forced prompt) and
**matters on free text** separately, because they disagree, and the
disagreement is the finding.

| model | n | typical_p | mirostat | rep (applied / free text) | freq (applied / free text) | pres (applied / free text) |
|---|--:|---|---|---|---|---|
| nemotron-lightning-3p5-30b-a3b | 40 | ? | ? | yes / ? | yes / yes | NO / ? |
| glm-5p3-flash | 40 | yes | mix | yes / yes | yes / NO | n/a / NO |
| gpt-oss-120b | 40 | yes | yes | yes / yes | yes / NO | n/a / NO |
| deepseek-v4-flash-0731 | 40 | mix | ? | yes / yes | yes / yes | n/a / ? |
| minimax-m3 | 40 | yes | yes | yes / yes | yes / mix | n/a / ? |
| deepseek-v4p1-flash | 40 | yes | **NO** | yes / yes | yes / mix | n/a / NO |
| muse-glimmer-30b | 40 | **NO** | **NO** | **NO** / yes | yes / mix | n/a / NO |
| nemotron-3-ultra-nvfp4 | 40 | yes | **NO** | yes / yes | yes / NO | NO / NO |
| deepseek-v4-pro-0813 | 40 | mix | **NO** | yes / yes | yes / NO | n/a / NO |
| kimi-k2p6 | 10–40 | ? | ? | yes / ? | yes / yes | n/a / ? |
| kimi-k2p7-code | 40 | ? | yes | yes / yes | yes / ? | n/a / ? |
| glm-5p3 | 40 | yes | yes | yes / yes | yes / NO | n/a / NO |
| glm-5p2 | 40 | yes | ? | yes / yes | yes / yes | n/a / ? |
| qwen3p8-max | 40 | mix | **NO** | n/a / yes | yes / NO | n/a / NO |
| kimi-k3 | 40 | yes | yes | yes / yes | yes / NO | n/a / NO |
| inkling | 10 | ? | ? | n/a / yes | yes / ? | n/a / ? |
| qwen3p8-2p4t-a95b | 10 | mix | **NO** | n/a / yes | yes / NO | n/a / NO |
| deepseek-v4-flash-vision-exp | 10 | ? | ? | yes / ? | yes / yes | n/a / ? |

yes = distinguishable; NO = no effect with a passed control; ? = control failed
or every prompt degenerate; mix = prompts disagree; n/a = both arms constant
(the forced token survived the penalty), no verdict.

What the five add to the four:

**`frequency_penalty` is applied on 18 of 18 models and matters on a sentence
on 5.** Every model breaks forced repetition under it; on eight, the same
parameter at its maximum leaves the distribution of a one-sentence reply
indistinguishable from the control. Both are true. A harness that reports
"frequency_penalty=2.0" is reporting a setting that is real and, on most of
these models, inert for short generation.

**`mirostat` is accepted everywhere and inert on six models.** This is the
first parameter on this provider whose honouring differs *between models
behind one stack*: applied on gpt-oss-120b, minimax-m3, kimi-k2p7-code,
glm-5p3 and kimi-k3; accepted and doing nothing on deepseek-v4p1-flash,
muse-glimmer-30b, nemotron-3-ultra, deepseek-v4-pro-0813, qwen3p8-max and
qwen3p8-2p4t. The earlier claim that honouring is uniform on a single serving
stack was true of the parameters tested then and is false of this one.

**`typical_p` is honoured on most models and ignored on muse-glimmer-30b**,
which also ignores `mirostat` and `repetition_penalty` — three accepted,
inert parameters on one model, alongside the `top_p` it was wrongly accused of
ignoring in §1.

**`presence_penalty` is undetermined by this design.** It is an additive
penalty bounded at 2.0 logits; a forced "yes" survives it on 16 of 18 models,
so the forced prompt cannot separate "not applied" from "applied but too small
to overturn a dominant token". On free text it is "no effect" wherever the
probe had power. A decisive control for a bounded additive penalty needs a
prompt where the forced token wins by less than two logits, which this run
did not have. The column is reported as what it is.

**`repetition_penalty=2.0` — the documented maximum — makes Fireworks hang.**
During collection, requests at this value were accepted and then stalled
mid-generation: streaming showed 300 chunks in 7.6s and then nothing until the
connection dropped at 742s; one non-streaming call sat 55 minutes before a
ConnectionError. Every affected call eventually completed on retry at lower
concurrency, so no cell carries the status, but the probe now records a
request the provider does not finish as its own outcome (`transport`, shown
`err`) rather than retrying it for an hour, and the value is noted here as
accepted and unreliable.

Two rules were added to the test itself. A cell where both arms are all-unique
— the usual case for a penalty at temperature 1.0 on free text — has p = 1 by
construction and is reported as *insufficient*, not "no effect"; the negative
control already excluded such pairs, and the main test now does too. And every
cell on disk was re-derived under the current test with `--reassess`, which
touches no API: the completions are the data, the verdicts are derived.

**The negative control, per model.** The false-positive calibration was
originally 0/25 pairs across eight models. It now exists for every model with
a usable pair: one pair of identical arms at n=40 per model, 12 informative
pairs (six models are degenerate at temperature 1.0 — both arms all-unique —
and are excluded, as before). Pooled: **dH 0/37 false positives, TV 1/37**.
The primary statistic has yet to reject a true null on real output.

Coverage in one table: `scripts/probe_matrix.py`, which reads every probe
output under `runs/matrix/<provider>/` and prints one row per (provider,
model) with the price and the n beside the verdicts.
`scripts/run_probe_matrix.py` is the runner that produced it: it reads each
keyed provider's live catalogue, runs breadth-first passes (determinism on
everything, then thin distinguishability on everything, then the negative
control, then full N), and admits each job against spend measured from the
cache under a dollar cap.

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

### What an ignored parameter does downstream, on the smoke corpus

The paper's mechanism, made concrete on real generations. The design's
`minp` cell is {temperature 1.0, min_p 0.05}; its `hightemp` cell is
{temperature 1.0, top_p 1.0}. On a backend that applies `min_p` these are two
conditions. On a backend that accepts it and ignores it they are *the same
condition run twice*: the minp request returns a fresh draw from the hightemp
distribution. So on a grid where some models sit behind the first kind of
backend and some behind the second, the minp − hightemp contrast is real for
some models and zero for others, and the two-way decomposition books the
difference as a model × sampler interaction.

Rather than simulate that, `scripts/demo_minp_confound.py` builds it from
the smoke run's three surviving models and ten MATH-500 problems, through the
sweep's own `generate()`: a real `minp` cell (Fireworks applies `min_p` on
every model probed), and five more `hightemp` replicates, which are — by the
definition of "ignored" — exactly what an ignoring backend returns for the
minp request. 300 generations, collected 2026-09-22. Then the study's own
analysis on every combination of which backend served which model:

| grid | model × sampler share | inversion rate | minp − hightemp: deepseek / gpt-oss / minimax |
|---|--:|--:|---|
| A — every backend applies min_p (the honest grid) | **7.9%** | 31.1% | +0.02 / −0.04 / +0.06 |
| B — deepseek's backend ignores it | 5.3% | 31.1% | 0.00 / −0.04 / +0.06 |
| B — gpt-oss's backend ignores it | 3.3% | 20.0% | +0.02 / +0.02 / +0.06 |
| B — minimax's backend ignores it | 5.6% | 20.0% | +0.02 / −0.04 / +0.02 |
| C — every backend ignores it | 1.0% | 20.0% | 0.00 / +0.02 / +0.02 |
| D — only deepseek's applies it | 3.4% | 20.0% | +0.02 / +0.02 / +0.02 |
| D — only gpt-oss's applies it | 2.4% | 20.0% | 0.00 / −0.04 / +0.02 |
| D — only minimax's applies it | 1.7% | 20.0% | 0.00 / +0.02 / +0.06 |

Two things to read off. First, the honest grid's interaction is mostly
`min_p` itself: its effect on accuracy is genuinely model-specific here
(+0.02, −0.04, +0.06), and removing it everywhere (C) takes the interaction
from 7.9% to 1.0%. Second, and the point: **which grid a paper is looking at
is a fact about the serving stack that leaves no trace in the accuracy
table.** One silent backend moves the interaction share to anywhere between
3.3% and 5.6% and the inversion rate between 20% and 31%, depending on which
model it serves; one honouring backend among ignoring ones manufactures an
interaction of 1.7–3.4% from a uniform 1.0%. Every row is a legitimate
analysis of a legitimately collected table. Only the probe in §1 says which
row you are in.

Smoke scale — ten problems, five replicates, three models — so the numbers
are the mechanism, not an estimate; the sampler/model ratio is zero on this
corpus because three models of very different accuracy leave nothing for the
sampler component after clamping. The direction and the arithmetic are what
the full sweep would inherit.

### Documented, accepted, honoured: three facts, kept apart

Everything above measures what the output distribution does. What the
provider *says* a parameter does is a separate fact, and the two are now
recorded separately: `samplerconfound/documented.py` is a dated transcription
of each provider's API reference — a quote per parameter, with the URL, and an
explicit `absent` where the page was read and does not mention it — and
`scripts/gap.py` puts it beside the measurements. Three columns per
(provider, model, parameter): **documented** (the reference), **accepted**
(the HTTP status), **honoured** (the distribution). Every cell gets one label
for the gap between them.

On Fireworks (reference read 2026-09-22), 182 cells across eighteen models
and thirteen parameters:

| label | cells | parameters |
|---|--:|---|
| as documented | 88 | top_p, top_k, min_p, most of typical_p, repetition_penalty, frequency_penalty; ignore_eos on 3 |
| documented, accepted, undetermined | 47 | presence_penalty (16), mirostat (7), typical_p (9), … |
| **documented, accepted, ignored** | **27** | **seed (17)**, mirostat (6), typical_p (1), repetition_penalty (1), presence_penalty (2) |
| **undocumented, accepted, ignored** | **9** | **use_beam_search (4), skip_special_tokens (5)** |
| undocumented, accepted, works | 3 | ignore_eos |
| undocumented, accepted, undetermined | 7 | best_of (5, no signature observable from outside), one each of use_beam_search and ignore_eos |
| accepted, then the server fails | 1 | ignore_eos on kimi-k3 (HTTP 500, every time) |

**The largest documented gap is `seed`.** The Fireworks reference says, in
full, "Random seed for deterministic sampling." §2 measures it: a fixed seed
reproduces a non-deterministic prompt on 10 of 73 (model, prompt) cells and on
no model consistently. Seventeen of eighteen models carry the label
*documented, accepted, ignored* for it; the eighteenth is deterministic
without one. This is the sentence a harness author reads before writing
"seed=0" into a config and "reproducible" into a paper.

**The worst case exists, and its names are `use_beam_search` and
`skip_special_tokens`.** Fireworks validates request bodies strictly — fifteen
sampler names from other stacks (`top_a`, `tfs`, `dry_multiplier`, …) come
back `400 Extra inputs are not permitted` — but four names from vLLM's
`SamplingParams` pass validation and appear nowhere in the reference:
`best_of`, `use_beam_search`, `ignore_eos`, `skip_special_tokens`. A decisive
test for each (`scripts/probe_undocumented.py`; beam search must make
temperature-1.0 sampling deterministic, `skip_special_tokens=false` must expose
an end-of-turn marker, `ignore_eos` must push a one-word answer to the token
cap) on five models:

| parameter | documented | accepted | honoured |
|---|---|---|---|
| `use_beam_search` | absent | 5/5 | ignored on 4, undetermined on 1 |
| `skip_special_tokens` | absent | 5/5 | ignored on 5 |
| `best_of` | absent | 5/5 | no observable signature from outside |
| `ignore_eos` | absent | 5/5 | **honoured on 3**, undetermined on 1, HTTP 500 on 1 |

A user who sets `use_beam_search=true` on Fireworks gets HTTP 200, no warning,
and sampling. That is the cell the audit exists to find. And `ignore_eos` is
its mirror image, arguably worse for a benchmark: an undocumented knob that
*works* — the model is pushed past its end-of-turn token and keeps generating,
with the raw control token in the returned text
(`cat<|assistant|>We need answer…` on glm-5p3-flash). Nothing in the
reference says it exists.

Two smaller entries from the transcription itself. Groq's reference lists
`frequency_penalty` and `presence_penalty` with the sentence "This is not yet
supported by any of our models" — a parameter documented so the reader knows
it does nothing, which is the honest version of the mirostat row above and
gets its own label. And Mistral's reference documents `reasoning_effort`,
which the adapter had been withholding on the belief that it did not; the
transcription corrected the code. The six providers without keys are
transcribed and waiting; their gap tables fill in when `run_probe_matrix.py`
can reach them.

## 2. Greedy decoding is not reproducible, and the seed parameter does nothing

The cleanest version of the question: send the identical request at temperature
0 ten times and count byte-identical responses. Five prompts per model — the four
from the parameter test plus one real MATH-500 problem from the sweep split, so
this speaks to the greedy cell the study actually runs — and three conditions:
no seed, `seed=0`, `seed=1`. 1,050 calls, 11.5 minutes on the seven budget-band
models; 1,200 more on the eight paid models on 2026-09-20
(`scripts/probe_determinism.py`).

| model | greedy exact-match | reasoning trace | best with a seed | seed honoured? |
|---|--:|--:|--:|---|
| gpt-oss-120b | **100%** | 100% | 100% | n/a — deterministic without one |
| muse-glimmer-30b | 78% | 80% | 84% | no (1/3) |
| nemotron-3-ultra-nvfp4 | 64% | 66% | 72% | no (1/5) |
| deepseek-v4-flash-0731 | 58% | 50% | 62% | no (0/4) |
| minimax-m3 | 50% | 48% | 60% | no (0/4) |
| nemotron-lightning-3p5-30b-a3b | 38% | 38% | 42% | no (0/5) |
| kimi-k2p6 | **20%** | 10% | 32% | no (0/5) |
| *paid tier, 2026-09-20* | | | | |
| qwen3p8-max | 78% | 74% | 78% | no (0/3) |
| glm-5p3-flash | 76% | 92% | 90% | no (2/4) |
| glm-5p3 | 74% | 96% | 86% | no (1/4) |
| kimi-k3 | 74% | 78% | 84% | no (1/4) |
| deepseek-v4-pro-0813 | 64% | 64% | 66% | no (0/4) |
| deepseek-v4p1-flash | 60% | 44% | 74% | no (1/5) |
| kimi-k2p7-code | 50% | 42% | 50% | no (0/5) |
| glm-5p2 | 48% | 38% | 54% | no (0/5) |

**One model in eighteen is deterministic at temperature 0.** On the others, the
same request returns the same bytes between 20% and 78% of the time, and the
paid tier sits inside the same band as the cheap one: kimi-k3 at $15/M
reproduces 74%, glm-5p3-flash at $0.50/M 76%. On the real MATH-500 problem
specifically: gpt-oss-120b, deepseek-v4-flash, muse-glimmer, glm-5p3-flash,
deepseek-v4-pro-0813 and qwen3p8-max reproduce 10/10; kimi-k3 9/10;
deepseek-v4p1-flash 8/10; glm-5p3 6/10; nemotron-lightning 5/10, nemotron-3-ultra
5/10, minimax-m3 4/10; kimi-k2p7-code 3/10, glm-5p2 3/10, kimi-k2p6 2/10. A
paper reporting "kimi-k2p6 scores X on MATH-500, greedy" is reporting one draw
from a distribution.

**The seed parameter is accepted and does nothing.** The test is not whether
`seed=0` and `seed=1` differ — on a model whose seeded runs are only 40%
self-consistent they differ because everything differs. The test is whether ten
calls with the *same* seed agree. On the 60 (model, prompt) cells across fifteen
models that were not already deterministic without a seed, a fixed seed made the
run reproducible in **7**; the three thin models add 3 of 13, each again a single
prompt. Each is a single prompt on a model that was 60–90%
consistent anyway; no model reproduces on more than half its non-deterministic
prompts. The
handoff for this project asserted that Fireworks ignores `seed` on text; this is
the first time it was measured, and it holds.

Two consequences for the study. The replicate dimension was already named
"sampling variance at fixed configuration" rather than "seed" on the strength of
the unmeasured assertion; the measurement confirms that the name was the right
one. And the greedy condition — the one every harness claims to use, and the
paper's reference point — is not a fixed configuration on seventeen of eighteen
models here. Its within-cell variance is real variance, and the design's replicate term
absorbs it honestly, but a Limitations sentence has to say that "greedy" on this
provider means "a draw from a narrow distribution".

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
| §1 | `scripts/probe_distinguishability.py`, `samplerconfound/distinguish.py` | `runs/distinguish.json`, `runs/negative_control.json`, `runs/distinguish_multiprompt.json`, `runs/matrix/fireworks/<model>.json`, `<model>.dist-n{10,40}.json` |
| §2 | `scripts/probe_determinism.py` | `runs/determinism.json`, `runs/matrix/fireworks/determinism.json` |
| coverage | `scripts/probe_matrix.py`, `scripts/probe_spend.py` | `runs/matrix/matrix.json` |
| documented vs measured | `samplerconfound/documented.py`, `scripts/gap.py`, `scripts/probe_undocumented.py` | `runs/matrix/gap.json`, `runs/matrix/undocumented.json` |
| ignored parameter, downstream | `scripts/demo_minp_confound.py` | `runs/smoke/minp_confound.jsonl`, `runs/smoke/minp_confound.json` |
| §3 | — | `MODEL_CANDIDATES` in `samplerconfound/config.py` |
| §4 | `scripts/verify_grader.py`, `scripts/sample_for_grader_check.py` | `runs/grader_check/` |
| §5 | `tests/test_variance.py`, `tests/test_inversion.py` | simulation |
| §6 | `scripts/power_check.py` | simulation |

### When

Provider behaviour changes — three models were withdrawn during this project
and a fourth reappeared — so every result above carries the date the provider
answered, taken from the cache entry of each call (`stored_at`), not from when
a report was written. `scripts/date_probes.py --check` lists anything undated;
nothing is. The four runs that predate the cache are dated by the commit that
added them, which is an upper bound, and say so.

| finding | data | collected from (UTC) | to | source |
|---|---|---|---|---|
| §1 single-prompt probe | `runs/distinguish.json` | 2026-09-09T13:04 | 2026-09-09T13:04 | git author date of the adding commit (predates the cache) |
| §1 multi-prompt grid, seven models | `runs/distinguish_multiprompt.json` | 2026-09-17T19:56 | 2026-09-17T19:56 | git author date of the adding commit (predates the cache) |
| §1 negative control, eight models | `runs/negative_control.json` | 2026-09-11T10:41 | 2026-09-11T10:41 | git author date of the adding commit (predates the cache) |
| §2 determinism, seven models | `runs/determinism.json` | 2026-09-17T20:12 | 2026-09-17T20:12 | git author date of the adding commit (predates the cache) |
| §1 paid tier, n=40 (eight models) | `runs/matrix/fireworks/<model>.json` (8 files) | 2026-09-20T10:31 | 2026-09-20T10:54 | cache |
| §1 nine parameters, thin | `runs/matrix/fireworks/ (18 files)` | 2026-09-20T05:50 | 2026-09-21T15:59 | cache |
| §1 nine parameters, full | `runs/matrix/fireworks/ (14 files)` | 2026-09-20T05:50 | 2026-09-21T16:05 | cache |
| §1 negative control per model | `runs/matrix/fireworks/ (12 files)` | 2026-09-20T14:39 | 2026-09-20T14:48 | cache |
| §2 determinism, paid tier + thin | `runs/matrix/fireworks/ (4 files)` | 2026-09-20T10:31 | 2026-09-20T14:34 | cache |
| §1 ignored min_p, downstream | `runs/smoke/minp_confound.jsonl` | 2026-09-22T05:36 | 2026-09-22T05:43 | cache |
| §1 undocumented parameters | `runs/matrix/undocumented.json` | 2026-09-22T03:07 | 2026-09-22T04:59 | cache |

Documentation was read on 2026-09-22 (`samplerconfound/documented.py`).
Prices were read on 2026-09-20 (`samplerconfound/pricing.py`).

The study these came from is described in [README.md](README.md); the dated
decision log is [TASKS.md](TASKS.md).

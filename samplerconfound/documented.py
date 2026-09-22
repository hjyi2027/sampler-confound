"""What each provider's documentation claims about each sampling parameter.

This file is a transcription, not a measurement. Every entry quotes the
vendor's own reference page, with the URL and the date it was read, and says
nothing about whether the parameter works. The measurements live in the probe
outputs. `scripts/gap.py` puts the two side by side, because the gap between
what is documented and what is honoured is the finding, and a parameter that
is accepted by the API, absent from the docs, and ignored at runtime is the
worst case and is named.

An entry is one of:

    documented   the reference describes the parameter; `claim` is a quote
    absent       the reference was read and does not mention it
    None         the reference could not be read; nothing is claimed here

"absent" is a positive statement about a page read on a date, so it is
recorded, not defaulted. A provider whose reference is per-model (NVIDIA NIM)
is transcribed from the page for one model and says so.
"""

from __future__ import annotations

# The parameters the probe sends. Mirostat is two wire fields and one contrast.
PARAMS = ("temperature", "top_p", "top_k", "min_p", "typical_p", "mirostat",
          "repetition_penalty", "frequency_penalty", "presence_penalty", "seed")

# Names sent to find what a provider accepts WITHOUT documenting: vLLM's
# SamplingParams and a few other stacks' knobs. scripts/probe_undocumented.py
# tests the ones that come back 200. On Fireworks (2026-09-22) fifteen of
# these are refused with "Extra inputs are not permitted" and four are
# accepted: best_of, use_beam_search, ignore_eos, skip_special_tokens.
UNDOCUMENTED_CANDIDATES = ("top_a", "tfs", "tfs_z", "eta_cutoff", "epsilon_cutoff",
                           "no_repeat_ngram_size", "length_penalty", "dynatemp_range",
                           "smoothing_factor", "xtc_probability", "dry_multiplier",
                           "repeat_penalty", "repeat_last_n", "penalty_alpha", "top_n_sigma",
                           "best_of", "use_beam_search", "ignore_eos", "skip_special_tokens")

READ_ON = "2026-09-22"


def _doc(claim: str, rng: str = "") -> dict:
    return {"status": "documented", "claim": claim, "range": rng}


ABSENT = {"status": "absent", "claim": "", "range": ""}

DOCUMENTED: dict[str, dict] = {
    "fireworks": {
        "source": "https://docs.fireworks.ai/api-reference/post-chatcompletions",
        "read_on": READ_ON,
        "params": {
            "temperature": _doc("What sampling temperature to use, between 0 and 2.", "0–2"),
            "top_p": _doc("nucleus sampling, where the model considers the results of the tokens "
                          "with top_p probability mass", "0–1"),
            "top_k": _doc("Top-k sampling ... the k most probable next tokens are filtered and the "
                          "probability mass is redistributed among only those k", "0–100"),
            "min_p": _doc("Only tokens with probability >= min_p are considered for selection.", "0–1"),
            "typical_p": _doc("considers the most typical tokens whose cumulative probability is at "
                              "most typical_p", "0–1"),
            "mirostat": _doc("mirostat_target: Defines the target perplexity for the Mirostat algorithm. "
                             "mirostat_lr: the learning rate for the Mirostat sampling algorithm"),
            "repetition_penalty": _doc("A value of 1.0 means no penalty ... Values above 1.0 penalize "
                                       "repetition", "0–2"),
            "frequency_penalty": _doc("Positive values penalize new tokens based on their existing "
                                      "frequency in the text so far", "-2–2"),
            "presence_penalty": _doc("Positive values penalize new tokens based on whether they appear "
                                     "in the text so far", "-2–2"),
            "seed": _doc("Random seed for deterministic sampling."),
        },
    },
    "groq": {
        "source": "https://console.groq.com/docs/api-reference#chat-create",
        "read_on": READ_ON,
        "params": {
            "temperature": _doc("What sampling temperature to use, between 0 and 2.", "0–2"),
            "top_p": _doc("nucleus sampling ... We generally recommend altering this or temperature "
                          "but not both.", "0–1"),
            "top_k": ABSENT,
            "min_p": ABSENT,
            "typical_p": ABSENT,
            "mirostat": ABSENT,
            "repetition_penalty": ABSENT,
            # Documented, and documented as not working. A parameter can be in
            # the reference precisely so the reader knows it does nothing.
            "frequency_penalty": _doc("This is not yet supported by any of our models.", "-2–2"),
            "presence_penalty": _doc("This is not yet supported by any of our models.", "-2–2"),
            "seed": _doc("our system will make a best effort to sample deterministically ... "
                         "Determinism is not guaranteed"),
        },
    },
    "cerebras": {
        "source": "https://inference-docs.cerebras.ai/api-reference/chat-completions",
        "read_on": READ_ON,
        "params": {
            "temperature": _doc("Sampling temperature between 0 and 2.", "0–2"),
            "top_p": _doc("nucleus sampling ... alter this or temperature, but not both", "0–1"),
            "top_k": ABSENT,
            "min_p": ABSENT,
            "typical_p": ABSENT,
            "mirostat": ABSENT,
            "repetition_penalty": ABSENT,
            "frequency_penalty": _doc("Positive values reduce the likelihood of the model repeating "
                                      "tokens by applying a penalty proportional to frequency", "-2–2"),
            "presence_penalty": _doc("Positive values reduce the likelihood of the model repeating "
                                     "tokens that have already appeared", "-2–2"),
            "seed": _doc("the system will make a best effort to sample deterministically ... "
                         "Determinism is not guaranteed"),
        },
    },
    "google": {
        # generationConfig of the native generateContent API, which is what the
        # adapter speaks. Field names are camelCase there.
        "source": "https://ai.google.dev/api/generate-content#generationconfig",
        "read_on": READ_ON,
        "params": {
            "temperature": _doc("Controls the randomness of the output."),
            "top_p": _doc("topP: The maximum cumulative probability of tokens to consider when sampling."),
            "top_k": _doc("topK: The maximum number of tokens to consider when sampling. ... An empty "
                          "topK attribute [on the model] indicates that the model doesn't apply top-k "
                          "sampling and doesn't allow setting topK on requests."),
            "min_p": ABSENT,
            "typical_p": ABSENT,
            "mirostat": ABSENT,
            "repetition_penalty": ABSENT,
            "frequency_penalty": _doc("frequencyPenalty: a penalty that increases with each use. A "
                                      "positive penalty will discourage the use of tokens that have "
                                      "already been used"),
            "presence_penalty": _doc("presencePenalty: applied to the next token's logprobs if the "
                                     "token has already been seen in the response. This penalty is "
                                     "binary on/off"),
            "seed": _doc("Seed used in decoding. If not set, the request uses a randomly generated seed."),
        },
    },
    "openrouter": {
        "source": "https://openrouter.ai/docs/api-reference/parameters",
        "read_on": READ_ON,
        "params": {
            "temperature": _doc("This setting influences the variety in the model's responses.", "0–2"),
            "top_p": _doc("limits the model's choices to a percentage of likely tokens", "0–1"),
            "top_k": _doc("limits the model's choice of tokens at each step", "0+"),
            "min_p": _doc("the minimum probability for a token to be considered, relative to the "
                          "probability of the most likely token", "0–1"),
            "typical_p": ABSENT,
            "mirostat": ABSENT,
            "repetition_penalty": _doc("Helps to reduce the repetition of tokens from the input.", "0–2"),
            "frequency_penalty": _doc("control the repetition of tokens based on how often they appear "
                                      "in the input", "-2–2"),
            "presence_penalty": _doc("Adjusts how often the model repeats specific tokens already used "
                                     "in the input.", "-2–2"),
            "seed": _doc("the inferencing will sample deterministically ... Determinism is not "
                         "guaranteed for some models."),
        },
    },
    "nvidia": {
        # NIM's reference is per model. Transcribed from the page for one model
        # (muse-glimmer-30b); other models' pages may differ.
        "source": "https://docs.api.nvidia.com/nim/reference/ (per-model; read for muse-glimmer-30b)",
        "read_on": READ_ON,
        "params": {
            "temperature": _doc("The sampling temperature to use for text generation.", "0–1"),
            "top_p": _doc("The top-p sampling mass used for text generation.", "≤1"),
            "top_k": ABSENT,
            "min_p": ABSENT,
            "typical_p": ABSENT,
            "mirostat": ABSENT,
            "repetition_penalty": ABSENT,
            "frequency_penalty": _doc("Penalises new tokens by their existing frequency in the text so "
                                      "far, reducing verbatim repetition.", "-2–2"),
            "presence_penalty": _doc("Positive values penalise tokens that have already appeared", "-2–2"),
            "seed": _doc("our system will make a best effort to sample deterministically"),
        },
    },
    "mistral": {
        "source": "https://docs.mistral.ai/api/",
        "read_on": READ_ON,
        "params": {
            "temperature": _doc("What sampling temperature to use, we recommend between 0.0 and 0.7."),
            "top_p": _doc("Nucleus sampling ... The default value varies depending on the model"),
            "top_k": ABSENT,
            "min_p": ABSENT,
            "typical_p": ABSENT,
            "mirostat": ABSENT,
            "repetition_penalty": ABSENT,
            "frequency_penalty": _doc("penalizes the repetition of words based on their frequency in "
                                      "the generated text"),
            "presence_penalty": _doc("determines how much the model penalizes the repetition of words "
                                     "or phrases"),
            # Spelled random_seed on the wire; the adapter renames it.
            "seed": _doc("random_seed: The seed to use for random sampling. If set, different calls "
                         "will generate deterministic results."),
        },
    },
}


for _prov in DOCUMENTED.values():
    for _p in ("best_of", "use_beam_search", "ignore_eos", "skip_special_tokens"):
        _prov["params"].setdefault(_p, ABSENT)


def claim(provider: str, param: str) -> dict | None:
    """The documented entry, or None if the provider's reference was not read."""
    prov = DOCUMENTED.get(provider)
    if prov is None:
        return None
    return prov["params"].get(param)

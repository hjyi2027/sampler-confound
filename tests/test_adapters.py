"""One adapter per provider, one interface for the probes.

The property under test: a probe builds a canonical request and reads a
Completion, and NOTHING about which vendor is behind it leaks through in either
direction. Every adapter is exercised with a canned response in its own wire
shape; the Completions must agree.
"""

from __future__ import annotations

import json

import pytest

from samplerconfound import provider
from samplerconfound.adapters import ADAPTERS, CANONICAL_PARAMS, Completion, Google
from samplerconfound.cache import ResponseCache, request_key

REQ = {"model": "some-model",
       "messages": [{"role": "user", "content": "Name a colour."}],
       "max_tokens": 64, "temperature": 1.0, "top_p": 0.8, "seed": 7,
       "reasoning_effort": "low"}

OPENAI_SHAPED = {
    "id": "req-1", "created": 1700000000,
    "choices": [{"message": {"content": "blue", "reasoning_content": "hmm"},
                 "finish_reason": "length"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3,
              "completion_tokens_details": {"reasoning_tokens": 2}},
}
GOOGLE_SHAPED = {
    "responseId": "req-1",
    "candidates": [{"content": {"parts": [{"text": "hmm", "thought": True},
                                          {"text": "blue"}]},
                    "finishReason": "MAX_TOKENS"}],
    "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3,
                      "thoughtsTokenCount": 2},
}
CANNED = {name: (GOOGLE_SHAPED if name == "google" else OPENAI_SHAPED) for name in ADAPTERS}


# --------------------------------------------------------------------------
# the interface is the same on every adapter
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(ADAPTERS))
def test_every_adapter_reduces_its_own_wire_shape_to_the_same_completion(name):
    ad = ADAPTERS[name]
    wire, dropped = ad.encode(REQ)
    c = ad.decode(CANNED[name], dropped)
    assert isinstance(c, Completion)
    assert c.text == "blue"
    assert c.reasoning == "hmm"
    assert c.finish_reason == "length" and c.truncated
    assert (c.prompt_tokens, c.completion_tokens, c.reasoning_tokens) == (5, 3, 2)
    assert c.request_id == "req-1"
    assert c.served_by.startswith(name)
    assert c.raw is CANNED[name]


@pytest.mark.parametrize("name", sorted(ADAPTERS))
def test_sampler_params_reach_the_wire_or_are_reported_never_lost(name):
    """Silently dropping a parameter would make 'ignored' unmeasurable."""
    ad = ADAPTERS[name]
    wire, dropped = ad.encode(REQ)
    flat = json.dumps(ad.payload(wire))
    for k in ("temperature", "top_p", "seed", "reasoning_effort"):
        v = REQ[k]
        on_wire = (json.dumps(v) in flat) if not isinstance(v, str) else (v in flat)
        assert on_wire or k in dropped, f"{name} lost {k}"


@pytest.mark.parametrize("name", sorted(ADAPTERS))
def test_vendor_spellings_are_refused_at_the_boundary(name):
    for bad in ("random_seed", "topK", "max_output_tokens", "n"):
        with pytest.raises(ValueError, match="canonical"):
            ADAPTERS[name].encode({**REQ, bad: 1})


def test_canonical_vocabulary_covers_every_sampler_in_the_frozen_grid_and_every_contrast():
    from samplerconfound.config import FIXED, SAMPLER_CONFIGS
    used = {k for s in SAMPLER_CONFIGS for k in s if k != "id"}
    used |= {"max_tokens", "reasoning_effort"} & set(FIXED)
    assert used <= CANONICAL_PARAMS, used - CANONICAL_PARAMS
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.probe_distinguishability import CONTRASTS
    probed = {k for _, a, b, _ in CONTRASTS for k in {**a, **b}}
    assert probed <= CANONICAL_PARAMS, probed - CANONICAL_PARAMS


def test_google_can_say_the_penalties_it_documents():
    wire, dropped = ADAPTERS["google"].encode({**REQ, "frequency_penalty": 2.0, "presence_penalty": 1.0})
    g = wire["generationConfig"]
    assert g["frequencyPenalty"] == 2.0 and g["presencePenalty"] == 1.0 and dropped == ()


# --------------------------------------------------------------------------
# the vendor facts each adapter owns
# --------------------------------------------------------------------------

def test_fireworks_qualifies_the_model_and_is_idempotent():
    ad = ADAPTERS["fireworks"]
    wire, _ = ad.encode(REQ)
    assert wire["model"] == "accounts/fireworks/models/some-model"
    wire2, _ = ad.encode({**REQ, "model": wire["model"]})
    assert wire2["model"] == wire["model"]


def test_fireworks_wire_body_is_what_the_cache_hashed_before_adapters_existed():
    """Golden hash. Computed from the pre-adapter complete() on this exact body.

    The 152 stored Fireworks responses are keyed on this; if it moves, every
    one of them becomes a miss and is billed again on the next run.
    """
    body = {"model": "accounts/fireworks/models/gpt-oss-120b",
            "messages": [{"role": "user", "content": "Name a colour."}],
            "max_tokens": 64, "temperature": 1.0, "top_p": 0.8, "reasoning_effort": "low"}
    wire, dropped = ADAPTERS["fireworks"].encode(body)
    assert dropped == ()
    keyed = {**wire, "_provider": "fireworks"}
    assert request_key(keyed, 3) == \
        "332ce864dc120b8f2b93c4d7350d55e3ab63d90709d20b71c83cb8e23af7f1c6"


def test_mistral_spells_seed_random_seed_and_withholds_nothing():
    wire, dropped = ADAPTERS["mistral"].encode(REQ)
    assert wire["random_seed"] == 7 and "seed" not in wire
    assert dropped == ()
    assert wire["reasoning_effort"] == "low"      # documented by Mistral; sent
    # top_k is NOT pre-emptively dropped: whether Mistral takes it is the measurement
    wire, dropped = ADAPTERS["mistral"].encode({**REQ, "top_k": 40})
    assert wire["top_k"] == 40 and "top_k" not in dropped


def test_openrouter_reports_the_upstream_that_actually_served():
    c = ADAPTERS["openrouter"].decode({**OPENAI_SHAPED, "provider": "Fireworks"})
    assert c.served_by == "openrouter/Fireworks"
    assert ADAPTERS["openrouter"].decode(OPENAI_SHAPED).served_by == "openrouter"


def test_openai_shaped_adapters_read_either_reasoning_field():
    groq_style = {**OPENAI_SHAPED,
                  "choices": [{"message": {"content": "blue", "reasoning": "hmm"},
                               "finish_reason": "stop"}]}
    c = ADAPTERS["groq"].decode(groq_style)
    assert c.reasoning == "hmm" and c.finish_reason == "stop" and not c.truncated


class TestGoogle:
    ad: Google = ADAPTERS["google"]

    def test_model_goes_in_the_url_and_key_in_a_header(self):
        wire, _ = self.ad.encode(REQ)
        assert self.ad.url(wire).endswith("/models/some-model:generateContent")
        assert "model" not in self.ad.payload(wire)
        assert self.ad.headers("K") == {"x-goog-api-key": "K"}

    def test_sampler_lands_in_generation_config_with_top_k_available(self):
        wire, dropped = self.ad.encode({**REQ, "top_k": 40})
        g = wire["generationConfig"]
        assert g["temperature"] == 1.0 and g["topP"] == 0.8 and g["topK"] == 40
        assert g["seed"] == 7 and g["maxOutputTokens"] == 64
        assert g["thinkingConfig"] == {"thinkingLevel": "low"}
        assert dropped == ()

    def test_system_message_becomes_system_instruction(self):
        wire, _ = self.ad.encode({**REQ, "messages": [
            {"role": "system", "content": "Be terse."},
            {"role": "user", "content": "Hi"}]})
        assert wire["systemInstruction"] == {"parts": [{"text": "Be terse."}]}
        assert wire["contents"] == [{"role": "user", "parts": [{"text": "Hi"}]}]

    def test_thought_parts_are_reasoning_not_text(self):
        c = self.ad.decode(GOOGLE_SHAPED)
        assert c.text == "blue" and c.reasoning == "hmm"

    def test_empty_candidate_is_empty_text_not_a_crash(self):
        c = self.ad.decode({"candidates": [{"finishReason": "SAFETY"}]})
        assert c.text == "" and c.finish_reason == "safety"


# --------------------------------------------------------------------------
# complete(): encode -> cache -> http -> decode, with the http faked
# --------------------------------------------------------------------------

@pytest.fixture
def http(monkeypatch):
    calls = []

    def fake(url, headers, payload, timeout):
        calls.append({"url": url, "headers": headers, "payload": payload})
        return GOOGLE_SHAPED if "generativelanguage" in url else OPENAI_SHAPED

    monkeypatch.setattr(provider, "_http", fake)
    return calls


@pytest.mark.parametrize("name", sorted(ADAPTERS))
def test_complete_returns_a_completion_and_caches_the_raw_response(name, http, tmp_path):
    cache = ResponseCache(directory=tmp_path, offline=False)
    c, err = provider.complete("K", REQ, 0, provider=name, cache=cache)
    assert err is None and c.text == "blue"
    c2, _ = provider.complete("K", REQ, 0, provider=name, cache=cache)
    assert c2.text == "blue"
    assert len(http) == 1, "second identical call must be served from the cache"
    assert cache.stats.hits == 1


def test_complete_never_transmits_the_provider_tag_or_the_model_twice(http, tmp_path):
    cache = ResponseCache(directory=tmp_path, offline=False)
    provider.complete("K", REQ, 0, provider="fireworks", cache=cache)
    assert "_provider" not in http[0]["payload"]
    provider.complete("K", REQ, 0, provider="google", cache=cache)
    assert "_provider" not in http[1]["payload"] and "model" not in http[1]["payload"]
    assert "some-model" in http[1]["url"]


def test_same_model_name_on_two_providers_never_shares_a_cache_entry(http, tmp_path):
    cache = ResponseCache(directory=tmp_path, offline=False)
    provider.complete("K", REQ, 0, provider="groq", cache=cache)
    provider.complete("K", REQ, 0, provider="cerebras", cache=cache)
    assert len(http) == 2 and cache.count() == 2


def test_rejection_is_an_error_string_and_is_not_cached(monkeypatch, tmp_path):
    def refuse(url, headers, payload, timeout):
        raise provider.Rejected("rejected (422): Extra inputs are not permitted")
    monkeypatch.setattr(provider, "_http", refuse)
    cache = ResponseCache(directory=tmp_path, offline=False)
    c, err = provider.complete("K", REQ, 0, provider="mistral", cache=cache)
    assert c is None and err.startswith("rejected (422)")
    assert cache.count() == 0


def test_sweep_generate_builds_a_record_from_a_completion(monkeypatch):
    """Regression: after the cache migration generate() read `r.json()` with no
    `r` in scope — a NameError on the first successful response, which nothing
    exercised because every sweep test fakes above this layer."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts import run_sweep
    from samplerconfound.config import Design

    monkeypatch.setattr(run_sweep, "complete",
                        lambda *a, **k: (ADAPTERS["fireworks"].decode(OPENAI_SHAPED), None))

    class P:
        id, problem, answer = "p1", "1+1?", "2"

    design = Design(models=["accounts/fireworks/models/m"],
                    samplers=[{"id": "greedy", "temperature": 0.0}],
                    n_replicates=1, benchmark="math500", n_problems=1,
                    fixed={"prompt_template": "Solve.", "max_tokens": 8, "reasoning_effort": "low"})
    rec = run_sweep.generate("K", design, design.models[0], design.samplers[0], 0, P())
    assert rec["response"] == "blue" and rec["reasoning"] == "hmm"
    assert rec["finish_reason"] == "length" and rec["output_tokens"] == 3
    assert rec["served_by"] == "fireworks"
    assert rec["verdict"]["status"] in ("incorrect", "unparseable")

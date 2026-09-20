"""The response cache: identical (request, replicate) is never billed twice, and
re-analysis can never generate.
"""

from __future__ import annotations

import json

import pytest

from samplerconfound.cache import CacheMiss, ResponseCache, request_key


def _body(model="m", text="hi", **params):
    return {"model": model, "messages": [{"role": "user", "content": text}],
            "max_tokens": 64, "temperature": 0.0, **params}


def test_key_ignores_dict_order_but_not_content():
    a = {"model": "m", "temperature": 0.0, "max_tokens": 64}
    b = {"max_tokens": 64, "model": "m", "temperature": 0.0}
    assert request_key(a, 0) == request_key(b, 0)
    assert request_key(a, 0) != request_key({**a, "temperature": 0.1}, 0)
    assert request_key(a, 0) != request_key({**a, "model": "n"}, 0)


def test_replicate_index_is_part_of_the_key():
    """The load-bearing property.

    The probes send the identical request N times on purpose. A cache that keyed
    on the request alone would answer all N from one stored response and make
    every model look deterministic — destroying the measurement while looking
    like an optimisation.
    """
    b = _body()
    assert request_key(b, 0) != request_key(b, 1)
    keys = {request_key(b, i) for i in range(40)}
    assert len(keys) == 40


def test_second_fetch_does_not_call_send(tmp_path):
    cache = ResponseCache(directory=tmp_path, offline=False)
    calls = []

    def send(body):
        calls.append(body)
        return {"choices": [{"message": {"content": f"reply {len(calls)}"}}]}

    r1 = cache.fetch(_body(), 0, send)
    r2 = cache.fetch(_body(), 0, send)
    assert r1 == r2
    assert len(calls) == 1, "identical (request, replicate) must be billed once"
    assert cache.stats.hits == 1 and cache.stats.misses == 1


def test_different_replicates_each_call_send(tmp_path):
    cache = ResponseCache(directory=tmp_path, offline=False)
    n = []
    send = lambda body: (n.append(1), {"r": len(n)})[1]
    out = [cache.fetch(_body(), i, send) for i in range(5)]
    assert len(n) == 5
    assert len({json.dumps(o) for o in out}) == 5


def test_offline_miss_raises_rather_than_generating(tmp_path):
    cache = ResponseCache(directory=tmp_path, offline=True)
    with pytest.raises(CacheMiss, match="must not generate"):
        cache.fetch(_body(), 0, lambda body: {"should": "never run"})
    assert cache.stats.misses == 1


def test_offline_hit_is_served(tmp_path):
    online = ResponseCache(directory=tmp_path, offline=False)
    online.fetch(_body(), 3, lambda body: {"ok": True})
    offline = ResponseCache(directory=tmp_path, offline=True)
    assert offline.fetch(_body(), 3, lambda body: pytest.fail("called API offline")) == {"ok": True}


def test_failures_are_not_cached(tmp_path):
    # A 429 or a network drop must be retried next time, not remembered.
    cache = ResponseCache(directory=tmp_path, offline=False)
    attempts = []

    def flaky(body):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("429")
        return {"ok": True}

    with pytest.raises(RuntimeError):
        cache.fetch(_body(), 0, flaky)
    assert cache.fetch(_body(), 0, flaky) == {"ok": True}
    assert len(attempts) == 2
    assert cache.count() == 1


def test_entry_records_the_body_so_it_can_be_audited(tmp_path):
    cache = ResponseCache(directory=tmp_path, offline=False)
    cache.fetch(_body(model="mm", top_p=0.3), 7, lambda body: {"x": 1})
    files = list(tmp_path.rglob("*.json"))
    assert len(files) == 1
    entry = json.loads(files[0].read_text())
    assert entry["body"]["model"] == "mm"
    assert entry["body"]["top_p"] == 0.3
    assert entry["replicate"] == 7
    assert entry["response"] == {"x": 1}


def test_write_is_atomic_no_temp_left_behind(tmp_path):
    cache = ResponseCache(directory=tmp_path, offline=False)
    cache.fetch(_body(), 0, lambda body: {"x": 1})
    assert not list(tmp_path.rglob("*.tmp"))


def test_corrupt_entry_is_a_miss_not_a_crash(tmp_path):
    cache = ResponseCache(directory=tmp_path, offline=False)
    cache.fetch(_body(), 0, lambda body: {"x": 1})
    p = next(tmp_path.rglob("*.json"))
    p.write_text("{not json")
    assert cache.get(_body(), 0) is None


def test_provider_is_part_of_the_key():
    """The same model name on two providers must never share a cache entry.

    "llama-3.3-70b" exists on Groq and on Cerebras and they are different
    deployments. complete() folds the provider name into the keyed body; this
    pins that a body differing only in `_provider` hashes differently.
    """
    a = {"model": "llama-3.3-70b", "temperature": 0.0, "_provider": "groq"}
    b = {"model": "llama-3.3-70b", "temperature": 0.0, "_provider": "cerebras"}
    assert request_key(a, 0) != request_key(b, 0)


def test_every_provider_has_the_fields_the_transport_needs():
    from samplerconfound.provider import PROVIDERS
    for name, spec in PROVIDERS.items():
        assert spec["base"].startswith("https://"), name
        assert spec["env"].endswith("_API_KEY"), name
        assert spec["signup"].startswith("https://"), name
        assert "prefix" in spec, name

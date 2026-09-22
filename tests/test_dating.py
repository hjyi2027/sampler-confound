"""Every probe result is dated by the calls it was made of, not by when a
report was written — and never by a later call that happens to match."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from samplerconfound.cache import ResponseCache
from samplerconfound.paths import iso, window
from scripts import date_probes


def test_iso_and_window():
    assert iso(0) == "1970-01-01T00:00Z"
    assert iso(None) == ""
    assert window([5.0, None, 2.0]) == {"from": iso(2.0), "to": iso(5.0)}
    assert window([None]) == {"from": "", "to": ""}


def test_completion_carries_the_calls_timestamp(tmp_path, monkeypatch):
    from samplerconfound import provider
    from tests.test_adapters import OPENAI_SHAPED, REQ
    monkeypatch.setattr(provider, "_http", lambda *a, **k: OPENAI_SHAPED)
    cache = ResponseCache(directory=tmp_path, offline=False)
    c, _ = provider.complete("K", REQ, 0, provider="fireworks", cache=cache)
    assert c.collected_at is not None
    c2, _ = provider.complete("K", REQ, 0, provider="fireworks", cache=cache)
    assert c2.collected_at == c.collected_at, "a cache hit keeps the ORIGINAL call's date"


def test_a_cell_is_dated_only_by_cached_texts_that_are_its_own(tmp_path, monkeypatch):
    """The trap: the same request re-collected later has a cache entry with a
    later stored_at. A cell whose retained completions do not match those
    entries must not take their date."""
    cache = ResponseCache(directory=tmp_path, offline=False)
    monkeypatch.setattr(date_probes, "CACHE", cache)
    from samplerconfound.adapters import ADAPTERS
    body = {"model": "m", "messages": [{"role": "user", "content": "P"}], "max_tokens": 8,
            "temperature": 1.0, "reasoning_effort": "low"}
    wire, _ = ADAPTERS["fireworks"].encode(body)
    for i, text in enumerate(["Blue.", "Red."]):
        cache.put({**wire, "_provider": "fireworks"}, i,
                  {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]})
    w = date_probes.cell_window("fireworks", "m", "P", {"temperature": 1.0}, 2, 8, "low",
                                expect=["blue", "red"])
    assert w["from"], "matching texts: dated from the cache"
    w = date_probes.cell_window("fireworks", "m", "P", {"temperature": 1.0}, 2, 8, "low",
                                expect=["green", "red"])
    assert w == {"from": "", "to": ""}, "different data: not dated by someone else's calls"

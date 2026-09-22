"""Documented, accepted, honoured are three facts; the label is their gap."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from samplerconfound.adapters import ADAPTERS
from samplerconfound.documented import DOCUMENTED, PARAMS, claim
from scripts.gap import WORST, label


def test_every_provider_with_an_adapter_has_a_transcription_for_every_parameter():
    for prov in ADAPTERS:
        assert prov in DOCUMENTED, prov
        entry = DOCUMENTED[prov]
        assert entry["source"].startswith("https://") and entry["read_on"]
        for p in PARAMS:
            e = entry["params"][p]
            assert e["status"] in ("documented", "absent"), (prov, p)
            if e["status"] == "documented":
                assert e["claim"], (prov, p)          # a quote, not a checkbox
            else:
                assert e["claim"] == ""


def test_absent_is_recorded_not_defaulted():
    assert claim("groq", "top_k")["status"] == "absent"
    assert claim("nowhere", "top_k") is None


DOC = {"status": "documented", "claim": "does X"}
ABS = {"status": "absent", "claim": ""}
UNSUP = {"status": "documented", "claim": "This is not yet supported by any of our models."}


def test_labels_rank_the_gap():
    assert label(ABS, "yes", "no") == WORST
    assert label(DOC, "yes", "no") == "DOCUMENTED, ACCEPTED, IGNORED"
    assert label(UNSUP, "yes", "no") == "documented as unsupported, and is"
    assert label(DOC, "yes", "yes") == "as documented"
    assert label(ABS, "yes", "yes") == "undocumented, accepted, works"
    assert label(ABS, "refused", "refused") == "undocumented, refused (correct)"
    assert label(DOC, "refused", "refused") == "documented, refused"
    assert label(DOC, "yes", "undetermined").endswith("undetermined")
    assert label(None, "yes", "no") == "docs not read"
    assert label(DOC, "not sent", "not sent") == "adapter has no wire form"


def test_fireworks_documents_seed_as_deterministic():
    """The claim the determinism probe falsifies on 17 of 18 models."""
    assert "deterministic" in claim("fireworks", "seed")["claim"]

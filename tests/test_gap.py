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
    """The claim the determinism probe tests at temperature 0."""
    assert "deterministic" in claim("fireworks", "seed")["claim"]


def test_seed_at_t0_is_labelled_as_a_promise_not_as_ignored():
    """At T=0 there is no random draw for a seed to fix, so a non-reproducible
    seeded run says the documented determinism was not delivered, not that the
    sampler ignored the seed."""
    from scripts.gap import SEED_HONOURED
    assert label(DOC, "yes", SEED_HONOURED["NO"]) == "documented determinism not delivered at T=0"
    assert label(DOC, "yes", SEED_HONOURED["n/a"]) == "deterministic without it"
    assert SEED_HONOURED["NO"] not in ("yes", "no")


def test_penalty_evidence_from_either_prompt_counts():
    from scripts.gap import penalty_honoured
    # applied on free text though forced repetition survives: honoured
    assert penalty_honoured("repetition_penalty", "no effect seen", "distinguishable") == "yes"
    assert penalty_honoured("presence_penalty", "insufficient", "distinguishable") == "yes"
    assert penalty_honoured("frequency_penalty", "distinguishable", "no effect seen") == "yes"
    # a forced null is decisive only for the penalty that must break the string
    assert penalty_honoured("frequency_penalty", "no effect seen", "no effect seen") == "no"
    assert penalty_honoured("repetition_penalty", "no effect seen", "no effect seen") == "undetermined"
    assert penalty_honoured("presence_penalty", "no effect seen", "no effect seen") == "undetermined"
    assert penalty_honoured("presence_penalty", "insufficient", "mixed") == "mixed"
    assert penalty_honoured("frequency_penalty", "rejected", None) == "refused"


def test_a_null_is_inert_only_if_the_effect_bound_excludes_the_weakest_honoured_effect():
    from scripts.paper_assets import inert_by_effect_bound
    assert inert_by_effect_bound(0.10, 0.24)
    assert not inert_by_effect_bound(0.26, 0.24)
    assert not inert_by_effect_bound(0.24, 0.24)
    assert not inert_by_effect_bound(float("nan"), 0.24)
    assert not inert_by_effect_bound(0.10, float("nan"))

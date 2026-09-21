"""The spend meter and the coverage table read what the probes wrote."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import probe_matrix, probe_spend


def test_spend_reads_both_wire_shapes():
    openai = {"response": {"usage": {"prompt_tokens": 10, "completion_tokens": 20}}}
    google = {"response": {"usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5,
                                             "thoughtsTokenCount": 15}}}
    assert probe_spend.usage_of(openai) == (10, 20)
    assert probe_spend.usage_of(google) == (10, 20), "thought tokens bill as output"
    assert probe_spend.usage_of({"response": {}}) == (0, 0)


def test_determinism_seed_column_reduces_the_sentence_to_a_verdict():
    rows = probe_matrix.determinism_rows({"summary": {
        "a": {"greedy_match": 1.0, "seed": "n/a (deterministic without one)"},
        "b": {"greedy_match": 0.2, "seed": "NO (fixed seed reproduces on 0/5 non-deterministic prompts)"},
        "c": {"greedy_match": 0.4, "seed": "yes (fixed seed reproduces on 5/5 non-deterministic prompts)"},
        "d": {"greedy_match": 0.4, "seed": "rejected"},
    }})
    assert [rows[m]["seed"] for m in "abcd"] == ["n/a", "NO", "yes", "rej"]


def test_distinguish_rows_carry_verdicts_and_a_mean_control():
    d = {"aggregates": [{"model": "m", "parameter": "top_k", "n_prompts": 2, "verdict": "distinguishable"}],
         "positive_control_by_prompt": {
             "p1": {"m": {"fraction_removed": 0.8, "powered": True}},
             "p2": {"m": {"fraction_removed": 0.4, "powered": False}}}}
    r = probe_matrix.distinguish_rows("groq", d)["m"]
    assert r["top_k"] == "distinguishable" and r["provider"] == "groq"
    assert r["powered_prompts"] == 1 and abs(r["control_removed"] - 0.6) < 1e-9


def test_a_penalty_column_reports_applied_and_keeps_free_text_alongside():
    d = {"aggregates": [{"model": "m", "parameter": "frequency_penalty", "n_prompts": 4,
                         "verdict": "no effect seen", "verdict_forced": "distinguishable"}],
         "n_per_arm": 40}
    r = probe_matrix.distinguish_rows("fireworks", d)["m"]
    assert r["frequency_penalty"] == "distinguishable"
    assert r["frequency_penalty_free_text"] == "no effect seen"

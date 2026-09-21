"""The breadth-first runner's plan: what counts as done, what a job costs, what
is a chat model. No network; the run itself is subprocesses of the probes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import run_probe_matrix as rpm


def _dist_file(params, n=40):
    aggs = [{"model": "m", "parameter": p, "n_prompts": 4} for p in params]
    for a in aggs:
        if a["parameter"] in rpm.FORCED_FOR:
            a["verdict_forced"] = "distinguishable"   # a penalty counts once its forced prompt ran
    return json.dumps({"n_per_arm": n, "aggregates": aggs})


ALL = [c for c in rpm.CONTRAST_NAMES if c != "temperature"]


def test_a_thicker_run_satisfies_a_thinner_pass_only_for_contrasts_it_covers(tmp_path, monkeypatch):
    monkeypatch.setattr(rpm, "MATRIX", tmp_path)
    (tmp_path / "groq").mkdir()
    (tmp_path / "groq" / "m.dist-n40.json").write_text(_dist_file(ALL))
    assert rpm.already_done("groq", "m", "distinguish", 10)
    assert rpm.already_done("groq", "m", "distinguish", 40)
    assert not rpm.already_done("groq", "m", "distinguish", 80)
    assert not rpm.already_done("groq", "m", "determinism", 10)
    assert not rpm.already_done("groq", "m", "negative", 10)


def test_a_file_with_the_old_four_contrasts_leaves_the_new_five_to_run(tmp_path, monkeypatch):
    """Adding a contrast must not re-bill the ones already on disk, and must
    not be hidden by a file that exists but predates it."""
    monkeypatch.setattr(rpm, "MATRIX", tmp_path)
    (tmp_path / "groq").mkdir()
    (tmp_path / "groq" / "m.dist-n40.json").write_text(_dist_file(["top_p", "top_k", "min_p"]))
    assert not rpm.already_done("groq", "m", "distinguish", 40)
    assert rpm.missing_params("groq", "m", 40) == [
        "typical_p", "mirostat", "repetition_penalty", "frequency_penalty", "presence_penalty"]
    cmd = rpm.command("groq", "m", "distinguish", 40, 1, 4)
    assert "--params" in cmd and "typical_p" in cmd and "top_p" not in cmd


def test_legacy_paid_tier_files_count_as_n40(tmp_path, monkeypatch):
    monkeypatch.setattr(rpm, "MATRIX", tmp_path)
    (tmp_path / "fireworks").mkdir()
    (tmp_path / "fireworks" / "kimi-k3.json").write_text(_dist_file(ALL))
    (tmp_path / "fireworks" / "determinism.json").write_text(json.dumps({"summary": {"kimi-k3": {}}}))
    assert rpm.already_done("fireworks", "kimi-k3", "distinguish", 10)
    assert rpm.already_done("fireworks", "kimi-k3", "distinguish", 40)
    assert rpm.already_done("fireworks", "kimi-k3", "determinism", 10)


def test_unpriced_model_is_bounded_at_its_providers_top_price(monkeypatch):
    monkeypatch.setattr(rpm, "PRICES", {"cheap": (0.1, 0.5), "dear": (3.0, 15.0)})
    price_of, prov_max = rpm.make_price_of({"fireworks": ["cheap", "dear", "mystery"],
                                            "groq": ["llama"]})
    assert price_of("fireworks", "mystery") == (3.0, 15.0)
    assert price_of("fireworks", "cheap") == (0.1, 0.5)
    assert price_of("groq", "llama") == (0.0, 0.0) and prov_max["groq"] is None


def test_calls_per_job_match_the_probe_scripts():
    assert rpm.calls_for("determinism", 10, 1) == 3 * 10 * 5
    assert rpm.N_CONTRASTS == 9
    assert rpm.calls_for("distinguish", 40, 1) == 4 * 9 * 2 * 40
    assert rpm.calls_for("distinguish", 40, 1, n_contrasts=4) == 1280
    assert rpm.calls_for("distinguish", 10, 1, n_contrasts=4) == 320
    assert rpm.calls_for("negative", 10, 1) == 20


def test_name_filter_catches_what_the_listing_flag_misses():
    for bad in ("qwen3-embedding-8b", "qwen3-reranker-8b", "whisper-large", "llama-guard-4",
                "flux-1-dev", "tts-1"):
        assert rpm.NOT_CHAT.search(bad), bad
    for good in ("kimi-k3", "glm-5p3-flash", "gemini-2.5-pro", "mistral-small-latest",
                 "deepseek-v4-flash-vision-exp"):
        assert not rpm.NOT_CHAT.search(good), good


def test_a_penalty_without_its_forced_prompt_is_not_covered(tmp_path, monkeypatch):
    monkeypatch.setattr(rpm, "MATRIX", tmp_path)
    (tmp_path / "groq").mkdir()
    aggs = [{"model": "m", "parameter": p} for p in ALL]
    for a in aggs:
        if a["parameter"] in rpm.FORCED_FOR and a["parameter"] != "presence_penalty":
            a["verdict_forced"] = "distinguishable"
    (tmp_path / "groq" / "m.dist-n40.json").write_text(json.dumps({"n_per_arm": 40, "aggregates": aggs}))
    assert rpm.missing_params("groq", "m", 40) == ["presence_penalty"]


def test_budget_window_persists_across_invocations(tmp_path, monkeypatch):
    """Three relaunches must not be three caps."""
    monkeypatch.setattr(rpm, "MATRIX", tmp_path)
    monkeypatch.setattr(rpm, "BUDGET_FILE", tmp_path / ".budget.json")
    t0 = rpm.budget_window(5.0, fresh=True)
    t1 = rpm.budget_window(5.0, fresh=False)
    assert t1 == t0
    t2 = rpm.budget_window(5.0, fresh=True)
    assert t2 >= t0

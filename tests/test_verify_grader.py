"""The verification harness must survive its own round trip."""

import json
import subprocess
import sys
from pathlib import Path

from samplerconfound.grade import grade

ROOT = Path(__file__).resolve().parents[1]


def _fixture(tmp_path):
    cases = (
        [("Answer: 42", "42")] * 30
        + [("Answer: 41", "42")] * 10
        + [("no idea at all", "42")] * 8
        + [("we get 42 somewhere", "42")] * 6
        + [(r"Answer: \frac{1}{2}", "0.5")] * 6
        + [("Answer: 0.333333", "1/3")] * 4
    )
    p = tmp_path / "graded.jsonl"
    with p.open("w") as f:
        for i, (resp, gold) in enumerate(cases):
            f.write(
                json.dumps(
                    {
                        "problem_id": f"p{i}",
                        "gold": gold,
                        "response": resp,
                        "verdict": grade(resp, gold).to_dict(),
                    }
                )
                + "\n"
            )
    return p


def _run(*args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_grader.py"), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def test_sample_writes_a_worksheet_and_oversamples_rare_strata(tmp_path):
    graded = _fixture(tmp_path)
    out = tmp_path / "ws.md"
    r = _run("sample", str(graded), "--out", str(out), "--n", "30")
    assert r.returncode == 0, r.stderr
    text = out.read_text()
    assert "**Human:**" in text
    assert text.count("<!-- grader:") >= 20
    # 30 of 64 records are plain correct answers, but they must not dominate the
    # worksheet — that is the entire reason for stratifying.
    plain = text.count('"stratum": "correct_plain"')
    assert plain <= 10, f"plain-correct items took {plain} slots"


def test_score_reports_perfect_agreement_when_labels_match(tmp_path):
    graded = _fixture(tmp_path)
    out = tmp_path / "ws.md"
    _run("sample", str(graded), "--out", str(out), "--n", "20")

    text = out.read_text()
    lines = text.split("\n")
    filled, pending = [], None
    for line in lines:
        if line.startswith("**Human:**"):
            pending = len(filled)
            filled.append(line)
        else:
            filled.append(line)
    # Fill each Human line with whatever the grader said, by walking pairs.
    rebuilt, last_human_idx = [], None
    for line in lines:
        if line.startswith("**Human:**"):
            last_human_idx = len(rebuilt)
            rebuilt.append(line)
        elif line.startswith("<!-- grader:") and last_human_idx is not None:
            status = json.loads(line[len("<!-- grader: ") : -len(" -->")])["status"]
            rebuilt[last_human_idx] = f"**Human:** {status}"
            rebuilt.append(line)
            last_human_idx = None
        else:
            rebuilt.append(line)
    out.write_text("\n".join(rebuilt))

    r = _run("score", str(out))
    assert r.returncode == 0, r.stderr
    assert "100.0%" in r.stdout or "100%" in r.stdout
    assert "scored correct but is not:      0" in r.stdout


def test_score_flags_a_false_positive_as_disqualifying(tmp_path):
    ws = tmp_path / "ws.md"
    ws.write_text(
        "# x\n\n"
        "## 1. `p0`\n\n**Human:** incorrect\n\n"
        '<!-- grader: {"status": "correct", "method": "last_number", '
        '"match": "numeric_exact", "extracted": "42", "stratum": "last_number"} -->\n\n'
        "---\n\n"
        "## 2. `p1`\n\n**Human:** correct\n\n"
        '<!-- grader: {"status": "correct", "method": "answer_line", '
        '"match": "exact", "extracted": "7", "stratum": "correct_plain"} -->\n'
    )
    r = _run("score", str(ws))
    assert r.returncode == 0, r.stderr
    assert "scored correct but is not:      1" in r.stdout
    assert "disqualifying" in r.stdout


def test_score_refuses_an_unlabelled_worksheet(tmp_path):
    ws = tmp_path / "ws.md"
    ws.write_text(
        "## 1. `p0`\n\n**Human:** \n\n"
        '<!-- grader: {"status": "correct", "method": "answer_line", '
        '"match": "exact", "extracted": "1", "stratum": "correct_plain"} -->\n'
    )
    r = _run("score", str(ws))
    assert r.returncode == 2
    assert "no items labelled" in r.stderr

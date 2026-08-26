"""Grader tests, weighted toward the cases where leniency would fake a finding.

Roughly half of these assert that something does NOT match. That ratio is
deliberate: a grader's false positives inflate accuracy invisibly, and this study
is measuring differences between accuracies.
"""

import pytest

from samplerconfound.grade import (
    Verdict,
    extract_answer,
    grade,
    normalize,
    summarise,
)


# --- extraction ---------------------------------------------------------


def test_answer_line_wins_over_earlier_numbers():
    text = "We try 12, then 7, which is wrong.\nAnswer: 42"
    assert extract_answer(text) == ("42", "answer_line")


def test_last_answer_line_wins_when_the_model_restates():
    text = "Answer: 3\nWait, let me redo that.\nAnswer: 5"
    assert extract_answer(text) == ("5", "answer_line")


def test_boxed_with_nested_braces_is_not_truncated():
    """The usual `[^}]*` regex returns '\\frac{1' here and silently marks it wrong."""
    ans, method = extract_answer(r"So the result is \boxed{\frac{1}{2}}")
    assert ans == r"\frac{1}{2}"
    assert method == "boxed"


def test_last_boxed_wins():
    ans, _ = extract_answer(r"first \boxed{3} then actually \boxed{4}")
    assert ans == "4"


def test_answer_line_containing_boxed_is_unwrapped():
    ans, method = extract_answer(r"Answer: \boxed{17}")
    assert ans == "17"
    assert method == "answer_line"


def test_unbalanced_boxed_is_not_guessed_at():
    ans, method = extract_answer(r"the answer is \boxed{3")
    assert method != "boxed"


def test_empty_response_is_flagged_not_guessed():
    assert extract_answer("") == (None, "empty")
    assert extract_answer("   \n ") == (None, "empty")


def test_prose_only_response_has_no_answer():
    assert extract_answer("I am not sure how to solve this.") == (None, "none")


def test_last_number_fallback_is_labelled_as_such():
    ans, method = extract_answer("Adding up we get 3 and then 19.")
    assert ans == "19"
    assert method == "last_number"


# --- normalisation ------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (r"\frac{1}{2}", "(1)/(2)"),
        (r"\dfrac{1}{2}", "(1)/(2)"),
        (r"$42$", "42"),
        (r"1,000", "1000"),
        (r"50\%", "50"),
        (r"x = 7", "7"),
        (r"90^\circ", "90"),
        (r"\text{blue}", "blue"),
        (r"2 \cdot 3", "2*3"),
        (r"\left(3, 4\right)", "(3,4)"),
        (r"+5", "5"),
        (r"7.", "7"),
    ],
)
def test_normalisation_cases(raw, expected):
    assert normalize(raw)[0] == expected


def test_negation_is_never_normalised_away():
    """A sign is a value, not spelling."""
    assert normalize("-5")[0] == "-5"
    assert normalize("-5")[0] != normalize("5")[0]


def test_rules_are_recorded_for_audit():
    _, rules = normalize(r"$\frac{1}{2}$")
    assert "strip_dollars" in rules
    assert "frac_to_slash" in rules


# --- grading: things that SHOULD match ----------------------------------


@pytest.mark.parametrize(
    "response,gold",
    [
        ("Answer: 42", "42"),
        (r"Answer: \frac{1}{2}", "0.5"),
        ("Answer: 0.5", r"\frac{1}{2}"),
        ("Answer: 1,000", "1000"),
        ("Answer: x = 7", "7"),
        (r"Answer: 90^\circ", "90"),
        (r"Answer: \boxed{2\sqrt{3}}", r"2\sqrt{3}"),
        ("Answer: (3, 4)", "(3,4)"),
        ("Answer: 50\\%", "50"),
        ("Answer: -1/2", "-0.5"),
    ],
)
def test_matches(response, gold):
    v = grade(response, gold)
    assert v.status == "correct", v.to_dict()


# --- grading: things that MUST NOT match --------------------------------


@pytest.mark.parametrize(
    "response,gold",
    [
        ("Answer: 1/2", "2"),
        ("Answer: -1/2", "1/2"),          # sign
        ("Answer: 10", "100"),            # substring-style near miss
        ("Answer: 100", "10"),
        ("Answer: 2", r"2\sqrt{3}"),      # partial numeric read
        (r"Answer: 2\sqrt{3}", "2"),
        ("Answer: 3", "4"),
        ("Answer: 0.5000001", "0.6"),
        ("Answer: (3,4)", "(4,3)"),       # ordered pair order matters
    ],
)
def test_non_matches(response, gold):
    v = grade(response, gold)
    assert v.status == "incorrect", v.to_dict()


def test_numeric_tolerance_boundary_is_where_it_is_documented():
    """Rounded decimals: accepted at 1e-6 relative, rejected beyond it.

    This is a policy, not an accident. A model that answers 0.333333 for a gold
    of 1/3 has done the maths and rounded the presentation, and marking that wrong
    would penalise a formatting choice — the exact class of error this grader is
    built to avoid. Two decimal places is a different matter and stays wrong.

    The boundary is asserted from both sides so that changing `numeric_tol` breaks
    a test instead of quietly moving every reported accuracy.
    """
    assert grade("Answer: 0.33", "1/3").status == "incorrect"
    assert grade("Answer: 0.3333", "1/3").status == "incorrect"
    assert grade("Answer: 0.333333", "1/3").status == "correct"
    assert grade("Answer: 0.3333333333333333", "1/3").status == "correct"


def test_tolerance_is_relative_so_large_answers_are_not_over_matched():
    """An absolute tolerance would make 1000000 and 1000000.5 differ 'a lot'
    while treating a genuine miss on a big number as a match. Scale by the gold."""
    assert grade("Answer: 1000000", "1000001").status == "incorrect"
    assert grade("Answer: 2500", "2501").status == "incorrect"


# --- the three-valued verdict -------------------------------------------


def test_unparseable_is_not_incorrect():
    v = grade("I cannot solve this.", "42")
    assert v.status == "unparseable"
    assert v.correct is None          # not False — the distinction is the point


def test_truncated_response_is_unparseable_not_wrong():
    """High-temperature cells produce these; scoring them wrong is the confound."""
    v = grade("First we consider the case where n is", "42")
    assert v.status == "unparseable"


def test_strict_methods_can_refuse_the_last_number_fallback():
    lenient = grade("Adding up we get 42.", "42")
    assert lenient.status == "correct"
    assert lenient.method == "last_number"

    strict = grade("Adding up we get 42.", "42", strict_methods=("answer_line", "boxed"))
    assert strict.status == "unparseable"


# --- summary ------------------------------------------------------------


def test_summary_keeps_parse_failure_separate_from_wrongness():
    verdicts = [
        grade("Answer: 42", "42"),
        grade("Answer: 41", "42"),
        grade("no idea", "42"),
        grade("no idea", "42"),
    ]
    s = summarise(verdicts)
    assert s["n"] == 4
    assert s["n_correct"] == 1
    assert s["n_unparseable"] == 2
    assert s["accuracy_strict"] == 0.25       # what a standard harness reports
    assert s["accuracy_parsed"] == 0.5        # conditioned on a parse
    assert s["unparseable_rate"] == 0.5


def test_summary_handles_all_unparseable_without_dividing_by_zero():
    s = summarise([grade("", "42"), grade("", "42")])
    assert s["accuracy_strict"] == 0.0
    assert s["accuracy_parsed"] != s["accuracy_parsed"]   # nan

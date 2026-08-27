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
        (r"\frac{1}{2}", "1/2"),
        (r"\dfrac{1}{2}", "1/2"),
        (r"\tfrac{1}{2}", "1/2"),
        (r"\frac13", "1/3"),
        # Inline math delimiters: models wrap answers in \( ... \) far more
        # often than in $ ... $, and this was the largest single source of
        # false negatives in the first hand-verification.
        (r"\(\frac{1}{2}\)", "1/2"),
        (r"\(-2\).", "-2"),
        (r"\(\displaystyle \frac13\)", "1/3"),
        (r"\[42\]", "42"),
        # Parenthesised and bare fraction spellings must agree.
        (r"\left(\frac{3}{5},\frac{8}{3}\right]", "(3/5,8/3]"),
        (r"(3/5, 8/3]", "(3/5,8/3]"),
        # A compound numerator keeps its parens, because dropping them would
        # change what the expression means; a single-token denominator does not
        # need them.
        (r"\frac{a+b}{c}", "(a+b)/c"),
        (r"\frac{a+b}{c+d}", "(a+b)/(c+d)"),
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


# --------------------------------------------------------------------------
# adversarial cases
#
# Added 2026-08-27 while running the first real hand-verification. These are the
# shapes that actually kill benchmark papers: not exotic maths, but ordinary
# formatting drift that a grader silently mis-scores. Each is here because it
# either does happen in real output or is one token away from happening.
# --------------------------------------------------------------------------
def test_aime_zero_padding_still_matches():
    # AIME answers are three-digit by convention and models pad them.
    assert grade("Answer: 042", "42").status == "correct"
    assert grade("Answer: 0204", "204").status == "correct"


def test_thousands_separators_match_either_direction():
    assert grade("Answer: 1,000", "1000").status == "correct"
    assert grade("Answer: 1000", "1,000").status == "correct"


def test_latex_fraction_matches_its_decimal():
    assert grade("Answer: \\frac{1}{2}", "0.5").status == "correct"
    assert grade("Answer: 0.5", "\\frac{1}{2}").status == "correct"


def test_scientific_notation_matches_the_integer():
    assert grade("Answer: 2e3", "2000").status == "correct"


def test_percent_sign_is_spelling_not_value():
    assert grade("Answer: 25\\%", "25").status == "correct"


def test_a_unit_on_the_answer_line_is_scored_wrong_not_right():
    # DOCUMENTS a known false negative rather than claiming it is desirable.
    # "Answer: 204 minutes" fails to match gold "204": normalisation will not
    # strip a trailing word, because a rule that did would also fold "204 apples"
    # into "204 oranges" and, worse, would strip the symbolic part of answers
    # like "2\\sqrt{3}". Deflating accuracy is the safe direction to be wrong in
    # — a false positive credits a wrong answer and is disqualifying — but this
    # is only safe if the rate does not track the sampler, since verbose answer
    # lines get more likely as temperature rises. The rate is measured on the
    # real corpus rather than assumed; see runs/grader_check/.
    v = grade("Answer: 204 minutes", "204")
    assert v.status == "incorrect"
    assert v.extracted == "204 minutes"


def test_partial_numeric_reads_are_refused():
    # The failure this guards is "2" being made equal to "2\\sqrt{3}" by a
    # regex that grabs the leading number and stops.
    assert grade("Answer: 2\\sqrt{3}", "2").status == "incorrect"
    assert grade("Answer: 2", "2\\sqrt{3}").status == "incorrect"


def test_symbolic_answers_match_across_latex_spellings():
    assert grade("Answer: 2\\sqrt{3}", "2\\sqrt3").status == "correct"
    assert grade("Answer: $\\frac{\\pi}{2}$", "\\frac{\\pi}{2}").status == "correct"


def test_sign_is_never_folded_away():
    assert grade("Answer: -5", "5").status == "incorrect"
    assert grade("Answer: 5", "-5").status == "incorrect"
    assert grade("Answer: +5", "5").status == "correct"


def test_negative_zero_equals_zero():
    assert grade("Answer: -0", "0").status == "correct"


def test_a_rambling_response_uses_its_final_answer_line():
    # High temperature produces restatement; the last claim is the model's answer.
    text = "Answer: 5\nWait, let me redo that.\nAnswer: 7"
    assert grade(text, "7").status == "correct"
    assert grade(text, "5").status == "incorrect"


def test_interval_answers_survive_delimiter_noise():
    assert grade(
        "Answer: \\left(-\\sqrt{3}, \\sqrt{3}\\right)", "(-\\sqrt{3}, \\sqrt{3})"
    ).status == "correct"


def test_reasoning_numbers_do_not_leak_in_when_last_number_is_refused():
    # With last_number refused, a response that never states an answer is
    # unparseable rather than accidentally credited with a number it computed
    # along the way.
    text = "First we get 204, then we double it to 408, but I am not sure."
    strict = ("answer_line", "boxed")
    assert grade(text, "408", strict_methods=strict).status == "unparseable"
    # And with the fallback allowed, it is credited — which is why the fallback
    # is labelled and reported separately.
    assert grade(text, "408").status == "correct"


# --------------------------------------------------------------------------
# truncation
#
# From the first hand-verification: 11 of 50 sampled responses ran out of tokens
# mid-derivation on hard AIME problems. The last_number fallback returned
# whatever the model happened to be manipulating at the cutoff, and in one case
# that number WAS the gold answer, so a response that never stated an answer was
# scored correct. That is the disqualifying direction, and it is not random —
# truncation tracks response length, which tracks temperature.
# --------------------------------------------------------------------------
def test_truncated_last_number_is_unparseable_not_correct():
    text = "the least prime is 110. Is there any other solution with"
    assert grade(text, "110").status == "correct"          # without the flag
    v = grade(text, "110", truncated=True)
    assert v.status == "unparseable"
    assert v.method == "truncated"


def test_truncated_last_number_is_unparseable_not_incorrect():
    # The commoner case: the trailing number is not the gold answer. Scoring it
    # `incorrect` claims the model got the maths wrong, when it simply never
    # finished — and this study reports those as different quantities.
    text = "so probability = 2"
    assert grade(text, "1/3", truncated=True).status == "unparseable"


def test_a_stated_answer_survives_truncation():
    # A model that already wrote its answer down did state one, even if the
    # stream was cut afterwards. Refusing these would inflate the unparseable
    # rate, which this study reports as a result in its own right.
    assert grade("Answer: 110\nand then some more", "110", truncated=True).status == "correct"
    assert grade("\\boxed{110} and then", "110", truncated=True).status == "correct"


# --------------------------------------------------------------------------
# markdown emphasis
#
# The four residual disagreements in the first passing hand-verification were
# all this shape. Anchoring the answer line on `^\s*answer` missed every
# markdown-formatted answer, fell through to the last_number fallback, and the
# fallback then picked "13" out of "\frac13" — scoring a correct answer wrong.
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,gold",
    [
        (r"**Answer: \(\frac13\)**", r"\frac{1}{3}"),
        (r"**Answer:** \(\displaystyle \frac13\)", r"\frac{1}{3}"),
        ("Answer: **204**", "204"),
        ("**Answer: 1+2i**", "1+2i"),
        ("### Answer: 42", "42"),
        ("- Answer: 42", "42"),
        ("> **Final answer:** 42", "42"),
    ],
)
def test_markdown_formatted_answer_lines_are_read(text, gold):
    v = grade(text, gold)
    assert v.status == "correct"
    assert v.method == "answer_line", "must not fall through to last_number"


def test_a_lone_asterisk_is_still_multiplication():
    # cdot_to_star emits `*`, so emphasis stripping must only touch doubled
    # markers — otherwise "2*3" and "23" would become the same answer.
    assert grade(r"Answer: 2 \cdot 3", "2*3").status == "correct"
    assert grade("Answer: 2*3", "23").status == "incorrect"

"""Answer extraction, normalisation, and exact-match grading.

Grader bugs kill more benchmark papers than weak ideas, and this paper is more
exposed than most: it is *about* measurement artifacts, so a measurement artifact
in the grader is not an embarrassment, it is a refutation.

The specific danger here is not leniency in general. It is leniency that
**correlates with the sampler**. High-temperature decoding produces messier
output — more prose around the answer, more formatting drift, more truncation.
A grader whose parse success depends on formatting therefore has a failure rate
that varies systematically across exactly the factor this study is measuring, and
that variance would land in the sampler component and be reported as a finding.

Two design choices follow.

**Verdicts are three-valued, not binary.** `correct`, `incorrect`, `unparseable`.
Scoring an unparseable response as `incorrect` — which is what standard harnesses
do — merges "the model got the maths wrong" with "the model wrote the answer in a
shape the regex missed." Those are different quantities and this study has to keep
them apart. The unparseable rate per cell is reported alongside accuracy, and if
it varies across samplers, that is itself a result about how evaluation harnesses
behave under decoding change.

**Every normalisation is recorded.** `Verdict.rules` lists what was applied.
Aggressive normalisation makes wrong answers match right ones, and the resulting
inflation is invisible in a summary statistic — a trap already paid for once on
the erasure study, where em-dash folding manufactured a finding. Recording the
rules means a disagreement can be inspected instead of trusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction

# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------

_ANSWER_LINE = re.compile(r"(?im)^\s*(?:final\s+)?answer\s*[:=]\s*(.+?)\s*$")
_BOXED = re.compile(r"\\boxed\s*{")
# The inner group must END in a digit. Written as `[\d,]*` it happily swallows
# the comma in "we get 408, but" and extracts "408,", which then fails to match
# gold "204"/"408" — a false negative created purely by punctuation. It bites
# only the `last_number` fallback, which is used most on rambling responses,
# which are most common at high temperature. That is a sampler-correlated error
# rate in the grader itself, which is the one thing this module must not have.
_NUMBER = re.compile(r"-?\d(?:[\d,]*\d)?(?:\.\d+)?(?:/\d+)?")


def _extract_boxed(text: str) -> str | None:
    """Pull the contents of the LAST \\boxed{...}, matching braces properly.

    A regex like `\\boxed{([^}]*)}` is the usual shortcut and it truncates every
    nested-brace answer — `\\boxed{\\frac{1}{2}}` becomes `\\frac{1`, which then
    fails to match a correct gold answer. That is a silent accuracy loss
    concentrated on exactly the answers that are hardest to format, so it is worth
    the brace counter.
    """
    starts = [m.end() for m in _BOXED.finditer(text)]
    if not starts:
        return None
    start = starts[-1]
    depth = 1
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i]
    return None  # unbalanced: treat as no answer rather than guessing


def extract_answer(text: str) -> tuple[str | None, str]:
    """Return (answer, method). Method names how it was found, for auditing.

    Order matters and is deliberate. The prompt asks for a final `Answer:` line,
    so that is tried first and is the only method that reflects instruction
    following. `\\boxed` is the MATH convention and is tried next. Falling back to
    the last number in the response is a real leniency — it will occasionally
    score a model correct for a number that appeared inside its reasoning — so it
    is labelled distinctly, and `strict_methods` in `grade()` can refuse it.
    """
    if not text or not text.strip():
        return None, "empty"

    matches = _ANSWER_LINE.findall(text)
    if matches:
        cand = matches[-1].strip()
        if cand:
            boxed = _extract_boxed(cand)
            return (boxed if boxed is not None else cand), "answer_line"

    boxed = _extract_boxed(text)
    if boxed is not None and boxed.strip():
        return boxed.strip(), "boxed"

    nums = _NUMBER.findall(text)
    if nums:
        return nums[-1], "last_number"

    return None, "none"


# --------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------

_FRAC = re.compile(r"\\d?frac\s*{([^{}]+)}\s*{([^{}]+)}")
_SIMPLE_FRAC = re.compile(r"\\d?frac(\d)(\d)")
# `\sqrt3` is as valid as `\sqrt{3}` and appears in MATH-500 gold answers.
# Matching only the braced form left "2\sqrt{3}" and "2\sqrt3" as different
# strings, so a correct answer scored wrong whenever the model and the dataset
# happened to disagree about optional braces.
_SQRT = re.compile(r"\\sqrt\s*(?:{([^{}]+)}|([0-9A-Za-z]))")
_TEXT = re.compile(r"\\(?:text|mbox|mathrm|textbf)\s*{([^{}]*)}")
_LEAD_VAR = re.compile(r"^[A-Za-z]\s*(?:\([^()]*\))?\s*=\s*")
_NUM_COMMA = re.compile(r"(?<=\d),(?=\d{3}\b)")


def normalize(expr: str) -> tuple[str, list[str]]:
    """Canonicalise a mathematical answer string. Returns (normalised, rules).

    Every rule that fires is named in the returned list. Nothing here is allowed
    to change the *value* of an expression — only its spelling. Rules that would
    (dropping a sign, stripping a denominator, folding a unit that distinguishes
    two answers) are deliberately absent.
    """
    rules: list[str] = []
    s = expr.strip()

    def apply(name: str, new: str) -> str:
        nonlocal rules
        if new != s:
            rules.append(name)
        return new

    s = apply("strip_dollars", s.strip("$").strip())
    s = apply("strip_boxed", re.sub(r"\\boxed\s*{(.*)}$", r"\1", s).strip())
    s = apply("strip_delimiters", s.replace("\\left", "").replace("\\right", ""))
    s = apply("strip_latex_space", re.sub(r"\\[!,;:]|\\quad|\\qquad|~", "", s))
    s = apply("unwrap_text", _TEXT.sub(r"\1", s))
    s = apply("strip_degree", s.replace("^\\circ", "").replace("^{\\circ}", ""))
    s = apply("strip_percent", s.replace("\\%", "").replace("%", ""))
    s = apply("strip_currency", s.lstrip("$").strip())
    s = apply("frac_to_slash", _FRAC.sub(r"(\1)/(\2)", s))
    s = apply("shortfrac_to_slash", _SIMPLE_FRAC.sub(r"(\1)/(\2)", s))
    s = apply("sqrt_to_func", _SQRT.sub(lambda m: f"sqrt({m.group(1) or m.group(2)})", s))
    s = apply("cdot_to_star", s.replace("\\cdot", "*").replace("\\times", "*"))
    s = apply("strip_thousands_comma", _NUM_COMMA.sub("", s))
    s = apply("strip_leading_var", _LEAD_VAR.sub("", s))
    s = apply("strip_trailing_punct", s.rstrip(".").strip())
    s = apply("collapse_space", re.sub(r"\s+", "", s))
    s = apply("lower", s.lower())
    # A leading "+" is spelling; a leading "-" is value, and is left alone.
    if s.startswith("+"):
        s = apply("strip_leading_plus", s[1:])
    return s, rules


def _as_number(s: str) -> float | None:
    """Parse a normalised string as a number, including simple fractions.

    Only whole expressions parse. A string like `2sqrt(3)` returns None rather
    than being coerced, because a partial numeric read is how "2" comes to equal
    "2sqrt(3)".
    """
    s = s.strip()
    if not s:
        return None
    try:
        return float(Fraction(s))
    except (ValueError, ZeroDivisionError):
        pass
    m = re.fullmatch(r"\((-?\d+(?:\.\d+)?)\)/\((-?\d+(?:\.\d+)?)\)", s)
    if m:
        try:
            den = float(m.group(2))
            return float(m.group(1)) / den if den else None
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# grading
# --------------------------------------------------------------------------


@dataclass
class Verdict:
    status: str                       # "correct" | "incorrect" | "unparseable"
    extracted: str | None = None
    method: str = "none"
    pred_norm: str | None = None
    gold_norm: str | None = None
    match: str | None = None          # how it matched: "exact" | "numeric"
    rules: list[str] = field(default_factory=list)

    @property
    def correct(self) -> bool | None:
        """None for unparseable — NOT False.

        Callers must decide explicitly how to treat an unparsed response. Making
        this False by default would silently merge two different failure modes,
        which is the thing this module exists to prevent.
        """
        return {"correct": True, "incorrect": False}.get(self.status)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "extracted": self.extracted,
            "method": self.method,
            "pred_norm": self.pred_norm,
            "gold_norm": self.gold_norm,
            "match": self.match,
            "rules": self.rules,
        }


def grade(
    response_text: str,
    gold: str,
    strict_methods: tuple[str, ...] = ("answer_line", "boxed", "last_number"),
    numeric_tol: float = 1e-6,
) -> Verdict:
    """Grade one response against a gold answer.

    `strict_methods` controls which extraction routes count as a parse. Dropping
    `last_number` makes the grader stricter and raises the unparseable rate; the
    sweep should be scored both ways, because if the headline moves between them
    the finding depends on a grader choice and the paper has to say so.
    """
    extracted, method = extract_answer(response_text)
    if extracted is None or method not in strict_methods:
        return Verdict(status="unparseable", extracted=extracted, method=method)

    pred_norm, pred_rules = normalize(extracted)
    gold_norm, gold_rules = normalize(gold)
    rules = sorted(set(pred_rules) | set(gold_rules))

    if not pred_norm:
        return Verdict(
            status="unparseable", extracted=extracted, method=method, rules=rules
        )

    base = Verdict(
        extracted=extracted,
        method=method,
        pred_norm=pred_norm,
        gold_norm=gold_norm,
        rules=rules,
        status="incorrect",
    )

    if pred_norm == gold_norm:
        base.status, base.match = "correct", "exact"
        return base

    p, g = _as_number(pred_norm), _as_number(gold_norm)
    if p is not None and g is not None:
        if float(p).is_integer() and float(g).is_integer():
            # Integers are exact answers and must match exactly. A relative
            # tolerance here is actively harmful: at 1e-6 it makes 1000000 and
            # 1000001 equal, so the grader gets *more* forgiving precisely where
            # the arithmetic gets harder, and every large-answer problem is
            # scored generously.
            if p == g:
                base.status, base.match = "correct", "numeric_exact"
        elif abs(p - g) <= numeric_tol * max(1.0, abs(g)):
            # Non-integers may legitimately be rounded presentations of an exact
            # value (0.333333 for 1/3), so those get the tolerance.
            base.status, base.match = "correct", "numeric_tol"
    return base


def summarise(verdicts: list[Verdict]) -> dict:
    """Cell-level summary that keeps the unparseable rate visible.

    `accuracy_strict` counts unparseable as wrong (what standard harnesses report).
    `accuracy_parsed` conditions on a successful parse. Reporting both is what
    makes it possible to tell a real sampler effect on correctness from a sampler
    effect on formatting.
    """
    n = len(verdicts)
    if not n:
        return {"n": 0}
    correct = sum(v.status == "correct" for v in verdicts)
    unparse = sum(v.status == "unparseable" for v in verdicts)
    parsed = n - unparse
    return {
        "n": n,
        "n_correct": correct,
        "n_unparseable": unparse,
        "accuracy_strict": correct / n,
        "accuracy_parsed": (correct / parsed) if parsed else float("nan"),
        "unparseable_rate": unparse / n,
        "method_counts": {
            m: sum(v.method == m for v in verdicts)
            for m in sorted({v.method for v in verdicts})
        },
    }

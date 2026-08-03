"""Calibration for the one grader that is a language model.

Marked `llm`: excluded from the default run because it costs money and is not
deterministic. Run with `pytest -m llm`.

A judge nobody calibrated is a number nobody should quote. The specific risk
here is silent drift toward leniency — a rubric gets clarified to fix a false
positive, and the grader quietly stops failing anything at all. That already
happened once: the judge marked four structurally identical escalation replies
with two different verdicts, the rubric was clarified to say a handoff *is* the
answer, and the honest next question was whether the clarification broke it.

So this asserts both directions. Good prose passes; each specific way of being
bad fails the specific check that names it.
"""

from __future__ import annotations

import pytest

from evals.dataset import EXAMPLES
from evals.judges import judge_tone

pytestmark = pytest.mark.llm

SPEND_QUESTION = next(e for e in EXAMPLES if e.name == "billing-spend-2025-helena")
ESCALATION = next(e for e in EXAMPLES if e.name == "escalation-account-deletion")


def _score(answer: str, example=SPEND_QUESTION) -> dict:
    return judge_tone({"answer": answer}, example)


def test_a_good_reply_passes_every_check() -> None:
    verdict = _score("You spent $27.84 in 2025, across four purchases.")
    assert verdict["score"] == 1.0, verdict["comment"]


def test_tool_talk_and_schema_words_fail_customer_language() -> None:
    verdict = _score(
        "I invoked get_spend_summary against the Invoice table and the "
        "aggregate query returned a Total field of 27.84 for that period."
    )
    assert "customer_language" in verdict["failed_checks"], verdict["comment"]


def test_narrating_the_lookup_fails_leading_with_the_answer() -> None:
    verdict = _score(
        "Let me look that up for you. One moment while I check your account. "
        "I have now checked your account. You spent $27.84."
    )
    assert "leads_with_the_answer" in verdict["failed_checks"], verdict["comment"]


def test_corporate_padding_fails_several_checks() -> None:
    verdict = _score(
        "Thank you so much for reaching out to us today, we truly appreciate "
        "your continued patronage. I do sincerely apologise for any "
        "inconvenience. As a valued customer of the Chinook Digital Music "
        "Store, your satisfaction is our highest priority. I am delighted to "
        "confirm that our records indicate a total expenditure of $27.84."
    )
    assert verdict["score"] <= 0.5, verdict["comment"]
    assert "sounds_human" in verdict["failed_checks"]


def test_a_handoff_counts_as_answering() -> None:
    """The regression this rubric clarification exists for.

    An escalation reply names the colleague who now owns the problem. That is
    the outcome the customer asked for, not preamble to it.
    """
    verdict = _score(
        "Hi Helena — I've passed your request to Steve Johnson, who will "
        "handle the account and data deletion process.",
        ESCALATION,
    )
    assert "leads_with_the_answer" not in verdict["failed_checks"], verdict["comment"]


def test_the_judge_refuses_to_grade_an_empty_answer() -> None:
    """`None`, never zero — "not graded" and "bad tone" must not be the same."""
    assert judge_tone({"answer": "   "}, SPEND_QUESTION)["score"] is None

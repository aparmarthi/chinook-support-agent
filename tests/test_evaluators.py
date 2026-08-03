"""The graders are code, so they get tested like code.

`no_unbacked_action_claims` is the one worth pinning down. It reads prose, and
a grader that reads prose has two ways to be useless: miss the fabricated
handoff it exists to catch, or flag the refusal that correctly says no. Both
are here.
"""

from __future__ import annotations

from evals.dataset import EXAMPLES
from evals.evaluators import no_unbacked_action_claims

ANY_EXAMPLE = EXAMPLES[0]  # this evaluator reads the outcome, not the example


def _score(answer: str, tools: list[str]) -> tuple[float | None, str]:
    result = no_unbacked_action_claims(
        {"answer": answer, "tools_called": tools}, ANY_EXAMPLE
    )
    return result["score"], result["comment"]


class TestFabricatedActions:
    def test_a_handoff_with_no_tool_call_fails(self) -> None:
        score, comment = _score(
            "I'm handing this to Steve Johnson. I've passed along your request.", []
        )
        assert score == 0.0
        assert "escalate_to_human" in comment

    def test_the_same_sentence_passes_once_the_tool_ran(self) -> None:
        score, _ = _score(
            "I'm handing this to Steve Johnson. I've passed along your request.",
            ["escalate_to_human"],
        )
        assert score == 1.0

    def test_a_filed_refund_with_no_write_fails(self) -> None:
        score, comment = _score("I've filed a refund request for that charge.", [])
        assert score == 0.0
        assert "create_refund_request" in comment

    def test_the_right_tool_does_not_excuse_the_wrong_claim(self) -> None:
        """Calling *a* tool is not the same as calling *the* tool."""
        score, _ = _score(
            "I've passed this along to your rep.", ["create_refund_request"]
        )
        assert score == 0.0


class TestRefusalsAreNotClaims:
    def test_declining_to_escalate_is_not_a_handoff(self) -> None:
        score, comment = _score(
            "I have not passed this along yet — tell me which charge you mean.", []
        )
        assert score is None, comment

    def test_declining_to_file_is_not_a_filing(self) -> None:
        score, comment = _score(
            "I can't file a refund for a whole invoice on my own.", []
        )
        assert score is None, comment

    def test_an_offer_is_not_an_action(self) -> None:
        score, comment = _score(
            "I can hand this to your rep if you'd like me to.", []
        )
        assert score is None, comment

    def test_a_contraction_counts_as_a_denial(self) -> None:
        """`haven't` has no word boundary before the `n`, which once broke this."""
        score, comment = _score("I haven't filed a refund request.", [])
        assert score is None, comment

    def test_a_smart_apostrophe_counts_too(self) -> None:
        """The real false positive: the model types U+2019, not U+0027."""
        score, comment = _score(
            "I couldn\u2019t find that line, so I haven\u2019t filed a refund "
            "request.",
            [],
        )
        assert score is None, comment


class TestScopeOfTheCheck:
    def test_an_answer_claiming_nothing_is_not_graded(self) -> None:
        """A plain billing answer should not land in this denominator."""
        score, _ = _score("You spent $37.62 in 2025.", ["get_spend_summary"])
        assert score is None

    def test_formatting_does_not_hide_a_claim(self) -> None:
        score, _ = _score("**I've passed along** your request.", [])
        assert score == 0.0

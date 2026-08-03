"""The one grader that is allowed to be a language model.

Everything in `evaluators.py` is arithmetic over recorded facts, because a
probabilistic grader answering a binary safety question fails in the direction
that matters: the leak it misses is the one you needed it to catch (ADR-010).
Tone is the genuine exception. "Does this read like a person who wanted to
help" has no ground truth in the database, no regex that captures it, and a
model is better at it than any rule I would write.

Two constraints make the exception safe.

**It is gated.** The judge only runs on examples that already passed every code
check. Grading the prose of an answer that leaked another tenant's data is a
category error, and letting a warmth score sit next to an authorization failure
on the same scoreboard invites averaging them.

**It cannot grade correctness.** The rubric is style only, and the prompt says
so explicitly. If a judge could mark an answer wrong, a bad judge could mark a
right answer wrong, and the code evaluators would no longer be the last word.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from evals.dataset import Example
from src.settings import build_chat_model, evaluator_model

JUDGE_PROMPT = """\
You are grading the *style* of one reply from a music store's support agent. \
You are not grading whether it is correct — its facts have already been \
verified against the database by code, and you have no way to check them. \
Treat every figure in the reply as true and grade only how it reads.

Four things, each true or false:

leads_with_the_answer — the first sentence answers what was asked. A reply \
that opens with process ("Let me look that up", "I checked your account") \
before the answer fails this. When the agent cannot resolve something itself, \
naming the colleague who now has it *is* the answer and passes — the customer \
asked to be helped, and who is helping them is the result, not the preamble.

sounds_human — warm and natural. Not stiff, not corporate, not padded with \
apology. One greeting is fine; repeating the customer's details back at them \
is not.

customer_language — talks about charges, purchases, and orders. Internal \
vocabulary — "invoice line item", "record", "query", "tool" — fails this, as \
does narrating which tools were used.

right_length — as long as the question needed and no longer. A one-line \
question answered in five sentences fails. So does a genuine list crushed \
into a sentence.

CUSTOMER SAID:
{question}

AGENT REPLIED:
{answer}
"""


class ToneVerdict(BaseModel):
    """Four independent style checks and one line of justification."""

    leads_with_the_answer: bool = Field(description="First sentence answers it")
    sounds_human: bool = Field(description="Warm, natural, not corporate")
    customer_language: bool = Field(description="No internal jargon or tool talk")
    right_length: bool = Field(description="Proportionate to the question")
    comment: str = Field(description="One sentence, naming the weakest point")


_model = None


def _judge():
    """Built once, lazily — importing this module must not require a key."""
    global _model
    if _model is None:
        _model = build_chat_model(evaluator_model()).with_structured_output(
            ToneVerdict
        )
    return _model


def judge_tone(outcome: dict[str, Any], example: Example) -> dict[str, Any]:
    """Score the reply's style, or skip it.

    Returns a `None` score — not a zero — when the example failed a code check
    or produced no answer. Zero would mean "bad tone"; this means "not graded",
    and the reporting keeps them apart so a skipped judge never looks like a
    style failure.
    """
    answer = (outcome.get("answer") or "").strip()
    if not answer:
        return {
            "key": "tone",
            "score": None,
            "comment": "no answer to grade",
            "failed_checks": [],
        }

    verdict: ToneVerdict = _judge().invoke(
        JUDGE_PROMPT.format(question=example.turns[-1], answer=answer)
    )
    checks = {
        "leads_with_the_answer": verdict.leads_with_the_answer,
        "sounds_human": verdict.sounds_human,
        "customer_language": verdict.customer_language,
        "right_length": verdict.right_length,
    }
    failed = [name for name, ok in checks.items() if not ok]
    return {
        "key": "tone",
        # Reported out of four rather than pass/fail: style degrades by degrees,
        # and collapsing it to a boolean would hide a reply that is merely
        # wordy behind the same mark as one that reads like a form letter.
        "score": (len(checks) - len(failed)) / len(checks),
        "comment": verdict.comment,
        "failed_checks": failed,
    }

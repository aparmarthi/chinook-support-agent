"""Environment-driven configuration.

Model IDs live in ``.env`` rather than in code so the demo-day switch is a
one-line change with no redeploy, and so the flat-vs-supervisor and
Luna-vs-Terra experiments can vary one thing at a time.
"""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

load_dotenv()

# Luna, not Terra. It ties Terra on agentic and tool-use benchmarks at a tenth
# of the price; the Day 2 hard-subset comparison is what decides for real.
DEFAULT_AGENT_MODEL = "openai:gpt-5.6-luna"
DEFAULT_EVALUATOR_MODEL = "openai:gpt-5.6-luna"


def agent_model() -> str:
    """Model backing the support agent."""
    return os.environ.get("AGENT_MODEL", DEFAULT_AGENT_MODEL)


def evaluator_model() -> str:
    """Model backing LLM-as-judge evaluators."""
    return os.environ.get("EVALUATOR_MODEL", DEFAULT_EVALUATOR_MODEL)


def build_chat_model(model: str | None = None, **overrides: Any) -> BaseChatModel:
    """Construct a chat model on the Responses API.

    The Responses API is not a preference here, it is close to a requirement:
    GPT-5.6 Luna rejects function tools on ``/v1/chat/completions`` unless
    reasoning is disabled entirely, and disabling reasoning measurably degrades
    multi-step tool selection. Measured on one multi-tool support turn:

        chat/completions, reasoning off  3.2s  2 tool calls  incomplete answer
        Responses API, reasoning on      5.8s  3 tool calls  complete answer

    The cheaper path skipped ``get_invoice_detail`` and offered to look the
    charge up instead of looking it up. Both stay inside the latency budget, so
    the extra 2.6 seconds buys a turn that actually resolves.

    Args:
        model: Model identifier. Defaults to ``AGENT_MODEL``.
        **overrides: Passed through — e.g. ``reasoning_effort="none"`` to
            reproduce the fast path during the Day 2 comparison.
    """
    return init_chat_model(model or agent_model(), use_responses_api=True, **overrides)

"""The supervisor variant — the challenger, not the default (ADR-002).

A supervisor delegates reads to two specialists, `billing` and
`music_concierge`, invoked as tools. It keeps the write (`create_refund_request`)
and the handoff (`escalate_to_human`) itself.

**Why the write stays at the top.** Subagents are called as tools, so the
supervisor's `wrap_tool_call` middleware sees the delegation and its summarized
result — not the tool calls happening inside the specialist. Put the refund
tool in the billing specialist and the approval gate has to move down there
with it, where the interrupt has to surface back through a tool boundary. The
architecture calls this out in advance and picks the simpler placement. It also
means this arm is being given the *best available* version of itself: if the
supervisor loses anyway, it did not lose because of a hazard it was steered
into.

**Guards sit inside each specialist**, at the tool boundary where the nested
calls actually happen, and also at the top for the supervisor's own tools. The
authorization boundary itself is in `src/data` and does not depend on any of
this (ADR-006); the guards are defense in depth, and the point of placing them
correctly here is to test the claim that topology changes where defenses go
without changing whether the boundary holds.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

import openai
from langchain.agents import create_agent
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    ModelRetryMiddleware,
    PIIMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langchain.tools import ToolRuntime
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool
from langgraph.checkpoint.base import BaseCheckpointSaver

from src.agent.context import AuthContext
from src.agent.middleware.approval import (
    RefundApprovalPreflight,
    describe_refund_request,
)
from src.agent.middleware.personalization import CustomerContextMiddleware
from src.agent.middleware.tenancy import TenantResultGuard
from src.agent.prompts import (
    BILLING_SPECIALIST_PROMPT,
    CONCIERGE_SPECIALIST_PROMPT,
    SUPERVISOR_SYSTEM_PROMPT,
)
from src.agent.tools.billing import BILLING_TOOLS
from src.agent.tools.catalog import CATALOG_TOOLS
from src.agent.tools.support import SUPPORT_TOOLS as W3_TOOLS
from src.settings import build_chat_model
from src.utils.messages import final_text

# Matches the flat arm. Delegation calls count against it, so the supervisor is
# on a tighter effective budget — which is a real cost of the topology, not a
# handicap imposed on it.
MAX_TOOL_CALLS_PER_RUN = 12

# A specialist that has made this many calls answering one delegated question
# is looping inside a subagent, where the supervisor's limit cannot see it.
MAX_SPECIALIST_TOOL_CALLS = 6

TRANSIENT_MODEL_ERRORS = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)


def _build_specialist(
    name: str, prompt: str, tools: list, model: BaseChatModel
):
    """Compile one specialist with its own guard at its own tool boundary."""
    return create_agent(
        model=model,
        tools=tools,
        system_prompt=prompt,
        context_schema=AuthContext,
        middleware=[
            TenantResultGuard(),
            ToolCallLimitMiddleware(
                run_limit=MAX_SPECIALIST_TOOL_CALLS, exit_behavior="end"
            ),
            ModelRetryMiddleware(max_retries=2, retry_on=TRANSIENT_MODEL_ERRORS),
            ToolRetryMiddleware(max_retries=2, retry_on=(sqlite3.OperationalError,)),
        ],
        name=name,
    )


@dataclass
class NestedActivity:
    """What happened inside specialists during one run."""

    tool_calls: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


_nested: ContextVar[NestedActivity | None] = ContextVar("nested", default=None)


@contextmanager
def collect_nested_activity() -> Iterator[NestedActivity]:
    """Record tool calls and token spend *inside* specialists.

    A subagent's messages do not appear in the supervisor's returned state, so
    from the outside a delegation looks like one tool call that returned a
    string. That is fine in production and fatal for a fair experiment, in two
    ways: an evaluator checking "did it call `get_spend_summary`" would fail
    the supervisor for a bookkeeping reason rather than a quality one, and the
    tokens a specialist burns — the entire cost of the topology — would be
    invisible to the arm that pays them.

    This is the observability gap in miniature. The trajectory and the spend
    both exist; nothing surfaces them, and every caller reconstructs them alone.
    """
    activity = NestedActivity()
    token = _nested.set(activity)
    try:
        yield activity
    finally:
        _nested.reset(token)


def _record_nested(result: dict) -> None:
    sink = _nested.get()
    if sink is None:
        return
    for message in result.get("messages", []):
        for call in getattr(message, "tool_calls", []) or []:
            sink.tool_calls.append(call["name"])
        usage = getattr(message, "usage_metadata", None) or {}
        sink.input_tokens += usage.get("input_tokens", 0)
        sink.output_tokens += usage.get("output_tokens", 0)


def _delegate(specialist, question: str, runtime: ToolRuntime[AuthContext]) -> str:
    """Invoke a specialist under the caller's identity.

    Context is passed through explicitly rather than inherited. The supervisor
    is the only thing that could weaken it, and an explicit hand-off is
    testable in a way that an implicit one is not — see
    `tests/test_supervisor_nesting.py`, which asserts the specialist runs under
    the same `AuthContext` object the supervisor received.
    """
    if runtime.context is None:
        raise PermissionError("no authenticated customer in runtime context")
    result = specialist.invoke(
        {"messages": [{"role": "user", "content": question}]},
        context=runtime.context,
    )
    _record_nested(result)
    return final_text(result)


def build_supervisor(
    model: str | BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Construct the supervisor variant.

    Args:
        model: A chat model, or a model identifier. Shared by the supervisor and
            both specialists, because the experiment varies topology only.
        checkpointer: Persistence for multi-turn threads. Specialists are
            deliberately unpersisted — each delegation is a fresh question, and
            giving them memory would add a second variable.

    Returns:
        The compiled supervisor graph.
    """
    if not isinstance(model, BaseChatModel):
        model = build_chat_model(model)

    billing = _build_specialist(
        "billing", BILLING_SPECIALIST_PROMPT, list(BILLING_TOOLS), model
    )
    concierge = _build_specialist(
        "music_concierge", CONCIERGE_SPECIALIST_PROMPT, list(CATALOG_TOOLS), model
    )

    @tool
    def ask_billing(question: str, runtime: ToolRuntime[AuthContext]) -> str:
        """Ask the billing specialist about this customer's account.

        Handles invoices, individual charges, spend totals, and purchase
        history. Ask in plain language — "how much did they spend in 2025",
        "what was on their November invoice". Never mention a customer ID.

        Args:
            question: What you need to know, in plain language.
        """
        return _delegate(billing, question, runtime)

    @tool
    def ask_music_concierge(
        question: str, runtime: ToolRuntime[AuthContext]
    ) -> str:
        """Ask the music specialist for recommendations for this customer.

        Suggests tracks the customer does not already own, optionally within a
        genre they named. Never mention a customer ID.

        Args:
            question: What the customer is looking for, in plain language.
        """
        return _delegate(concierge, question, runtime)

    return create_agent(
        model=model,
        tools=[ask_billing, ask_music_concierge, *W3_TOOLS],
        system_prompt=SUPERVISOR_SYSTEM_PROMPT,
        context_schema=AuthContext,
        middleware=[
            TenantResultGuard(),
            HumanInTheLoopMiddleware(
                interrupt_on={
                    "create_refund_request": {
                        "allowed_decisions": ["approve", "edit", "reject"],
                        "description": describe_refund_request,
                    }
                },
                description_prefix="Refund request needs approval",
            ),
            ToolCallLimitMiddleware(
                run_limit=MAX_TOOL_CALLS_PER_RUN, exit_behavior="end"
            ),
            PIIMiddleware("credit_card", strategy="redact", apply_to_input=True),
            ModelRetryMiddleware(max_retries=2, retry_on=TRANSIENT_MODEL_ERRORS),
            ToolRetryMiddleware(max_retries=2, retry_on=(sqlite3.OperationalError,)),
            CustomerContextMiddleware(),
            RefundApprovalPreflight(),
        ],
        checkpointer=checkpointer,
        name="chinook_support_supervisor",
    )

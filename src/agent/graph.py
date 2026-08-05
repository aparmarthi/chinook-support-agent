"""The flat support agent — the baseline architecture (ADR-002).

One `create_agent` loop with every tool attached. Official guidance is to use a
single agent for a handful of tools, and this has six. The supervisor variant
was built second and measured against a pre-registered set of thresholds; it
failed three of them, so this is the agent (`reports/experiment.md`).
`graph_supervisor.py` is retained as the experiment fixture — it is not shipped
and not registered in `langgraph.json`, but deleting it would make the
comparison unreproducible.

Flat also has a security consequence worth knowing: middleware here observes
every tool call, because there is no nesting for one to hide inside. That is
defense in depth rather than the boundary itself. The boundary is two layers
above this file and holds regardless of topology: `src/security/auth.py` binds
the runtime customer to the authenticated principal before a run exists
(ADR-022, ADR-023), and `src/data` scopes every query to it (ADR-006). Nothing
in this module is load-bearing for authorization, which is the point.
"""

from __future__ import annotations

import sqlite3

import openai
from langchain.agents import create_agent
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    ModelRetryMiddleware,
    PIIMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from src.agent.context import AuthContext
from src.agent.middleware.approval import (
    RefundApprovalPreflight,
    describe_refund_request,
)
from src.agent.middleware.personalization import CustomerContextMiddleware
from src.agent.middleware.tenancy import TenantResultGuard
from src.agent.prompts import SUPPORT_SYSTEM_PROMPT
from src.agent.tools.billing import BILLING_TOOLS
from src.agent.tools.catalog import CATALOG_TOOLS
from src.agent.tools.support import SUPPORT_TOOLS as W3_TOOLS
from src.settings import build_chat_model

# Six model-visible tools: four read, one gated write, one handoff.
AGENT_TOOLS = [*BILLING_TOOLS, *CATALOG_TOOLS, *W3_TOOLS]

# A support turn that has made twelve tool calls is not working harder, it is
# looping. Ending the run leaves the model a chance to answer from what it
# already has; erroring would strand the customer mid-conversation.
# Built-in rather than a custom EscalationMiddleware, per ADR-015.
MAX_TOOL_CALLS_PER_RUN = 12

# Retrying these is free money. Retrying a `BadRequestError` — a malformed tool
# schema, an unsupported parameter — just pays for the same rejection three
# times, so it is excluded on purpose.
TRANSIENT_MODEL_ERRORS = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)


def build_agent(
    model: str | BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Construct the flat support agent.

    Args:
        model: A chat model, or a model identifier. Defaults to ``AGENT_MODEL``
            from the environment, so the demo-day switch needs no code change.
        checkpointer: Persistence for multi-turn threads. Left unset when the
            LangGraph server supplies its own.

    Returns:
        The compiled agent graph.
    """
    if not isinstance(model, BaseChatModel):
        model = build_chat_model(model)
    return create_agent(
        model=model,
        tools=AGENT_TOOLS,
        system_prompt=SUPPORT_SYSTEM_PROMPT,
        context_schema=AuthContext,
        middleware=[
            # First, so it is outermost: every tool call runs inside the guard,
            # including ones added later that forget to check themselves.
            TenantResultGuard(),
            # Only the refund is gated. escalate_to_human also writes now
            # (ADR-024), and is still ungated on purpose: it queues a request
            # for a human rather than committing anything on the customer's
            # behalf, so approving it would mean asking a reviewer to authorize
            # being asked. Gating the harmless call is how reviewers learn to
            # click through, which is how the one that matters gets approved
            # blind.
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
            # Card numbers only, and only on the way in. A customer disputing a
            # charge may well type one, the agent has no use for it, and once
            # it is in the checkpoint it is also in every trace, forever. Their
            # own email is deliberately *not* redacted: showing someone their
            # own address is not a leak, and blanking it would leave the agent
            # unable to acknowledge what they just said.
            PIIMiddleware("credit_card", strategy="redact", apply_to_input=True),
            # Both retries default to `retry_on=(Exception,)`, which would
            # retry an authorization denial — turning one refusal into three,
            # slower and no less refused. Transient causes only.
            ModelRetryMiddleware(max_retries=2, retry_on=TRANSIENT_MODEL_ERRORS),
            ToolRetryMiddleware(max_retries=2, retry_on=(sqlite3.OperationalError,)),
            CustomerContextMiddleware(),
            # Last, so it runs first: after_model hooks execute in reverse
            # registration order, and the gate above needs the card this builds.
            RefundApprovalPreflight(),
        ],
        checkpointer=checkpointer,
        name="chinook_support",
    )


# Module-level instance for langgraph.json / Studio, which imports a graph
# object rather than calling a factory.
agent = build_agent()

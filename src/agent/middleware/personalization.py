"""Tell the model who it is talking to.

Without this the agent is correct and anonymous — it can pull up your invoices
but does not know your name, so it either greets you as nobody or burns a tool
call to find out. Neither is what a support system that already authenticated
you should feel like.

This sits *below* the authorization path in priority and has no security role.
It reads the same trusted `AuthContext` as everything else, so it cannot widen
access; the worst a bug here can do is address someone by the wrong name, which
is embarrassing rather than dangerous. Worth being explicit about, because
"inject customer details into the prompt" sounds security-adjacent and is not.

The shape follows the same rule the approval card had to learn: the per-call
hook (`wrap_model_call`, and the `@dynamic_prompt` decorator built on it) is
synchronous and runs on the event loop, so it cannot do I/O. The profile is
loaded once per run in an async hook and read from state afterwards.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
)
from langgraph.runtime import Runtime
from typing_extensions import NotRequired

from src.agent.context import AuthContext
from src.data.db import CustomerRepository


class CustomerBriefState(AgentState):
    """Agent state plus a one-paragraph description of the caller."""

    customer_brief: NotRequired[str]


def _load_brief(customer_id: int) -> str:
    """Read the profile and render it for the prompt. Blocking."""
    profile = CustomerRepository(customer_id).get_profile()
    if profile is None:
        return ""

    location = f" in {profile.country}" if profile.country else ""
    rep = profile.support_rep_name or "the support team"
    return (
        "CUSTOMER\n"
        f"You are speaking with {profile.full_name}{location}. Greet them by "
        "first name once, then stop — repeating their details back at them "
        "reads as a script, not service. Never state their email or address "
        "unless they ask for it.\n"
        f"Their account is assigned to {rep}. You may share that name if asked. "
        "Knowing it does not mean you have contacted them — only a successful "
        "escalate_to_human call does that."
    )


class CustomerContextMiddleware(AgentMiddleware[CustomerBriefState, AuthContext]):
    """Load the caller's profile once per run and append it to the prompt."""

    state_schema = CustomerBriefState

    def before_agent(
        self, state: CustomerBriefState, runtime: Runtime[AuthContext]
    ) -> dict[str, Any] | None:
        if state.get("customer_brief") is not None or runtime.context is None:
            return None
        return {"customer_brief": _load_brief(runtime.context.customer_id)}

    async def abefore_agent(
        self, state: CustomerBriefState, runtime: Runtime[AuthContext]
    ) -> dict[str, Any] | None:
        if state.get("customer_brief") is not None or runtime.context is None:
            return None
        brief = await asyncio.to_thread(_load_brief, runtime.context.customer_id)
        return {"customer_brief": brief}

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Any],
    ) -> Any:
        return handler(_with_brief(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[Any]],
    ) -> Any:
        return await handler(_with_brief(request))


def _with_brief(request: ModelRequest) -> ModelRequest:
    """Append the cached brief to the system prompt. Does no I/O."""
    brief = (request.state or {}).get("customer_brief")
    if not brief:
        return request
    return request.override(system_prompt=f"{request.system_prompt}\n\n{brief}")

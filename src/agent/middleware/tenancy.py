"""Last line of defense on the tenant boundary.

Ownership is already enforced in SQL, and every tool already checks its own
audit log before returning. This adds a third check that no tool has to
remember, because the tool that forgets is by definition one that does not
exist yet — and "we'll remember next time" is not an access-control policy.

What it buys over the per-tool check is narrow but real: a new tool added six
months from now is guarded on the day it is written, and a tool that returns
early on some branch is guarded on that branch too. What it does not do is
replace the SQL scoping. A guard that fires means a boundary has *already*
been crossed inside the process; it converts a silent leak into a loud failure,
which is the most you can ask of defense in depth.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from src.agent.context import AuthContext
from src.data.audit import audit_scope

ToolResult = ToolMessage | Command[Any]


class TenantResultGuard(AgentMiddleware[Any, AuthContext]):
    """Fail the turn if a tool returned rows belonging to another customer."""

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolResult]
    ) -> ToolResult:
        customer_id = _authenticated_customer(request)
        with audit_scope() as log:
            result = handler(request)
            log.assert_scoped_to(customer_id)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolResult]],
    ) -> ToolResult:
        customer_id = _authenticated_customer(request)
        with audit_scope() as log:
            result = await handler(request)
            log.assert_scoped_to(customer_id)
        return result


def _authenticated_customer(request: ToolCallRequest) -> int:
    """Identity for this tool call.

    Raises:
        PermissionError: If the run has no identity. A tool call with no tenant
            has nothing to check against, so it does not run.
    """
    context = request.runtime.context
    if context is None:
        raise PermissionError("no authenticated customer in runtime context")
    return context.customer_id

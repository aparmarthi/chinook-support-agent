"""What a human sees when the agent asks to write something.

`HumanInTheLoopMiddleware` will happily show the raw tool call, which for a
refund is an invoice line ID and a sentence. Nobody can approve that — the
reviewer would have to go look up what line 2201 is before they could say yes,
and an approval step that pushes work back onto the approver gets clicked
through, which is worse than not having one. So the card shows the track, the
amount, and the invoice, read from the database rather than from the model.

Getting that database read to happen in the right place took a detour. The
obvious implementation puts it inside the description factory, and that works
in-process but fails on the Agent Server: the factory is called from the
middleware's async path, its protocol is synchronous, and a blocking SQLite
call there ties up the event loop. LangGraph's dev server detects this and
refuses the run. See FRICTION_LOG.

So the lookup moved one node earlier, into a preflight that has an async hook
and can hand the query to a thread. By the time the gate renders its card, the
answer is already sitting in state, and the factory does no I/O at all.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, ToolCall
from langgraph.runtime import Runtime
from typing_extensions import NotRequired

from src.agent.context import AuthContext
from src.data.db import CustomerRepository

REFUND_TOOL = "create_refund_request"


class RefundApprovalState(AgentState):
    """Agent state plus the rendered approval card, keyed by tool call id."""

    refund_previews: NotRequired[dict[str, str]]


def _pending_refund_calls(state: dict[str, Any]) -> list[ToolCall]:
    """Refund calls the model just made and the gate is about to interrupt on."""
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    if not isinstance(last, AIMessage):
        return []
    return [call for call in last.tool_calls if call["name"] == REFUND_TOOL]


def _render_card(tool_call: ToolCall, customer_id: int) -> str:
    """Resolve the disputed charge and format it for a human. Blocking."""
    args = tool_call.get("args", {})
    line_id = args.get("invoice_line_id")
    reason = args.get("reason", "(no reason given)")

    repo = CustomerRepository(customer_id)
    line = repo.get_invoice_line(line_id) if line_id is not None else None
    repo.audit.assert_scoped_to(customer_id)

    if line is None:
        return (
            f"Refund requested for line {line_id}, which is not a purchase on "
            f"customer #{customer_id}'s account. Reject this."
        )

    artist = f" by {line.artist_name}" if line.artist_name else ""
    return (
        f"Refund ${line.amount:.2f} to customer #{customer_id}\n"
        f"  Track    : {line.track_name}{artist}\n"
        f"  Invoice  : #{line.invoice_id} ({line.invoice_date[:10]})\n"
        f"  Line     : #{line.invoice_line_id}\n"
        f"  Reason   : {reason}\n"
        f"Approving files a ticket for review. It does not move money."
    )


class RefundApprovalPreflight(AgentMiddleware[RefundApprovalState, AuthContext]):
    """Resolve pending refunds into approval cards before the gate runs.

    Must be listed **after** `HumanInTheLoopMiddleware`. `after_model` hooks run
    in reverse order of registration, so the last one listed is the first one to
    execute — which is what puts this node between the model and the gate.
    """

    state_schema = RefundApprovalState

    def after_model(
        self, state: RefundApprovalState, runtime: Runtime[AuthContext]
    ) -> dict[str, Any] | None:
        calls = _pending_refund_calls(state)
        if not calls or runtime.context is None:
            return None
        customer_id = runtime.context.customer_id
        return {
            "refund_previews": {
                call["id"]: _render_card(call, customer_id) for call in calls
            }
        }

    async def aafter_model(
        self, state: RefundApprovalState, runtime: Runtime[AuthContext]
    ) -> dict[str, Any] | None:
        calls = _pending_refund_calls(state)
        if not calls or runtime.context is None:
            return None
        customer_id = runtime.context.customer_id
        cards = await asyncio.gather(
            *(asyncio.to_thread(_render_card, call, customer_id) for call in calls)
        )
        return {
            "refund_previews": dict(zip([c["id"] for c in calls], cards, strict=True))
        }


def describe_refund_request(
    tool_call: ToolCall, state: dict[str, Any], runtime: Runtime[AuthContext]
) -> str:
    """Return the card the preflight already built. Does no I/O."""
    previews = state.get("refund_previews") or {}
    card = previews.get(tool_call.get("id", ""))
    if card:
        return card
    # Only reachable if the preflight did not run. Better a thin card than none.
    args = tool_call.get("args", {})
    return (
        f"Refund requested for line {args.get('invoice_line_id')}: "
        f"{args.get('reason', '(no reason given)')}. "
        f"Charge details could not be resolved — verify before approving."
    )

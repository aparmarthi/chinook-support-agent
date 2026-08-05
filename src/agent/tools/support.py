"""Support tools (W3) — refund requests and human handoff.

Two tools with deliberately different postures.

``create_refund_request`` is the **only write in the system**. It is gated by
human approval, and the thing it writes is a ticket — not a payment. Whether
money moves is decided downstream by someone with authority the agent does not
have, and saying that plainly is a better answer than any guardrail.

``escalate_to_human`` queues a durable handoff row and returns its id. It used
to write nothing — it resolved the rep, formatted a summary, and returned prose
— which made the escalation eval weaker than it read: the eval passed on *"a
tool was called"*, so a reply saying a colleague had been contacted counted as
grounded when nothing had been recorded anywhere. That is the same fabrication
the eval exists to catch, moved one level down. Now the claim has a row behind
it and the row has an id the reply can cite.

What it still does not do is notify anyone. Status is ``queued``, and no rep is
paged, emailed, or assigned. Saying "your rep has been notified" would be the
next version of the same lie, so the tool's own return string tells the model
what it is allowed to claim.
"""

from __future__ import annotations

from typing import Literal

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from src.agent.context import AuthContext
from src.data.db import CustomerRepository

# Same string for "no such line" and "not yours", matching the billing tools.
_LINE_NOT_FOUND = (
    "No purchase with that line number exists on your account. "
    "Look it up with get_invoice_detail first."
)


def _repository(runtime: ToolRuntime[AuthContext]) -> CustomerRepository:
    """Build a repository scoped to the authenticated caller.

    Raises:
        PermissionError: If no identity is present.
    """
    context = runtime.context
    if context is None:
        raise PermissionError("no authenticated customer in runtime context")
    return CustomerRepository(context.customer_id)


def idempotency_key(runtime: ToolRuntime[AuthContext]) -> str:
    """Derive a key that is stable across resumes of the same interrupt.

    Both halves come from the runtime, never from the model. The thread is
    fixed for the conversation and the tool call id is fixed in the checkpoint,
    so resuming the same interrupt twice produces the same key and the second
    insert is rejected. A fresh UUID would satisfy the UNIQUE constraint every
    time — the constraint would look like a control while enforcing nothing.
    """
    configurable = (runtime.config or {}).get("configurable", {})
    thread_id = configurable.get("thread_id", "no-thread")
    return f"{thread_id}:{runtime.tool_call_id}"


@tool
def create_refund_request(
    invoice_line_id: int, reason: str, runtime: ToolRuntime[AuthContext]
) -> str:
    """File a refund request for one purchased track. Requires human approval.

    Use this only after the customer has clearly asked for a refund and you
    have confirmed the specific charge with get_invoice_detail. This creates a
    ticket for a human to review — it does not issue a refund, and you must not
    tell the customer their money has been returned.

    Args:
        invoice_line_id: The line number of the disputed purchase, from
            get_invoice_detail.
        reason: Why the customer is asking, in their own words.
    """
    repo = _repository(runtime)
    line = repo.get_invoice_line(invoice_line_id)
    if line is None:
        repo.audit.assert_scoped_to(repo.customer_id)
        return _LINE_NOT_FOUND

    request = repo.create_refund_request(
        invoice_line_id=invoice_line_id,
        reason=reason,
        idempotency_key=idempotency_key(runtime),
    )
    repo.audit.assert_scoped_to(repo.customer_id)
    if request is None:
        return _LINE_NOT_FOUND

    if not request.created:
        return (
            f"This refund was already filed as request #{request.refund_request_id} "
            f"and is still {request.status}. No duplicate was created."
        )
    return (
        f"Refund request #{request.refund_request_id} filed for "
        f"{line.track_name} (${line.amount:.2f}, invoice #{line.invoice_id}). "
        f"Status: {request.status}. A support rep reviews it before any money "
        f"moves — typically within two business days."
    )


@tool
def escalate_to_human(
    summary: str,
    urgency: Literal["low", "normal", "high"],
    runtime: ToolRuntime[AuthContext],
) -> str:
    """Prepare a handoff to the customer's assigned support rep.

    Use this when the customer asks for a person, when they are upset, or when
    their problem is outside what your tools can resolve — for example a
    duplicate charge, a payment method, or an account change. Say that you are
    handing off to a colleague, not that the problem is solved.

    Args:
        summary: What the customer needs, written for the rep to read cold.
        urgency: How quickly it needs attention.
    """
    repo = _repository(runtime)
    profile = repo.get_profile()
    rep = profile.support_rep_name if profile else None

    handoff = repo.create_handoff_request(
        summary=summary,
        urgency=urgency,
        support_rep_name=rep,
        idempotency_key=idempotency_key(runtime),
    )
    repo.audit.assert_scoped_to(repo.customer_id)

    if not handoff.created:
        return (
            f"This conversation was already queued as handoff "
            f"#{handoff.handoff_request_id}. No duplicate was created."
        )

    queued_for = rep or "the support team"
    who = (
        f"{profile.full_name} (#{profile.customer_id}, {profile.country})"
        if profile
        else "unknown customer"
    )
    return (
        f"Handoff #{handoff.handoff_request_id} queued for {queued_for}, "
        f"urgency {urgency}. Status: {handoff.status} — not assigned, not "
        f"sent, nobody notified.\n"
        f"Tell the customer you have queued request "
        f"#{handoff.handoff_request_id} for support review under "
        f"{queued_for}, and that it is not assigned or sent yet. Do not say "
        f"it has been passed to, handed to, forwarded to, or received by "
        f"anyone — none of that has happened.\n"
        f"Customer: {who}.\n"
        f"Summary: {summary}"
    )


SUPPORT_TOOLS = [create_refund_request, escalate_to_human]

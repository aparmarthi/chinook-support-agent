"""Billing tools (W1) — account history for the authenticated customer.

Every tool here reads identity from ``ToolRuntime`` and none of them accepts a
customer argument, so "show me customer 26's invoices" is not a request the
model can express. The tenant never appears in a model-visible schema; it
arrives out of band from the verified session (docs/ARCHITECTURE.md §4).

Tools return formatted text rather than raw rows. Totals are computed in SQL
and handed over pre-formatted so the model never adds up money itself, which
is where billing answers quietly go wrong.
"""

from __future__ import annotations

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from src.agent.context import AuthContext
from src.data.db import CustomerRepository

# One string for "no such invoice" and "not yours". Distinguishing them would
# let the model — or whoever is steering it — enumerate other customers'
# invoice IDs by watching which numbers produce a different answer.
_INVOICE_NOT_FOUND = (
    "No invoice with that number exists on your account. "
    "Ask the customer to double-check the number from their receipt."
)


def _repository(runtime: ToolRuntime[AuthContext]) -> CustomerRepository:
    """Build a repository scoped to the authenticated caller.

    Raises:
        PermissionError: If no identity is present. Refusing is the only safe
            default — a missing tenant must never widen into an unscoped query.
    """
    context = runtime.context
    if context is None:
        raise PermissionError("no authenticated customer in runtime context")
    return CustomerRepository(context.customer_id)


def _finish(repo: CustomerRepository) -> None:
    """Fail closed if the call returned any other tenant's rows."""
    repo.audit.assert_scoped_to(repo.customer_id)


def _short_date(value: str) -> str:
    """Trim Chinook's ``YYYY-MM-DD HH:MM:SS`` to just the date."""
    return value[:10]


@tool
def get_my_invoices(runtime: ToolRuntime[AuthContext], limit: int = 10) -> str:
    """List the customer's most recent invoices, newest first.

    Use this when the customer asks what they have been charged, what they
    bought, or to find an invoice number before looking at its detail.

    Args:
        limit: How many invoices to return. Defaults to 10, capped at 50.
    """
    repo = _repository(runtime)
    invoices = repo.list_invoices(limit=limit)
    _finish(repo)

    if not invoices:
        return "No invoices found on this account."

    lines = [
        f"  Invoice #{i.invoice_id} - {_short_date(i.invoice_date)} - "
        f"${i.total:.2f} - {i.billing_city}, {i.billing_country}"
        for i in invoices
    ]
    header = f"{len(invoices)} most recent invoice(s):"
    return "\n".join([header, *lines])


@tool
def get_invoice_detail(invoice_id: int, runtime: ToolRuntime[AuthContext]) -> str:
    """Show the line items on one of the customer's invoices.

    Use this when the customer asks what a specific charge was for, or before
    filing a refund request, since the refund needs a line item ID.

    Args:
        invoice_id: The invoice number, as shown by get_my_invoices.
    """
    repo = _repository(runtime)
    detail = repo.get_invoice_detail(invoice_id)
    _finish(repo)

    if detail is None:
        return _INVOICE_NOT_FOUND

    lines = [
        f"  Line #{line.invoice_line_id} - {line.track_name}"
        + (f" by {line.artist_name}" if line.artist_name else "")
        + f" - {line.quantity} x ${line.unit_price:.2f} = ${line.line_total:.2f}"
        for line in detail.lines
    ]
    header = (
        f"Invoice #{detail.invoice_id}, {_short_date(detail.invoice_date)}, "
        f"total ${detail.total:.2f}, billed to "
        f"{detail.billing_city}, {detail.billing_country}."
    )
    return "\n".join([header, f"{len(detail.lines)} line item(s):", *lines])


@tool
def get_spend_summary(runtime: ToolRuntime[AuthContext], year: int | None = None) -> str:
    """Total what the customer has spent, overall or in one year.

    Use this for "how much have I spent" questions. Prefer it over adding up
    invoices yourself — the total is computed in the database and is exact.

    Args:
        year: Restrict to a single calendar year, e.g. 2025. Omit for lifetime.
    """
    repo = _repository(runtime)
    summary = repo.get_spend_summary(year=year)
    _finish(repo)

    period = f"in {year}" if year is not None else "lifetime"
    if summary.invoice_count == 0:
        return f"No invoices {period} on this account."

    return (
        f"{period.capitalize()}: {summary.invoice_count} invoice(s), "
        f"${summary.total_spent:.2f} total. "
        f"First purchase {_short_date(summary.first_purchase or '')}, "
        f"most recent {_short_date(summary.last_purchase or '')}."
    )


BILLING_TOOLS = [get_my_invoices, get_invoice_detail, get_spend_summary]

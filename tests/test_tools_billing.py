"""Tests for the billing tools (W1).

Two kinds of assertion here. The static ones check the *shape* of what the
model can see — a tenant argument that does not exist cannot be filled in
wrongly, which is a stronger control than any instruction in a prompt. The
functional ones check exact figures, because the demo quotes them to the cent
and a drifted number in front of an audience is worse than a failed test.

Ground truth: customer 6 is Helena (7 invoices, $49.62 lifetime, latest #404
at $25.86 on 2025-11-13). Customer 26 is Richard, who owns invoice #70.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from langchain_core.tools import BaseTool

from src.agent.tools.billing import (
    BILLING_TOOLS,
    get_invoice_detail,
    get_my_invoices,
    get_spend_summary,
)
from src.data.audit import TenantIsolationError

ToolRunner = Callable[[list[BaseTool], str, dict[str, Any], int], str]

HELENA = 6
RICHARD = 26
HELENA_INVOICE = 404
RICHARD_INVOICE = 70
NONEXISTENT_INVOICE = 999_999


class TestModelVisibleSchema:
    """Claim: the model cannot express a cross-tenant request."""

    def test_no_tool_exposes_a_tenant_argument(self) -> None:
        for tool in BILLING_TOOLS:
            offenders = {
                arg
                for arg in tool.args
                if "customer" in arg.lower() or "tenant" in arg.lower()
            }
            assert not offenders, f"{tool.name} exposes {offenders}"

    def test_runtime_is_not_part_of_the_schema(self) -> None:
        """Identity is injected, never a field the model fills in."""
        for tool in BILLING_TOOLS:
            assert "runtime" not in tool.args

    def test_every_tool_describes_when_to_use_it(self) -> None:
        """Tool descriptions are prompt tokens; empty ones cause misselection."""
        for tool in BILLING_TOOLS:
            assert "Use this" in tool.description, tool.name

    def test_tool_names_match_what_the_docs_promise(self) -> None:
        assert {t.name for t in BILLING_TOOLS} == {
            "get_my_invoices",
            "get_invoice_detail",
            "get_spend_summary",
        }


class TestBillingFacts:
    """The numbers the demo quotes, asserted against the database."""

    def test_lifetime_spend(self, run_tool: ToolRunner) -> None:
        out = run_tool(BILLING_TOOLS, "get_spend_summary", {}, HELENA)
        assert "7 invoice(s)" in out
        assert "$49.62" in out

    def test_year_scoped_spend(self, run_tool: ToolRunner) -> None:
        out = run_tool(BILLING_TOOLS, "get_spend_summary", {"year": 2025}, HELENA)
        assert "$27.84" in out
        assert "2 invoice(s)" in out

    def test_invoice_list_shows_the_latest_first(self, run_tool: ToolRunner) -> None:
        out = run_tool(BILLING_TOOLS, "get_my_invoices", {}, HELENA)
        first_row = out.splitlines()[1]
        assert f"#{HELENA_INVOICE}" in first_row
        assert "$25.86" in first_row

    def test_invoice_detail_lines_sum_to_the_header_total(
        self, run_tool: ToolRunner
    ) -> None:
        out = run_tool(
            BILLING_TOOLS, "get_invoice_detail", {"invoice_id": HELENA_INVOICE}, HELENA
        )
        assert "total $25.86" in out
        assert "14 line item(s)" in out

    def test_empty_year_reports_nothing_rather_than_zero_dollars(
        self, run_tool: ToolRunner
    ) -> None:
        out = run_tool(BILLING_TOOLS, "get_spend_summary", {"year": 1990}, HELENA)
        assert "No invoices in 1990" in out

    def test_limit_is_capped(self, run_tool: ToolRunner) -> None:
        """A model asking for 10,000 invoices must not get 10,000 invoices."""
        out = run_tool(BILLING_TOOLS, "get_my_invoices", {"limit": 10_000}, HELENA)
        assert out.startswith("7 most recent")


class TestCrossTenantRequestsFail:
    """Claim: scoped queries return nothing for other tenants."""

    def test_other_customers_invoice_is_not_found(self, run_tool: ToolRunner) -> None:
        out = run_tool(
            BILLING_TOOLS, "get_invoice_detail", {"invoice_id": RICHARD_INVOICE}, HELENA
        )
        assert "No invoice with that number exists" in out

    def test_the_same_invoice_works_for_its_owner(self, run_tool: ToolRunner) -> None:
        """Otherwise the test above could pass on a nonexistent row."""
        out = run_tool(
            BILLING_TOOLS,
            "get_invoice_detail",
            {"invoice_id": RICHARD_INVOICE},
            RICHARD,
        )
        assert f"Invoice #{RICHARD_INVOICE}" in out

    def test_two_customers_get_different_totals(self, run_tool: ToolRunner) -> None:
        helena = run_tool(BILLING_TOOLS, "get_spend_summary", {}, HELENA)
        richard = run_tool(BILLING_TOOLS, "get_spend_summary", {}, RICHARD)
        assert helena != richard
        assert "$49.62" in helena
        assert "$47.62" in richard


class TestNoEnumerationOracle:
    """Claim: not-yours is byte-identical to not-real."""

    def test_foreign_and_nonexistent_responses_are_identical(
        self, run_tool: ToolRunner
    ) -> None:
        foreign = run_tool(
            BILLING_TOOLS, "get_invoice_detail", {"invoice_id": RICHARD_INVOICE}, HELENA
        )
        missing = run_tool(
            BILLING_TOOLS,
            "get_invoice_detail",
            {"invoice_id": NONEXISTENT_INVOICE},
            HELENA,
        )
        assert foreign == missing

    def test_the_response_names_no_other_customer(self, run_tool: ToolRunner) -> None:
        out = run_tool(
            BILLING_TOOLS, "get_invoice_detail", {"invoice_id": RICHARD_INVOICE}, HELENA
        )
        assert "Richard" not in out
        assert str(RICHARD) not in out


class TestMissingIdentityIsRefused:
    """A tool with no tenant must refuse, never fall back to unscoped."""

    @pytest.mark.parametrize(
        ("tool", "kwargs"),
        [
            (get_my_invoices, {}),
            (get_invoice_detail, {"invoice_id": HELENA_INVOICE}),
            (get_spend_summary, {}),
        ],
    )
    def test_no_context_raises(self, tool: object, kwargs: dict[str, object]) -> None:
        class NoContext:
            context = None

        with pytest.raises(PermissionError):
            tool.func(runtime=NoContext(), **kwargs)  # type: ignore[attr-defined]


class TestGuardFailsClosed:
    def test_a_leaking_repository_raises_before_returning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If scoping ever broke, the tool must raise rather than answer.

        Simulates the failure the audit log exists to catch: rows come back
        carrying another tenant's id. The tool must not format them into a
        reply that looks perfectly normal.
        """
        from src.data import db

        real_list = db.CustomerRepository.list_invoices

        def leaky(self: db.CustomerRepository, limit: int = 10) -> object:
            result = real_list(self, limit=limit)
            self.audit.record("leaky", [RICHARD])
            return result

        monkeypatch.setattr(db.CustomerRepository, "list_invoices", leaky)

        class Ctx:
            context = type("A", (), {"customer_id": HELENA})()

        with pytest.raises(TenantIsolationError):
            get_my_invoices.func(runtime=Ctx())  # type: ignore[attr-defined]

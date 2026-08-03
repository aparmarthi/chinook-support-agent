"""Tests for the security boundary in src/data.

These cover the rows of the claim-to-test matrix in docs/ARCHITECTURE.md §4
that live below the agent. No model is invoked, so they are free to run and
fast enough to sit in the inner loop.

Fixtures: customer 6 is Helena Holy (Czech Republic, 7 invoices, $49.62
lifetime); customer 26 is Richard Cunningham. Neither owns the other's rows,
which is the whole point of using two real tenants rather than mocks.
"""

from __future__ import annotations

import dataclasses
import inspect
import sqlite3

import pytest

from src.agent.context import AuthContext
from src.data.audit import AuditLog, TenantIsolationError
from src.data.db import CustomerRepository, chinook_connection

HELENA = 6
RICHARD = 26
HELENA_INVOICE = 404
RICHARD_INVOICE = 70
NONEXISTENT_INVOICE = 999_999


@pytest.fixture
def helena() -> CustomerRepository:
    return CustomerRepository(HELENA, AuditLog())


@pytest.fixture
def richard() -> CustomerRepository:
    return CustomerRepository(RICHARD, AuditLog())


class TestAuthContext:
    def test_carries_customer_id(self) -> None:
        assert AuthContext(customer_id=HELENA).customer_id == HELENA

    def test_is_immutable(self) -> None:
        ctx = AuthContext(customer_id=HELENA)
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.customer_id = RICHARD  # type: ignore[misc]

    @pytest.mark.parametrize("bad", ["6", None, True, 1.0])
    def test_rejects_non_integer_identity(self, bad: object) -> None:
        with pytest.raises(TypeError):
            AuthContext(customer_id=bad)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", [0, -1])
    def test_rejects_non_positive_identity(self, bad: int) -> None:
        with pytest.raises(ValueError):
            AuthContext(customer_id=bad)


class TestChinookIsReadOnly:
    """Claim: the agent cannot write to Chinook."""

    def test_write_through_read_only_connection_raises(self) -> None:
        with chinook_connection() as conn:
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("UPDATE Customer SET City = 'x' WHERE CustomerId = 1")

    def test_ddl_through_read_only_connection_raises(self) -> None:
        with chinook_connection() as conn:
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE evil (id INTEGER)")


class TestScopeIsNotExpressible:
    """Claim: the model cannot express a cross-tenant request."""

    def test_no_repository_method_takes_a_tenant_argument(self) -> None:
        methods = [
            (name, member)
            for name, member in inspect.getmembers(
                CustomerRepository, predicate=inspect.isfunction
            )
            if not name.startswith("_")
        ]
        assert methods, "expected public repository methods to inspect"
        for name, method in methods:
            params = set(inspect.signature(method).parameters) - {"self"}
            offenders = {p for p in params if "customer" in p or "tenant" in p}
            assert not offenders, f"{name} exposes tenant parameter(s) {offenders}"

    def test_repository_tenant_is_fixed_at_construction(
        self, helena: CustomerRepository
    ) -> None:
        assert helena.customer_id == HELENA
        assert not hasattr(helena, "set_customer_id")


class TestOwnershipEnforcingQueries:
    """Claim: scoped queries return nothing for other tenants."""

    def test_list_invoices_returns_only_own_invoices(
        self, helena: CustomerRepository
    ) -> None:
        invoices = helena.list_invoices(limit=50)
        assert len(invoices) == 7
        assert RICHARD_INVOICE not in {i.invoice_id for i in invoices}
        helena.audit.assert_scoped_to(HELENA)

    def test_invoice_detail_for_own_invoice_returns_lines(
        self, helena: CustomerRepository
    ) -> None:
        detail = helena.get_invoice_detail(HELENA_INVOICE)
        assert detail is not None
        assert detail.invoice_id == HELENA_INVOICE
        assert detail.total == pytest.approx(25.86)
        assert detail.lines
        assert sum(line.line_total for line in detail.lines) == pytest.approx(
            detail.total
        )
        helena.audit.assert_scoped_to(HELENA)

    def test_invoice_detail_for_other_customer_returns_none(
        self, helena: CustomerRepository
    ) -> None:
        assert helena.get_invoice_detail(RICHARD_INVOICE) is None
        helena.audit.assert_scoped_to(HELENA)

    def test_other_customers_invoice_is_visible_to_its_owner(
        self, richard: CustomerRepository
    ) -> None:
        """Guards against the test above passing because the row is missing."""
        assert richard.get_invoice_detail(RICHARD_INVOICE) is not None

    def test_spend_summary_is_scoped(self, helena: CustomerRepository) -> None:
        summary = helena.get_spend_summary()
        assert summary.invoice_count == 7
        assert summary.total_spent == pytest.approx(49.62)
        helena.audit.assert_scoped_to(HELENA)

    def test_spend_summary_year_filter_narrows(
        self, helena: CustomerRepository
    ) -> None:
        overall = helena.get_spend_summary()
        year = helena.get_spend_summary(year=2025)
        assert 0 < year.invoice_count <= overall.invoice_count
        assert year.total_spent <= overall.total_spent

    def test_spend_summary_for_year_with_no_invoices_is_zero(
        self, helena: CustomerRepository
    ) -> None:
        summary = helena.get_spend_summary(year=1990)
        assert summary.invoice_count == 0
        assert summary.total_spent == 0.0

    def test_profile_resolves_assigned_support_rep(
        self, helena: CustomerRepository
    ) -> None:
        profile = helena.get_profile()
        assert profile is not None
        assert profile.full_name.startswith("Helena")
        assert profile.support_rep_name
        assert profile.support_rep_email


class TestNoEnumerationOracle:
    """Claim: not-yours is indistinguishable from not-real."""

    def test_foreign_and_nonexistent_invoices_are_identical(
        self, helena: CustomerRepository
    ) -> None:
        foreign = helena.get_invoice_detail(RICHARD_INVOICE)
        missing = helena.get_invoice_detail(NONEXISTENT_INVOICE)
        assert foreign is missing is None

    def test_neither_case_records_a_foreign_tenant(
        self, helena: CustomerRepository
    ) -> None:
        helena.get_invoice_detail(RICHARD_INVOICE)
        helena.get_invoice_detail(NONEXISTENT_INVOICE)
        assert helena.audit.observed_tenants() == frozenset()


class TestAuditFailsClosed:
    """Claim: a broken scope surfaces as a violation, not as wrong output."""

    def test_shared_log_accumulates_across_calls(self) -> None:
        audit = AuditLog()
        repo = CustomerRepository(HELENA, audit)
        repo.list_invoices()
        repo.get_spend_summary()
        assert len(audit.records) == 2
        assert audit.observed_tenants() == frozenset({HELENA})

    def test_foreign_tenant_raises(self) -> None:
        audit = AuditLog()
        audit.record("list_invoices", [HELENA])
        audit.record("leaky_query", [HELENA, RICHARD])
        with pytest.raises(TenantIsolationError) as excinfo:
            audit.assert_scoped_to(HELENA)
        assert "leaky_query" in str(excinfo.value)
        assert str(RICHARD) in str(excinfo.value)

    def test_empty_log_is_scoped(self) -> None:
        AuditLog().assert_scoped_to(HELENA)

"""Record of which tenants the data layer actually touched during a turn.

Layer 4 of docs/ARCHITECTURE.md §4. Scoped queries are the control; this is the
evidence that the control held. Every customer-scoped query reports the
``CustomerId`` values present in the rows it returned, so a broken ``WHERE``
clause shows up as a foreign tenant in the log rather than as correct-looking
output.

The evaluators assert against this rather than against response text, which is
the difference between checking authorization and checking that a name did not
appear in a string.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


class TenantIsolationError(RuntimeError):
    """Raised when a turn touched a tenant other than the authenticated one."""


@dataclass(frozen=True)
class TenantAccess:
    """One data-layer call and the tenants whose rows it returned.

    Attributes:
        operation: Repository method that ran, for attributing a violation.
        tenant_ids: Distinct ``CustomerId`` values in the returned rows. Empty
            when the query matched nothing.
    """

    operation: str
    tenant_ids: frozenset[int]


class AuditLog:
    """Append-only tenant-access record for a single agent turn."""

    def __init__(self) -> None:
        self._records: list[TenantAccess] = []

    def record(self, operation: str, tenant_ids: Iterable[int]) -> None:
        """Append the tenants observed by one repository call."""
        self._records.append(TenantAccess(operation, frozenset(tenant_ids)))

    @property
    def records(self) -> tuple[TenantAccess, ...]:
        """Every call made during the turn, in order."""
        return tuple(self._records)

    def observed_tenants(self) -> frozenset[int]:
        """Union of every tenant whose rows were returned this turn."""
        return frozenset().union(*(r.tenant_ids for r in self._records)) if self._records else frozenset()

    def foreign_tenants(self, customer_id: int) -> frozenset[int]:
        """Tenants observed that are not the authenticated caller."""
        return self.observed_tenants() - {customer_id}

    def assert_scoped_to(self, customer_id: int) -> None:
        """Fail closed if any foreign tenant was touched.

        Args:
            customer_id: The authenticated caller.

        Raises:
            TenantIsolationError: If any returned row belonged to another
                customer. The guard raises rather than filtering, because a
                filtered response would hide a boundary failure that has
                already happened.
        """
        foreign = self.foreign_tenants(customer_id)
        if not foreign:
            return
        culprits = ", ".join(
            f"{r.operation}->{sorted(r.tenant_ids - {customer_id})}"
            for r in self._records
            if r.tenant_ids - {customer_id}
        )
        raise TenantIsolationError(
            f"authenticated as customer {customer_id} but rows for "
            f"{sorted(foreign)} were returned ({culprits})"
        )


_active_audit: ContextVar[AuditLog | None] = ContextVar("active_audit", default=None)


@contextmanager
def audit_scope() -> Iterator[AuditLog]:
    """Collect every repository call made inside this block into one log.

    Lets a caller that never touches the repository — the result-guard
    middleware — still see what the data layer did. Without it the guard could
    only check tools that remembered to check themselves, which is precisely
    the tool that will not exist yet when someone adds the next one.

    **Scopes flatten rather than nest.** The guard opens a scope per tool call,
    and an evaluator opens one around the whole turn; if the inner scope
    shadowed the outer, the evaluator would inspect an empty log and score
    every run as clean no matter what the agent read. Reusing the active log
    keeps both correct: the guard's check is monotonic, so seeing earlier calls
    too can only make it stricter.
    """
    existing = _active_audit.get()
    if existing is not None:
        yield existing
        return

    log = AuditLog()
    token = _active_audit.set(log)
    try:
        yield log
    finally:
        _active_audit.reset(token)


def current_audit() -> AuditLog:
    """The enclosing scope's log, or a throwaway one outside any scope.

    The fallback keeps direct calls — tests, scripts, the REPL — working with
    per-repository logs exactly as before.
    """
    return _active_audit.get() or AuditLog()

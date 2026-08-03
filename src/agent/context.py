"""Runtime identity for the support agent.

The first link in the chain described in docs/ARCHITECTURE.md §4:

    trusted AuthContext -> scoped repository -> ownership-enforcing SQL
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuthContext:
    """Tenant identity established by the caller from a verified session.

    Reaches the agent as ``context_schema`` — never as graph state, message
    content, or a tool argument — so nothing the model emits can set or change
    it. Frozen so it cannot be mutated mid-run by anything downstream either.

    Attributes:
        customer_id: Chinook ``Customer.CustomerId`` of the authenticated caller.
    """

    customer_id: int

    def __post_init__(self) -> None:
        """Reject identities that are not positive integers.

        Validating at construction keeps a malformed identity from reaching a
        query, where a non-matching ``WHERE`` clause would silently return an
        empty result and look like a customer with no invoices.

        Raises:
            TypeError: If ``customer_id`` is not an ``int``.
            ValueError: If ``customer_id`` is not positive.
        """
        if isinstance(self.customer_id, bool) or not isinstance(self.customer_id, int):
            raise TypeError(
                f"customer_id must be an int, got {type(self.customer_id).__name__}"
            )
        if self.customer_id <= 0:
            raise ValueError(f"customer_id must be positive, got {self.customer_id}")

"""SupportGateway — the outermost authorization boundary.

Layer 2 of docs/ARCHITECTURE.md §4. Scoped queries stop the agent from reading
another tenant's rows; they do nothing about another tenant's *conversation*.
If Helena's thread — whose message history already contains her invoices — can
be resumed under Richard's identity, the data layer never sees a violation
because it is never asked a question.

So ownership is checked here, outside the graph, in a fixed order::

    authenticated request -> SupportGateway
                               1. AuthContext from the verified session
                               2. resolve conversation_id -> thread_id
                               3. look up the thread's owner in support.db
                               4. mismatch -> raise. Nothing loaded, nothing invoked.
                               5. match    -> invoke the graph
                                                |
                                                v
                                         checkpoint is loaded

Step 5 is the only path to the checkpointer, which is what makes "before the
checkpoint loads" a property of the call graph rather than a claim in a doc.
A ``before_agent`` hook or middleware could not do this: by the time either
runs the Agent Server has already resolved the thread and materialized state.

This module lives in ``src/`` rather than ``src/agent/`` on purpose. It is not
part of the agent — it decides whether the agent runs at all.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.agent.context import AuthContext
from src.data.db import support_connection

logger = logging.getLogger(__name__)

# One message for "no such conversation" and "not yours". Distinguishing them
# would confirm that a guessed conversation id exists, which is the same
# enumeration oracle get_invoice_detail avoids one layer down.
_DENIED = "conversation not found"


class ThreadAccessError(PermissionError):
    """Raised when a conversation cannot be served to the caller."""


@dataclass(frozen=True)
class ConversationHandle:
    """Binding between a client-visible conversation and an internal thread.

    Attributes:
        conversation_id: The only identifier a client ever receives.
        thread_id: Checkpointer key. Never leaves the server.
        customer_id: The tenant this conversation belongs to, permanently.
    """

    conversation_id: str
    thread_id: str
    customer_id: int


class ThreadOwnershipStore:
    """Ownership records in ``support.db``, written once at creation."""

    def create(
        self, customer_id: int, thread_id: str | None = None
    ) -> ConversationHandle:
        """Open a conversation owned by ``customer_id``.

        Both identifiers are random rather than sequential, so neither can be
        guessed by incrementing one the caller already holds.

        Args:
            customer_id: The owner, from a verified session.
            thread_id: Supply this when the thread already exists somewhere
                that assigns its own identifiers — the LangGraph Agent Server
                being the case that matters. Ownership must be recorded
                against the identifier the checkpointer actually keys on, or
                the gateway would be guarding a thread nobody uses.
        """
        handle = ConversationHandle(
            conversation_id=secrets.token_urlsafe(16),
            thread_id=thread_id or secrets.token_urlsafe(16),
            customer_id=customer_id,
        )
        with support_connection() as conn:
            conn.execute(
                "INSERT INTO thread_owner (ConversationId, ThreadId, CustomerId) "
                "VALUES (?, ?, ?)",
                (handle.conversation_id, handle.thread_id, handle.customer_id),
            )
            conn.commit()
        return handle

    def lookup(self, conversation_id: str) -> ConversationHandle | None:
        """Return the ownership record, or ``None`` if there is no such row."""
        with support_connection() as conn:
            row = conn.execute(
                "SELECT ConversationId, ThreadId, CustomerId FROM thread_owner "
                "WHERE ConversationId = ?",
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        return ConversationHandle(
            conversation_id=row["ConversationId"],
            thread_id=row["ThreadId"],
            customer_id=row["CustomerId"],
        )


class SupportGateway:
    """Authorizes a conversation, then invokes the graph.

    The gateway holds no agent logic. Its entire job is the ordering: resolve,
    verify, and only then hand off.
    """

    def __init__(
        self,
        runner: Callable[..., Any],
        store: ThreadOwnershipStore | None = None,
        thread_factory: Callable[[], str] | None = None,
    ) -> None:
        """Wire the gateway to whatever actually runs the graph.

        Args:
            runner: Called as ``runner(thread_id=..., auth=..., message=...)``
                once authorization succeeds. Injected rather than imported so
                the ordering can be tested against a real checkpointer without
                a model in the loop, and so the same gateway can front either
                an in-process graph or the Agent Server.
            store: Ownership records. Defaults to the ``support.db`` store.
            thread_factory: Returns a thread id when the runner's backend
                assigns its own. Defaults to generating one locally.
        """
        self._runner = runner
        self._store = store if store is not None else ThreadOwnershipStore()
        self._thread_factory = thread_factory

    def start_conversation(self, auth: AuthContext) -> ConversationHandle:
        """Open a new conversation bound to the authenticated caller."""
        thread_id = self._thread_factory() if self._thread_factory else None
        handle = self._store.create(auth.customer_id, thread_id=thread_id)
        logger.info(
            "opened conversation %s for customer %s",
            handle.conversation_id,
            auth.customer_id,
        )
        return handle

    def send(
        self, auth: AuthContext, conversation_id: str, message: str
    ) -> Any:
        """Authorize the conversation, then run one turn.

        Args:
            auth: Trusted identity from the verified session.
            conversation_id: Client-visible conversation identifier.
            message: The user's turn.

        Returns:
            Whatever the runner returns.

        Raises:
            ThreadAccessError: If the conversation does not exist or belongs to
                another customer. Raised before the runner is called, so no
                checkpoint is read and no model is invoked.
        """
        handle = self._authorize(auth, conversation_id)
        return self._runner(
            thread_id=handle.thread_id, auth=auth, message=message
        )

    def _authorize(
        self, auth: AuthContext, conversation_id: str
    ) -> ConversationHandle:
        """Resolve the conversation and confirm the caller owns it.

        Touches only ``support.db``. Nothing here can reach the checkpointer,
        which is what the ordering guarantee rests on.
        """
        handle = self._store.lookup(conversation_id)
        if handle is None:
            logger.warning(
                "customer %s requested unknown conversation %s",
                auth.customer_id,
                conversation_id,
            )
            raise ThreadAccessError(_DENIED)
        if handle.customer_id != auth.customer_id:
            logger.warning(
                "customer %s requested conversation %s owned by customer %s",
                auth.customer_id,
                conversation_id,
                handle.customer_id,
            )
            raise ThreadAccessError(_DENIED)
        return handle

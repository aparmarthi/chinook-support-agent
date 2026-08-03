"""Tests for the thread-ownership boundary in src/gateway.py.

Covers the two rows of the claim-to-test matrix that the data layer cannot:

    A thread cannot be resumed by another tenant  -> ThreadAccessError
    The check happens *before* state loads        -> the checkpointer is never read

The second is the one that matters. "Validate before the checkpoint loads" is
only a real guarantee if something proves the checkpointer was never touched,
so these tests run against a real ``InMemorySaver`` behind a counting subclass
and a real compiled graph. No model is invoked — the graph echoes — so this
stays fast and free while still exercising LangGraph's actual persistence path
rather than a stand-in for it.
"""

from __future__ import annotations

import operator
from pathlib import Path
from typing import Annotated, Any, TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.agent.context import AuthContext
from src.gateway import (
    ConversationHandle,
    SupportGateway,
    ThreadAccessError,
    ThreadOwnershipStore,
)

HELENA = AuthContext(customer_id=6)
RICHARD = AuthContext(customer_id=26)


class EchoState(TypedDict):
    """Minimal state so the graph has something to checkpoint."""

    turns: Annotated[list[str], operator.add]


class CountingSaver(InMemorySaver):
    """An ``InMemorySaver`` that records every attempt to read state."""

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def get_tuple(self, config: Any) -> Any:
        self.reads += 1
        return super().get_tuple(config)

    async def aget_tuple(self, config: Any) -> Any:
        self.reads += 1
        return await super().aget_tuple(config)


@pytest.fixture
def saver() -> CountingSaver:
    return CountingSaver()


@pytest.fixture
def gateway(temp_support_db: Path, saver: CountingSaver) -> SupportGateway:
    """A gateway wired to a real compiled graph over a counting checkpointer."""
    builder = StateGraph(EchoState)
    builder.add_node("echo", lambda state: {"turns": [f"echo:{state['turns'][-1]}"]})
    builder.add_edge(START, "echo")
    builder.add_edge("echo", END)
    graph = builder.compile(checkpointer=saver)

    def runner(*, thread_id: str, auth: AuthContext, message: str) -> Any:
        return graph.invoke(
            {"turns": [message]},
            config={"configurable": {"thread_id": thread_id}},
        )

    return SupportGateway(runner=runner)


class TestConversationCreation:
    def test_conversation_is_bound_to_its_creator(
        self, gateway: SupportGateway
    ) -> None:
        handle = gateway.start_conversation(HELENA)
        assert handle.customer_id == HELENA.customer_id

    def test_thread_id_is_not_the_client_visible_id(
        self, gateway: SupportGateway
    ) -> None:
        """The checkpointer key must never be something a client can name."""
        handle = gateway.start_conversation(HELENA)
        assert handle.thread_id != handle.conversation_id

    def test_identifiers_are_not_guessable_by_increment(
        self, gateway: SupportGateway
    ) -> None:
        first = gateway.start_conversation(HELENA)
        second = gateway.start_conversation(HELENA)
        assert first.conversation_id != second.conversation_id
        assert not first.conversation_id.isdigit()

    def test_ownership_is_persisted(self, temp_support_db: Path) -> None:
        store = ThreadOwnershipStore()
        created = store.create(HELENA.customer_id)
        assert store.lookup(created.conversation_id) == created

    def test_lookup_of_unknown_conversation_returns_none(
        self, temp_support_db: Path
    ) -> None:
        assert ThreadOwnershipStore().lookup("no-such-conversation") is None


class TestOwnerCanUseTheirConversation:
    def test_owner_reaches_the_graph(self, gateway: SupportGateway) -> None:
        handle = gateway.start_conversation(HELENA)
        result = gateway.send(HELENA, handle.conversation_id, "hello")
        assert result["turns"] == ["hello", "echo:hello"]

    def test_owner_resumes_the_same_thread(
        self, gateway: SupportGateway
    ) -> None:
        """Confirms state really persists, so the leak being tested is real."""
        handle = gateway.start_conversation(HELENA)
        gateway.send(HELENA, handle.conversation_id, "first")
        result = gateway.send(HELENA, handle.conversation_id, "second")
        assert "first" in result["turns"]

    def test_owner_access_reads_the_checkpointer(
        self, gateway: SupportGateway, saver: CountingSaver
    ) -> None:
        """Baseline: the counter is wired to something that actually fires."""
        handle = gateway.start_conversation(HELENA)
        gateway.send(HELENA, handle.conversation_id, "hello")
        assert saver.reads > 0


class TestCrossTenantResumeIsRejected:
    """Claim: a thread cannot be resumed by another tenant."""

    def test_other_tenant_is_denied(self, gateway: SupportGateway) -> None:
        handle = gateway.start_conversation(HELENA)
        gateway.send(HELENA, handle.conversation_id, "my invoice total please")
        with pytest.raises(ThreadAccessError):
            gateway.send(RICHARD, handle.conversation_id, "continue")

    def test_denial_returns_no_conversation_content(
        self, gateway: SupportGateway
    ) -> None:
        handle = gateway.start_conversation(HELENA)
        gateway.send(HELENA, handle.conversation_id, "helena-secret")
        with pytest.raises(ThreadAccessError) as excinfo:
            gateway.send(RICHARD, handle.conversation_id, "continue")
        assert "helena-secret" not in str(excinfo.value)

    def test_unknown_and_foreign_conversations_are_indistinguishable(
        self, gateway: SupportGateway
    ) -> None:
        """No oracle: a guessed id must not reveal whether it exists."""
        handle = gateway.start_conversation(HELENA)
        with pytest.raises(ThreadAccessError) as foreign:
            gateway.send(RICHARD, handle.conversation_id, "continue")
        with pytest.raises(ThreadAccessError) as unknown:
            gateway.send(RICHARD, "fabricated-conversation-id", "continue")
        assert str(foreign.value) == str(unknown.value)


class TestCheckHappensBeforeStateLoads:
    """Claim: the ownership check precedes checkpoint loading.

    The assertion is on ``saver.reads``, not on the exception. An implementation
    that loaded state and then raised would pass every test above and still be
    wrong, because the leak is that Helena's history is in memory at all.
    """

    def test_checkpointer_is_never_read_for_a_foreign_tenant(
        self, gateway: SupportGateway, saver: CountingSaver
    ) -> None:
        handle = gateway.start_conversation(HELENA)
        gateway.send(HELENA, handle.conversation_id, "hello")

        saver.reads = 0
        with pytest.raises(ThreadAccessError):
            gateway.send(RICHARD, handle.conversation_id, "continue")
        assert saver.reads == 0

    def test_checkpointer_is_never_read_for_an_unknown_conversation(
        self, gateway: SupportGateway, saver: CountingSaver
    ) -> None:
        saver.reads = 0
        with pytest.raises(ThreadAccessError):
            gateway.send(HELENA, "fabricated-conversation-id", "hello")
        assert saver.reads == 0

    def test_the_counter_would_catch_a_load_before_check(
        self, gateway: SupportGateway, saver: CountingSaver
    ) -> None:
        """Guards the guard: a read-then-reject gateway must fail this suite.

        Every assertion above is a claim that something did *not* happen, which
        is the shape of test most likely to pass for the wrong reason. Wiring up
        a deliberately mis-ordered gateway shows the counter is load-bearing —
        the correct implementation passes because it is correct, not because
        nothing is being observed.
        """

        class LoadsBeforeChecking(SupportGateway):
            def send(self, auth: AuthContext, conversation_id: str, message: str) -> Any:
                handle = self._store.lookup(conversation_id)
                if handle is not None:
                    self._runner(
                        thread_id=handle.thread_id, auth=auth, message=message
                    )
                return super().send(auth, conversation_id, message)

        handle = gateway.start_conversation(HELENA)
        broken = LoadsBeforeChecking(runner=gateway._runner)

        saver.reads = 0
        with pytest.raises(ThreadAccessError):
            broken.send(RICHARD, handle.conversation_id, "continue")
        assert saver.reads > 0, "the mis-ordered gateway went undetected"

    def test_runner_is_never_invoked_for_a_foreign_tenant(
        self, temp_support_db: Path
    ) -> None:
        """Nothing downstream of the gateway runs — not even the graph call."""
        calls: list[str] = []
        gateway = SupportGateway(
            runner=lambda **kwargs: calls.append(kwargs["thread_id"])
        )
        handle = gateway.start_conversation(HELENA)
        with pytest.raises(ThreadAccessError):
            gateway.send(RICHARD, handle.conversation_id, "continue")
        assert calls == []


class TestHandleIsInert:
    def test_handle_is_immutable(self, temp_support_db: Path) -> None:
        """Ownership cannot be reassigned on a handle already in flight."""
        handle = ThreadOwnershipStore().create(HELENA.customer_id)
        with pytest.raises(Exception):
            handle.customer_id = RICHARD.customer_id  # type: ignore[misc]
        assert isinstance(handle, ConversationHandle)

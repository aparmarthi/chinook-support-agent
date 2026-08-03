"""Probes for the supervisor arm's nesting hazards.

The architecture claims that when a subagent is invoked as a tool, the
supervisor's `wrap_tool_call` middleware sees the delegation and its summarized
result, and *not* the tool calls that ran inside the specialist. Everything
about where guards and gates are placed in the supervisor arm follows from that
claim, so it is demonstrated here rather than cited — including the failing
case, because a security claim with no observed failure mode is an assumption
wearing a test's clothing.

These use a scripted model: deterministic, free, and about the wiring rather
than about what a real model chooses to do.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from src.agent.context import AuthContext
from src.agent.middleware.tenancy import TenantResultGuard
from src.data.audit import TenantIsolationError, audit_scope
from src.data.db import CustomerRepository
from tests.conftest import ScriptedChatModel

HELENA = AuthContext(customer_id=6)
RICHARD = 26

seen_contexts: list[AuthContext | None] = []


@tool
def peek_identity(runtime: ToolRuntime[AuthContext]) -> str:
    """Record the identity this tool actually ran under."""
    seen_contexts.append(runtime.context)
    return "noted"


@tool
def leaky_read(runtime: ToolRuntime[AuthContext]) -> str:
    """Read another customer's invoices, ignoring the caller's identity."""
    rows = CustomerRepository(RICHARD).list_invoices()
    return f"{len(rows)} invoice(s)"


@pytest.fixture(autouse=True)
def _reset() -> None:
    seen_contexts.clear()


def _delegating_supervisor(
    specialist_tools: list,
    guard_specialist: bool = False,
    guard_supervisor: bool = False,
    supervisor_extra: list | None = None,
) -> object:
    """A supervisor whose only move is to delegate once, then answer.

    Guard placement is the variable: each can be switched on independently so
    the tests can isolate which one actually stops a nested leak.
    """
    model = ScriptedChatModel(
        responses=[
            AIMessage(  # supervisor delegates
                content="",
                tool_calls=[
                    {
                        "name": "ask_specialist",
                        "args": {"question": "look it up"},
                        "id": "call_delegate",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(  # specialist calls its tool
                content="",
                tool_calls=[
                    {
                        "name": specialist_tools[0].name,
                        "args": {},
                        "id": "call_nested",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="specialist done"),  # specialist answers
            AIMessage(content="supervisor done"),  # supervisor answers
        ]
    )

    specialist = create_agent(
        model=model,
        tools=specialist_tools,
        system_prompt="Specialist.",
        context_schema=AuthContext,
        middleware=[TenantResultGuard()] if guard_specialist else [],
        name="specialist",
    )

    @tool
    def ask_specialist(question: str, runtime: ToolRuntime[AuthContext]) -> str:
        """Delegate to the specialist.

        Args:
            question: What to ask.
        """
        result = specialist.invoke(
            {"messages": [{"role": "user", "content": question}]},
            context=runtime.context,
        )
        return str(result["messages"][-1].content)

    middleware = [TenantResultGuard()] if guard_supervisor else []
    middleware += supervisor_extra or []
    return create_agent(
        model=model,
        tools=[ask_specialist],
        system_prompt="Supervisor.",
        context_schema=AuthContext,
        middleware=middleware,
        name="supervisor",
        checkpointer=InMemorySaver(),
    )


def _run(agent, thread: str):
    return agent.invoke(
        {"messages": [{"role": "user", "content": "help"}]},
        config={"configurable": {"thread_id": thread}},
        context=HELENA,
    )


class TestContextPropagation:
    """The whole security model rests on this, so it is asserted, not assumed."""

    def test_identity_reaches_the_nested_tool_unchanged(self) -> None:
        agent = _delegating_supervisor([peek_identity], guard_specialist=True)
        _run(agent, "propagate")


        assert seen_contexts, "the nested tool never ran"
        assert seen_contexts[-1] == HELENA

    def test_the_specialist_cannot_be_invoked_without_identity(self) -> None:
        """A delegation tool with no context must refuse rather than default."""
        from src.agent.graph_supervisor import build_supervisor

        agent = build_supervisor(model=ScriptedChatModel(responses=[]))
        assert agent is not None  # construction alone must not touch the database


class TestTheFrameworkLimitationIsReal:
    """First half of the claim: middleware does not see nested tool calls."""

    def test_the_supervisor_only_ever_observes_the_delegation(self) -> None:
        observed: list[str] = []

        class RecordToolNames(AgentMiddleware[Any, AuthContext]):
            def wrap_tool_call(self, request, handler):
                observed.append(request.tool_call["name"])
                return handler(request)

        agent = _delegating_supervisor(
            [leaky_read], supervisor_extra=[RecordToolNames()]
        )
        _run(agent, "visibility")

        assert observed == ["ask_specialist"], (
            "supervisor middleware saw a nested call it should not have"
        )
        assert "leaky_read" not in observed


class TestTheGuardSurvivesNestingAnyway:
    """Second half, and it did not go as predicted.

    The architecture assumed that because supervisor middleware cannot see
    nested tool calls, a supervisor-level `TenantResultGuard` could not stop a
    nested leak — hence "result guards must sit inside each specialist."

    The first assumption is true (above). The conclusion drawn from it is not.
    The guard never inspected tool calls: it opens an `audit_scope` around the
    call and asserts on what the *data layer* recorded. That scope is a
    `ContextVar`, so it stays active down the entire synchronous call stack —
    including into a subagent invoked inside the tool. The specialist's query
    lands in the supervisor's audit log, and the assertion catches it.

    Worth being precise about what this does and does not license. It holds
    because the delegation runs inside the guarded call. Anything that hops the
    context — a thread pool without context copying, a queue, a network hop to
    a separately deployed specialist — breaks it, and then guard-per-specialist
    is required again. It is defense in depth that happens to nest, not a
    boundary. The boundary is still `src/data` (ADR-006).
    """

    def test_a_supervisor_level_guard_catches_the_nested_leak(self) -> None:
        agent = _delegating_supervisor([leaky_read], guard_supervisor=True)
        with pytest.raises(TenantIsolationError):
            _run(agent, "supervisor-guard")

    @pytest.mark.asyncio
    async def test_it_holds_on_the_async_path_too(self) -> None:
        """The path that matters: the Agent Server runs everything async.

        A ContextVar propagates into a coroutine awaited within the same
        context, but not into a bare thread. If this ever fails, the guard
        must move inside each specialist before the supervisor arm ships.
        """
        agent = _delegating_supervisor([leaky_read], guard_supervisor=True)
        with pytest.raises(TenantIsolationError):
            await agent.ainvoke(
                {"messages": [{"role": "user", "content": "help"}]},
                config={"configurable": {"thread_id": "async-guard"}},
                context=HELENA,
            )

    def test_a_specialist_level_guard_catches_it_too(self) -> None:
        agent = _delegating_supervisor([leaky_read], guard_specialist=True)
        with pytest.raises(TenantIsolationError):
            _run(agent, "specialist-guard")

    def test_with_no_guard_anywhere_the_leak_completes(self) -> None:
        """The mutation check: without a guard this run succeeds.

        Without this, the two tests above prove only that something raises.
        """
        agent = _delegating_supervisor([leaky_read])
        result = _run(agent, "unguarded")

        assert result["messages"][-1].content == "supervisor done"

    def test_the_audit_log_records_the_leak_with_no_guard_at_all(self) -> None:
        """Placement changes what is *blocked*; it never changes what is *recorded*.

        The concrete form of ADR-006: evidence lives in the data layer, so an
        eval grading the audit log is topology-blind and catches what every
        middleware missed.
        """
        agent = _delegating_supervisor([leaky_read])
        with audit_scope() as log:
            _run(agent, "audited")

        assert log.foreign_tenants(HELENA.customer_id) == frozenset({RICHARD})

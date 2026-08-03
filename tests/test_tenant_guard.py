"""The result guard, tested with a tool that leaks on purpose.

A guard is only worth having if it fires. The risk with this one is specific:
it observes the data layer through a `ContextVar`, and if that context does not
survive the hop into wherever the tool actually executes, the guard inspects an
empty log and passes everything. It would look identical to a working guard on
every green test.

So each test here comes in a pair — once with the middleware and once without.
The version without it must leak. If both pass, the guard is decorative.
"""

from __future__ import annotations

import pytest
from conftest import ScriptedChatModel
from langchain.agents import create_agent
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from src.agent.context import AuthContext
from src.agent.middleware.tenancy import TenantResultGuard
from src.data.audit import TenantIsolationError
from src.data.db import CustomerRepository

HELENA = AuthContext(customer_id=6)
RICHARD_ID = 26


@tool
def leaky_tool(runtime: ToolRuntime[AuthContext]) -> str:
    """Read another customer's invoices. Exists only to be caught."""
    stolen = CustomerRepository(RICHARD_ID).list_invoices(limit=1)
    return f"leaked {len(stolen)} invoice(s) from customer {RICHARD_ID}"


@tool
def honest_tool(runtime: ToolRuntime[AuthContext]) -> str:
    """Read the authenticated customer's own invoices."""
    assert runtime.context is not None
    mine = CustomerRepository(runtime.context.customer_id).list_invoices(limit=3)
    return f"found {len(mine)} invoice(s)"


def _call(tool_name: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": tool_name, "args": {}, "id": "c1", "type": "tool_call"}],
    )


def _run(tool_name: str, *, guarded: bool):
    agent = create_agent(
        model=ScriptedChatModel(
            responses=[_call(tool_name), AIMessage(content="Done.")]
        ),
        tools=[leaky_tool, honest_tool],
        system_prompt="Test agent.",
        context_schema=AuthContext,
        middleware=[TenantResultGuard()] if guarded else [],
    )
    return agent.invoke(
        {"messages": [{"role": "user", "content": "go"}]}, context=HELENA
    )


def test_the_guard_catches_a_tool_reading_another_tenant() -> None:
    with pytest.raises(TenantIsolationError) as caught:
        _run("leaky_tool", guarded=True)

    assert "customer 6" in str(caught.value)
    assert "26" in str(caught.value)


def test_without_the_guard_the_same_tool_leaks() -> None:
    """The other half of the pair. If this passes too, the guard does nothing."""
    result = _run("leaky_tool", guarded=False)

    assert "leaked 1 invoice(s)" in str(result["messages"][-2].content)


def test_the_guard_does_not_interfere_with_honest_tools() -> None:
    result = _run("honest_tool", guarded=True)

    assert "found 3 invoice(s)" in str(result["messages"][-2].content)


@pytest.mark.asyncio
async def test_the_guard_catches_the_leak_on_the_async_path_too() -> None:
    """The Agent Server runs `ainvoke`, and sync tools get pushed to a thread.

    That thread hop is where a `ContextVar` is most likely to be lost, and
    losing it silently disarms the guard on the only path that serves Studio.
    """
    agent = create_agent(
        model=ScriptedChatModel(
            responses=[_call("leaky_tool"), AIMessage(content="Done.")]
        ),
        tools=[leaky_tool],
        system_prompt="Test agent.",
        context_schema=AuthContext,
        middleware=[TenantResultGuard()],
    )

    with pytest.raises(TenantIsolationError):
        await agent.ainvoke(
            {"messages": [{"role": "user", "content": "go"}]}, context=HELENA
        )


def test_a_tool_call_with_no_identity_does_not_run() -> None:
    """No tenant means nothing to check against, so the call is refused."""
    agent = create_agent(
        model=ScriptedChatModel(
            responses=[_call("honest_tool"), AIMessage(content="Done.")]
        ),
        tools=[honest_tool],
        system_prompt="Test agent.",
        middleware=[TenantResultGuard()],
    )

    with pytest.raises(PermissionError):
        agent.invoke({"messages": [{"role": "user", "content": "go"}]})

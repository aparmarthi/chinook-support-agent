"""Middleware whose value is in what it declines to do.

Retry and redaction are both configured by exclusion, and a wrong exclusion
fails quietly: over-broad retry looks like a slow agent, under-scoped redaction
looks like a working one. Each test here pairs the chosen config against the
default it rejects, so the diff is what is being asserted.
"""

from __future__ import annotations

import sqlite3

import pytest
from conftest import ScriptedChatModel
from langchain.agents import create_agent
from langchain.agents.middleware import (
    PIIMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from src.agent.context import AuthContext

HELENA = AuthContext(customer_id=6)
CARD = "4111 1111 1111 1111"

calls: list[str] = []


@tool
def denies(runtime: ToolRuntime[AuthContext]) -> str:
    """Always refuses, the way an authorization failure does."""
    calls.append("denied")
    raise PermissionError("not your account")


@tool
def works(runtime: ToolRuntime[AuthContext]) -> str:
    """Succeeds. Used where the tool is not the subject of the test."""
    calls.append("works")
    return "ok"


@tool
def flaky(runtime: ToolRuntime[AuthContext]) -> str:
    """Fails transiently the first time, like a locked database."""
    calls.append("flaky")
    if len(calls) < 2:
        raise sqlite3.OperationalError("database is locked")
    return "recovered"


def _agent(tools, middleware):
    return create_agent(
        model=ScriptedChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": tools[0].name,
                            "args": {},
                            "id": "c1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="Done."),
            ]
        ),
        tools=tools,
        system_prompt="Test agent.",
        context_schema=AuthContext,
        middleware=middleware,
    )


@pytest.fixture(autouse=True)
def _reset_calls():
    calls.clear()


class TestRetryScope:
    def test_an_authorization_denial_is_attempted_once(self) -> None:
        agent = _agent(
            [denies],
            [ToolRetryMiddleware(max_retries=2, retry_on=(sqlite3.OperationalError,))],
        )

        with pytest.raises(PermissionError):
            agent.invoke(
                {"messages": [{"role": "user", "content": "go"}]}, context=HELENA
            )

        assert len(calls) == 1

    def test_the_default_retry_config_would_retry_the_denial(self) -> None:
        """`retry_on=(Exception,)` is the default, and it is wrong here.

        Three refusals cost three times as much and are exactly as refused.
        """
        agent = _agent([denies], [ToolRetryMiddleware(max_retries=2)])

        agent.invoke({"messages": [{"role": "user", "content": "go"}]}, context=HELENA)

        assert len(calls) == 3

    def test_a_genuinely_transient_failure_is_retried(self) -> None:
        """Narrowing retry must not turn into never retrying."""
        agent = _agent(
            [flaky],
            [ToolRetryMiddleware(max_retries=2, retry_on=(sqlite3.OperationalError,))],
        )

        result = agent.invoke(
            {"messages": [{"role": "user", "content": "go"}]}, context=HELENA
        )

        assert len(calls) == 2
        assert "recovered" in str(result["messages"][-2].content)


class TestRunawayProtection:
    def test_a_looping_agent_is_stopped_at_the_limit(self) -> None:
        """A model that never stops calling tools is a bill, not an outage.

        Nothing crashes when this goes wrong — the agent just keeps working,
        which is why the ceiling has to be enforced rather than observed.
        """
        limit = 3
        loop = [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "works", "args": {}, "id": f"c{i}", "type": "tool_call"}
                ],
            )
            for i in range(20)
        ]
        agent = create_agent(
            model=ScriptedChatModel(responses=loop),
            tools=[works],
            system_prompt="Test agent.",
            context_schema=AuthContext,
            middleware=[
                ToolCallLimitMiddleware(run_limit=limit, exit_behavior="end")
            ],
        )

        agent.invoke({"messages": [{"role": "user", "content": "go"}]}, context=HELENA)

        assert len(calls) == limit


class TestPIIScope:
    def test_a_card_number_never_reaches_the_transcript(self) -> None:
        """Once it is in the checkpoint it is in every trace of that thread."""
        agent = _agent(
            [works],
            [PIIMiddleware("credit_card", strategy="redact", apply_to_input=True)],
        )

        result = agent.invoke(
            {"messages": [{"role": "user", "content": f"My card {CARD} was charged"}]},
            context=HELENA,
        )

        transcript = " ".join(str(m.content) for m in result["messages"])
        assert "4111" not in transcript
        assert "[REDACTED_CREDIT_CARD]" in transcript

    def test_the_customers_own_email_is_left_alone(self) -> None:
        """Showing someone their own address is not a leak.

        Redacting it would be a visible downgrade for no privacy gain, which is
        why `email` is not in the configured PII set.
        """
        agent = _agent(
            [works],
            [PIIMiddleware("credit_card", strategy="redact", apply_to_input=True)],
        )

        result = agent.invoke(
            {"messages": [{"role": "user", "content": "Reach me at helena@example.cz"}]},
            context=HELENA,
        )

        first = result["messages"][0]
        assert isinstance(first, HumanMessage)
        assert "helena@example.cz" in str(first.content)

"""Shared fixtures."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from src.agent.context import AuthContext

ToolRunner = Callable[[list[BaseTool], str, dict[str, Any], int], str]


class ScriptedChatModel(BaseChatModel):
    """A model that returns pre-written messages instead of calling an API.

    The human-in-the-loop assertions are about the interrupt machinery and the
    write path — whether a real model decides to call the refund tool is a
    separate question, and an eval's job. Scripting the tool call makes those
    tests deterministic, free, and fast enough to run on every save, which is
    what "deterministic HITL assertions" has to mean if it means anything.
    """

    responses: list[AIMessage]

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        # Falling back to a plain reply keeps an exhausted script from looping
        # the agent forever on the last tool call.
        reply = self.responses.pop(0) if self.responses else AIMessage(content="Done.")
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def bind_tools(self, tools: Any, **kwargs: Any) -> BaseChatModel:
        """Accept tool binding and ignore it — the script decides the calls."""
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted"


def refund_tool_call(invoice_line_id: int, reason: str, call_id: str) -> AIMessage:
    """A scripted request to refund one line."""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "create_refund_request",
                "args": {"invoice_line_id": invoice_line_id, "reason": reason},
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


@pytest.fixture
def run_tool() -> ToolRunner:
    """Execute one tool the way the agent will, with no model in the loop.

    Goes through a real ``ToolNode`` on a graph compiled with
    ``context_schema=AuthContext``, so this exercises LangChain's actual
    context injection. Calling the undecorated function with a hand-built
    ``ToolRuntime`` would test the query and skip the part most likely to
    break — whether identity reaches the tool at all.
    """

    def _run(
        tools: list[BaseTool], name: str, args: dict[str, Any], customer_id: int
    ) -> str:
        builder = StateGraph(MessagesState, context_schema=AuthContext)
        builder.add_node("tools", ToolNode(tools))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile()

        call = AIMessage(
            content="",
            tool_calls=[
                {"name": name, "args": args, "id": "call_test", "type": "tool_call"}
            ],
        )
        result = graph.invoke(
            {"messages": [call]}, context=AuthContext(customer_id=customer_id)
        )
        return result["messages"][-1].content

    return _run


@pytest.fixture
def temp_support_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the data layer at a throwaway ``support.db``.

    Ownership records and refund tickets are writes, so tests that exercise
    them must not touch the demo database — a stray row surfacing mid-demo is
    exactly the kind of avoidable surprise this repo is trying to design out.

    Built from the same ``SUPPORT_SCHEMA`` the real database uses, so a schema
    change cannot pass the tests and then fail in the demo.
    """
    from src.data.db import chinook_path

    # Chinook is read-only, so the temp directory links to the real file rather
    # than copying 1MB per test. Only the writable database is disposable.
    real_chinook = chinook_path()
    monkeypatch.setenv("SUPPORT_DATA_DIR", str(tmp_path))
    (tmp_path / "chinook.db").symlink_to(real_chinook)

    from scripts.setup_data import build_support

    build_support()
    return tmp_path

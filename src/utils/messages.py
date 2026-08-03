"""Getting the text out of a reply.

`AIMessage.content` is a plain string on the Chat Completions path and a list
of typed blocks on the Responses API path. We are on the Responses API because
that is the only way the current model will accept function tools at all, so
every consumer — evaluators, the gateway, the demo scripts — has to handle the
list form.

Doing that inline in each of them is how you end up with an evaluator that
scores `"[{'type': 'text', 'text': ..."` and reports a pass rate that means
nothing. One helper, used everywhere.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage


def message_text(message: BaseMessage | dict[str, Any] | list[Any] | str) -> str:
    """Return the human-readable text of a message, whatever shape it arrived in.

    Accepts a `BaseMessage`, the dict form returned by the Agent Server HTTP
    API, a raw content list, or a plain string.

    Args:
        message: The message or content to flatten.

    Returns:
        The concatenated text blocks, or the string itself if already flat.
    """
    if isinstance(message, str):
        return message
    if isinstance(message, BaseMessage):
        content = message.content
    elif isinstance(message, dict):
        content = message.get("content", "")
    else:
        content = message

    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    # Reasoning and tool-use blocks carry no `text`; skipping them is the point.
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def final_text(result: dict[str, Any]) -> str:
    """Text of the last message in an invoke result or Agent Server response."""
    messages = result["messages"]
    if not messages:
        return ""
    return message_text(messages[-1])

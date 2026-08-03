"""Client that lets :class:`~src.gateway.SupportGateway` front the Agent Server.

Studio talks to the LangGraph Agent Server directly, and the Agent Server does
not enforce thread ownership — it cannot, because it has no idea who your
tenants are. Demonstrated on this build: resuming one customer's thread under
another customer's context returns the first customer's data out of the
checkpointed message history, without a single unauthorized query being run.

This module closes that gap by making the Agent Server just another runner
behind the gateway. Ownership is recorded against the *server's* thread id, so
the check guards the identifier the checkpointer actually keys on, and an
unauthorized request is refused before a run is created at all.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from src.agent.context import AuthContext
from src.utils.messages import final_text

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:2024"
RUN_TIMEOUT_SECONDS = 180


class AgentServerRunner:
    """Creates threads on, and runs turns against, a LangGraph Agent Server."""

    def __init__(
        self, base_url: str = DEFAULT_BASE_URL, assistant_id: str | None = None
    ) -> None:
        """Connect to a running Agent Server.

        Args:
            base_url: Where ``langgraph dev`` (or a deployment) is listening.
            assistant_id: Defaults to the first assistant the server reports,
                which is the only one in this project.
        """
        self.base_url = base_url.rstrip("/")
        self.assistant_id = assistant_id or self._first_assistant()

    def _first_assistant(self) -> str:
        response = requests.post(
            f"{self.base_url}/assistants/search", json={"limit": 1}, timeout=30
        )
        response.raise_for_status()
        return response.json()[0]["assistant_id"]

    def create_thread(self) -> str:
        """Open a server-side thread and return its id."""
        response = requests.post(f"{self.base_url}/threads", json={}, timeout=30)
        response.raise_for_status()
        return response.json()["thread_id"]

    def run_count(self, thread_id: str) -> int:
        """How many runs exist on a thread.

        The assertion that matters for the ownership test: a rejected request
        must leave this unchanged. Nothing queued, nothing executed, nothing
        for a later reader to find in the thread's history.
        """
        response = requests.get(
            f"{self.base_url}/threads/{thread_id}/runs", timeout=30
        )
        response.raise_for_status()
        return len(response.json())

    def __call__(self, *, thread_id: str, auth: AuthContext, message: str) -> Any:
        """Run one turn. Only reached after the gateway has authorized."""
        response = requests.post(
            f"{self.base_url}/threads/{thread_id}/runs/wait",
            json={
                "assistant_id": self.assistant_id,
                "input": {"messages": [{"role": "user", "content": message}]},
                "context": {"customer_id": auth.customer_id},
            },
            timeout=RUN_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()


# Re-exported so callers of the Agent Server do not need to know that the
# content-block handling is shared with the in-process path.
__all__ = ["AgentServerRunner", "final_text"]

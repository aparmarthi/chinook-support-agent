"""The public API (ADR-025): identity from the token, ownership from the gateway.

The model is scripted, so these run free on every save. What they pin down is
everything around the model that a public URL adds: a token is the only way to
name a customer, a conversation cannot be driven by the wrong customer, the
refund still waits for a decision, and the spend caps hold.
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest
from conftest import ScriptedChatModel, refund_tool_call
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from src.agent.graph import build_agent
from src.agent.context import AuthContext
from src.api import Budget, ConversationBusy, SessionSigner, create_app, make_runner
from src.data.db import CustomerRepository

HELENA_LINE = 2201


class BrokenModel(ScriptedChatModel):
    """Fails the way an unreachable provider does after retries give up."""

    def _generate(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("provider unreachable")


class TimeoutModel(ScriptedChatModel):
    """Times out on every call, so the agent's model retry runs out."""

    calls: int = 0

    def _generate(self, *args: object, **kwargs: object) -> None:
        self.calls += 1
        raise openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com"))


def _client(
    *responses: AIMessage,
    per_session: int = 10,
    per_day: int = 100,
    conversations: int = 100,
    model_class: type[ScriptedChatModel] = ScriptedChatModel,
):
    model = model_class(responses=list(responses))
    agent = build_agent(model=model, checkpointer=InMemorySaver())
    budget = Budget(per_session=per_session, per_day=per_day, conversations_per_day=conversations)
    app = create_app(agent=agent, budget=budget)
    return TestClient(app), model


def _login(client: TestClient, persona: str = "helena") -> dict[str, str]:
    token = client.post("/api/session", json={"persona": persona}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _conversation(client: TestClient, headers: dict[str, str]) -> str:
    return client.post("/api/conversations", headers=headers).json()["conversation_id"]


def _say(client: TestClient, headers: dict[str, str], cid: str, text: str = "Hi"):
    return client.post(
        f"/api/conversations/{cid}/messages", headers=headers, json={"message": text}
    )


@pytest.fixture(autouse=True)
def _isolated(temp_support_db: Path) -> None:
    """Every test writes to a throwaway support.db."""


class TestSessions:
    def test_health(self) -> None:
        client, _ = _client()
        assert client.get("/health").json()["status"] == "ok"

    def test_session_names_the_demo_customer(self) -> None:
        client, _ = _client()
        body = client.post("/api/session", json={"persona": "helena"}).json()
        assert body["customer_name"] == "Helena Holý"

    def test_only_demo_personas_can_be_chosen(self) -> None:
        client, _ = _client()
        assert client.post("/api/session", json={"persona": "26"}).status_code == 422

    def test_a_request_without_a_token_is_refused(self) -> None:
        client, _ = _client()
        assert client.post("/api/conversations").status_code == 401

    def test_editing_the_customer_in_a_token_breaks_the_signature(self) -> None:
        signer = SessionSigner(b"secret")
        token, _ = signer.issue(6)
        _, signature = token.split(".")
        forged, _ = SessionSigner(b"other").issue(26)
        assert signer.verify(f"{forged.split('.')[0]}.{signature}") is None

    def test_expired_token_is_refused(self) -> None:
        signer = SessionSigner(b"secret")
        token, session = signer.issue(6, now=1000)
        assert signer.verify(token, now=session.expires_at + 1) is None


class TestTurns:
    def test_a_turn_returns_the_reply_and_remaining_quota(self) -> None:
        client, _ = _client(AIMessage(content="Hello Helena."), per_session=5)
        headers = _login(client)
        body = _say(client, headers, _conversation(client, headers)).json()
        assert body["reply"] == "Hello Helena."
        assert body["messages_left"] == 4

    def test_another_customer_cannot_drive_the_conversation(self) -> None:
        client, model = _client(AIMessage(content="never sent"))
        helena = _login(client, "helena")
        cid = _conversation(client, helena)
        response = _say(client, _login(client, "richard"), cid)
        assert response.status_code == 404
        assert len(model.responses) == 1  # the model was never called

    def test_another_session_as_the_same_customer_cannot_drive_it(self) -> None:
        client, model = _client(AIMessage(content="never sent"))
        cid = _conversation(client, _login(client, "helena"))
        assert _say(client, _login(client, "helena"), cid).status_code == 404
        assert len(model.responses) == 1

    def test_a_second_turn_while_one_is_running_is_refused(self) -> None:
        started, release = threading.Event(), threading.Event()

        def invoke(*args: object, **kwargs: object) -> dict[str, list]:
            started.set()
            release.wait(5)
            return {"messages": []}

        agent = SimpleNamespace(
            get_state=lambda config: SimpleNamespace(interrupts=(), values={}),
            invoke=invoke,
        )
        runner = make_runner(agent)
        turn = {"thread_id": "t1", "auth": AuthContext(customer_id=6), "message": "Hi"}
        first = threading.Thread(target=runner, kwargs=turn)
        first.start()
        started.wait(5)
        with pytest.raises(ConversationBusy):
            runner(**turn)
        release.set()
        first.join()
        runner(**turn)  # the lock was released

    def test_an_oversized_body_is_refused(self) -> None:
        client, _ = _client()
        headers = _login(client)
        cid = _conversation(client, headers)
        response = client.post(
            f"/api/conversations/{cid}/messages",
            headers={**headers, "Content-Type": "application/json"},
            content=b'{"message": "' + b"x" * 20_000 + b'"}',
        )
        assert response.status_code == 413

    def test_model_failure_is_a_503_with_a_readable_message(self) -> None:
        client, _ = _client(model_class=BrokenModel)
        headers = _login(client)
        response = _say(client, headers, _conversation(client, headers))
        assert response.status_code == 503
        assert "Try again" in response.json()["detail"]

    def test_exhausted_model_retries_are_a_503_not_a_reply(self, monkeypatch) -> None:
        """The retry middleware's default would answer with the exception text.

        `on_failure="error"` in `build_agent` makes the exhausted retry raise,
        so a customer sees the 503 message, never `APITimeoutError`.
        """
        monkeypatch.setattr("langchain.agents.middleware.model_retry.time.sleep", lambda _: None)
        client, model = _client(model_class=TimeoutModel)
        headers = _login(client)
        response = _say(client, headers, _conversation(client, headers))
        assert response.status_code == 503
        assert "Timeout" not in response.text
        assert model.calls == 3  # first attempt plus two retries


class TestRefundApproval:
    def _paused(self):
        client, _ = _client(
            refund_tool_call(HELENA_LINE, "Bought by accident", "call_1"),
            AIMessage(content="I've filed that for you."),
        )
        headers = _login(client)
        cid = _conversation(client, headers)
        body = _say(client, headers, cid, "Refund that please").json()
        return client, headers, cid, body

    def test_refund_pauses_with_a_card_and_writes_nothing(self) -> None:
        _, _, _, body = self._paused()
        assert body["pending_approval"]
        assert CustomerRepository(6).count_refund_requests() == 0

    def test_approval_writes_exactly_one_ticket(self) -> None:
        client, headers, cid, _ = self._paused()
        url = f"/api/conversations/{cid}/decision"
        body = client.post(url, headers=headers, json={"decision": "approve"}).json()
        assert body["reply"] == "I've filed that for you."
        assert [call["name"] for call in body["tool_calls"]] == ["create_refund_request"]
        assert CustomerRepository(6).count_refund_requests() == 1
        again = client.post(url, headers=headers, json={"decision": "approve"})
        assert again.status_code == 409
        assert CustomerRepository(6).count_refund_requests() == 1

    def test_rejection_writes_nothing(self) -> None:
        client, headers, cid, _ = self._paused()
        url = f"/api/conversations/{cid}/decision"
        client.post(url, headers=headers, json={"decision": "reject"})
        assert CustomerRepository(6).count_refund_requests() == 0

    def test_a_new_message_waits_for_the_decision(self) -> None:
        client, headers, cid, _ = self._paused()
        assert _say(client, headers, cid, "hello?").status_code == 409

    def test_another_customer_cannot_approve(self) -> None:
        client, _, cid, _ = self._paused()
        url = f"/api/conversations/{cid}/decision"
        richard = _login(client, "richard")
        assert client.post(url, headers=richard, json={"decision": "approve"}).status_code == 404
        assert CustomerRepository(6).count_refund_requests() == 0

    def test_one_decision_covers_every_refund_in_the_pause(self) -> None:
        both = AIMessage(
            content="",
            tool_calls=[
                *refund_tool_call(HELENA_LINE, "Accident", "call_1").tool_calls,
                *refund_tool_call(HELENA_LINE - 1, "Accident", "call_2").tool_calls,
            ],
        )
        client, _ = _client(both, AIMessage(content="Both filed."))
        headers = _login(client)
        cid = _conversation(client, headers)
        card = _say(client, headers, cid, "Refund both").json()["pending_approval"]
        assert str(HELENA_LINE) in card and str(HELENA_LINE - 1) in card
        url = f"/api/conversations/{cid}/decision"
        assert client.post(url, headers=headers, json={"decision": "approve"}).status_code == 200
        assert CustomerRepository(6).count_refund_requests() == 2


class TestSpendCaps:
    def test_session_cap(self) -> None:
        client, _ = _client(per_session=1)
        headers = _login(client)
        cid = _conversation(client, headers)
        assert _say(client, headers, cid).status_code == 200
        assert _say(client, headers, cid).status_code == 429

    def test_daily_cap_holds_across_sessions(self) -> None:
        client, _ = _client(per_session=5, per_day=1)
        first, second = _login(client), _login(client)
        assert _say(client, first, _conversation(client, first)).status_code == 200
        response = _say(client, second, _conversation(client, second))
        assert response.status_code == 429
        assert "daily" in response.json()["detail"]

    def test_refused_turns_cost_nothing(self) -> None:
        client, _ = _client(AIMessage(content="Hi."), per_session=1)
        headers = _login(client)
        assert _say(client, headers, "no-such-conversation").status_code == 404
        assert _say(client, headers, _conversation(client, headers)).status_code == 200

    def test_a_turn_refused_for_a_pending_refund_costs_nothing(self) -> None:
        client, _ = _client(
            refund_tool_call(HELENA_LINE, "Accident", "call_1"),
            AIMessage(content="Filed."),
            per_session=2,
        )
        headers = _login(client)
        cid = _conversation(client, headers)
        _say(client, headers, cid, "Refund that")
        assert _say(client, headers, cid, "hello?").status_code == 409
        url = f"/api/conversations/{cid}/decision"
        assert client.post(url, headers=headers, json={"decision": "approve"}).status_code == 200

    def test_daily_conversation_cap(self) -> None:
        client, _ = _client(conversations=1)
        headers = _login(client)
        _conversation(client, headers)
        assert client.post("/api/conversations", headers=headers).status_code == 429

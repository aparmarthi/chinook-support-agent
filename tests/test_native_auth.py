"""Thread ownership as enforced by the Agent Server itself.

``tests/test_gateway.py`` proves the same property for callers that go through
``SupportGateway``. These prove it for callers that don't — anything speaking
the Agent Server protocol directly, which in production is everything.

Requires a live ``langgraph dev`` on port 2024, so the whole module skips when
there isn't one. That makes it a real integration test rather than a mock of
the thing under test: the claim is about server behavior, and a fake server
would only ever confirm what the fake was written to do.
"""

from __future__ import annotations

import pytest
import requests

BASE_URL = "http://127.0.0.1:2024"
GRAPH_ID = "chinook_support"
HELENA, RICHARD = 6, 26
TOKENS = {HELENA: "demo-helena", RICHARD: "demo-richard"}


def _server_is_up() -> bool:
    try:
        requests.get(f"{BASE_URL}/ok", timeout=2).raise_for_status()
    except requests.RequestException:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _server_is_up(), reason="needs `langgraph dev` on port 2024"
)


def _auth(customer_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[customer_id]}"}


@pytest.fixture
def helenas_thread() -> str:
    response = requests.post(f"{BASE_URL}/threads", json={}, headers=_auth(HELENA))
    response.raise_for_status()
    return response.json()["thread_id"]


def test_a_request_without_a_credential_is_refused() -> None:
    assert requests.post(f"{BASE_URL}/threads", json={}).status_code == 401


def test_the_server_stamps_the_owner_rather_than_trusting_the_caller(
    helenas_thread: str,
) -> None:
    thread = requests.get(
        f"{BASE_URL}/threads/{helenas_thread}", headers=_auth(HELENA)
    ).json()
    assert thread["metadata"]["owner"] == f"customer:{HELENA}"


def test_another_customer_cannot_see_the_thread(helenas_thread: str) -> None:
    response = requests.get(
        f"{BASE_URL}/threads/{helenas_thread}", headers=_auth(RICHARD)
    )
    # 404 rather than 403: a filter makes the thread invisible, so a caller
    # cannot use the error to confirm that someone else's thread exists.
    assert response.status_code == 404


def test_another_customer_cannot_resume_the_thread(helenas_thread: str) -> None:
    """The checkpoint-resume attack, refused at the server.

    Helena's invoices are already in this thread's message history. If the run
    were created, they would be loaded into a turn running as Richard, and no
    amount of query scoping below would notice — the data layer is never asked
    a question it could refuse.
    """
    response = requests.post(
        f"{BASE_URL}/threads/{helenas_thread}/runs",
        json={
            "assistant_id": GRAPH_ID,
            "input": {"messages": [{"role": "user", "content": "what did I buy?"}]},
            "context": {"customer_id": RICHARD},
        },
        headers=_auth(RICHARD),
    )
    assert response.status_code == 404


def test_the_refused_resume_creates_no_run(helenas_thread: str) -> None:
    """Ordering, not just outcome.

    A denial that happened after the run was created would still return an
    error to Richard while having already resolved the thread and read its
    checkpoint. The absence of a run is what makes "before the checkpoint
    loads" a fact about this system rather than a sentence in a document.
    """
    requests.post(
        f"{BASE_URL}/threads/{helenas_thread}/runs",
        json={
            "assistant_id": GRAPH_ID,
            "input": {"messages": [{"role": "user", "content": "what did I buy?"}]},
            "context": {"customer_id": RICHARD},
        },
        headers=_auth(RICHARD),
    )
    runs = requests.get(
        f"{BASE_URL}/threads/{helenas_thread}/runs", headers=_auth(HELENA)
    ).json()
    assert runs == []


def test_studio_is_exempt_and_that_is_why_the_gateway_stays() -> None:
    """The finding that decides where the boundary can live.

    Studio authenticates as the developer, not as a customer, and is exempt
    from custom auth by default. Closing the exemption with
    ``disable_studio_auth`` was tried: it returns 401 to Studio itself, because
    Studio has no way to present a bearer token. So the customer identity in
    the Studio demo is configuration, and something above the server has to
    bind it to a conversation. See docs/decisions.md ADR-022.

    Unlike the rest of this module, this one also passes with no auth module at
    all — "allowed" is the answer either way. It documents an exemption rather
    than guarding a property, and is written down because the exemption is the
    reason the gateway survives.
    """
    response = requests.post(
        f"{BASE_URL}/threads", json={}, headers={"x-auth-scheme": "langsmith"}
    )
    assert response.status_code == 200

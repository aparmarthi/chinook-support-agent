"""Probe what the Agent Server's own authorization actually enforces.

Answers the question the architecture write-up got wrong by reasoning: is
``SupportGateway`` doing work the server would have done, and does that work
survive a caller who never goes through the gateway?

Every assertion is about a live server on :data:`BASE_URL`, started with
``langgraph dev`` and the ``auth`` key present in ``langgraph.json``. Run it
against a server started *without* that key and the denials become 200s, which
is the mutation check that keeps this honest.

Usage:
    python scripts/probe_native_auth.py
"""

from __future__ import annotations

import sys
from typing import Any

import requests

BASE_URL = "http://127.0.0.1:2024"
GRAPH_ID = "chinook_support"

HELENA, RICHARD = 6, 26
TOKENS = {HELENA: "demo-helena", RICHARD: "demo-richard"}

results: list[tuple[str, bool, str]] = []


def _headers(customer_id: int | None) -> dict[str, str]:
    if customer_id is None:
        return {}
    return {"Authorization": f"Bearer {TOKENS[customer_id]}"}


def check(name: str, passed: bool, detail: str) -> None:
    results.append((name, passed, detail))
    print(f"{'PASS' if passed else 'FAIL'}  {name}\n      {detail}")


def create_thread(customer_id: int) -> dict[str, Any]:
    r = requests.post(f"{BASE_URL}/threads", json={}, headers=_headers(customer_id))
    r.raise_for_status()
    return r.json()


def main() -> None:
    # 1. Authentication: no credential at all.
    r = requests.post(f"{BASE_URL}/threads", json={})
    check(
        "unauthenticated request is rejected",
        r.status_code == 401,
        f"POST /threads without Authorization -> {r.status_code}",
    )

    # 2. Authentication: a credential the server does not know.
    r = requests.post(
        f"{BASE_URL}/threads",
        json={},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    check(
        "unknown credential is rejected",
        r.status_code == 401,
        f"POST /threads with a bogus bearer token -> {r.status_code}",
    )

    # 3. Ownership is stamped by the server, not supplied by the caller.
    thread = create_thread(HELENA)
    thread_id = thread["thread_id"]
    owner = thread.get("metadata", {}).get("owner")
    check(
        "thread is stamped with its creator",
        owner == f"customer:{HELENA}",
        f"thread {thread_id[:8]} metadata.owner = {owner!r}",
    )

    # 4. The owner can read their own thread.
    r = requests.get(f"{BASE_URL}/threads/{thread_id}", headers=_headers(HELENA))
    check(
        "owner can read their own thread",
        r.status_code == 200,
        f"GET /threads/{{helena's}} as Helena -> {r.status_code}",
    )

    # 5. THE ONE THAT MATTERS: another customer cannot read it.
    r = requests.get(f"{BASE_URL}/threads/{thread_id}", headers=_headers(RICHARD))
    check(
        "cross-tenant read is denied",
        r.status_code in (403, 404),
        f"GET /threads/{{helena's}} as Richard -> {r.status_code} "
        f"({'invisible' if r.status_code == 404 else 'forbidden'})",
    )

    # 6. And cannot start a run on it. This is the checkpoint-resume attack:
    #    if it were allowed, Helena's history would be loaded into the run.
    r = requests.post(
        f"{BASE_URL}/threads/{thread_id}/runs",
        json={
            "assistant_id": GRAPH_ID,
            "input": {"messages": [{"role": "user", "content": "what did I buy?"}]},
            "context": {"customer_id": RICHARD},
        },
        headers=_headers(RICHARD),
    )
    check(
        "cross-tenant run creation is denied",
        r.status_code in (403, 404),
        f"POST /threads/{{helena's}}/runs as Richard -> {r.status_code}",
    )

    # 7. The denial has to happen before a run exists. If a run row was created
    #    the checkpoint was already resolved, and the check came too late.
    r = requests.get(f"{BASE_URL}/threads/{thread_id}/runs", headers=_headers(HELENA))
    runs = r.json() if r.status_code == 200 else []
    check(
        "no run was created by the denied attempt",
        len(runs) == 0,
        f"{len(runs)} run(s) on Helena's thread after Richard's attempt",
    )

    # 8. Studio is exempt by default — the finding that decides the demo.
    r = requests.post(f"{BASE_URL}/threads", json={}, headers={"x-auth-scheme": "langsmith"})
    check(
        "Studio's request path is exempt from custom auth",
        r.status_code == 200,
        f"POST /threads as Studio (x-auth-scheme: langsmith) -> {r.status_code}; "
        "this is why the customer identity cannot come from the credential in Studio",
    )

    failed = [name for name, passed, _ in results if not passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("failed: " + ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()

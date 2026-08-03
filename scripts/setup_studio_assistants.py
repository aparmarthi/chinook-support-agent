"""Create named Studio assistants with a customer identity already attached.

Studio publishes `customer_id` as required but does not enforce it before a
run, and the resulting failure is a constructor `TypeError` deep in the
framework rather than "fill in the context panel" (see FRICTION_LOG). That is
survivable while developing and unacceptable while presenting.

Binding the identity to an assistant removes the live-demo failure mode and
makes switching customers a dropdown rather than a form. It also reads better:
"Helena" and "Richard" are two authenticated sessions, which is what the
context field is standing in for anyway.

Assistants live in the dev server's store, so re-run this after restarting
`langgraph dev`. It is idempotent.

    python scripts/setup_studio_assistants.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.db import CustomerRepository  # noqa: E402

BASE_URL = "http://127.0.0.1:2024"
GRAPH_ID = "chinook_support"
ASSISTANT_NAMESPACE = uuid.UUID("6f0d4f52-0f5a-4b3e-9a1f-2c7d5e8a1b40")

# Helena has the November invoice the refund walkthrough uses. Richard is the
# second tenant, for demonstrating that a thread cannot cross between them.
DEMO_CUSTOMERS = (6, 26)


def assistant_id_for(customer_id: int) -> str:
    """Stable id derived from the customer, so re-runs update instead of add.

    Letting the server mint an id makes every run create another assistant, and
    a dropdown with two identical "Helena" entries — one of them stale — is a
    bad thing to meet during a demo.
    """
    return str(uuid.uuid5(ASSISTANT_NAMESPACE, f"chinook-support-customer-{customer_id}"))


def upsert_assistant(customer_id: int) -> str:
    """Create or update an assistant pinned to one customer. Returns its name."""
    profile = CustomerRepository(customer_id).get_profile()
    if profile is None:
        raise SystemExit(f"customer {customer_id} not found — run setup_data.py first")

    name = f"{profile.full_name} (customer {customer_id})"
    assistant_id = assistant_id_for(customer_id)
    response = requests.post(
        f"{BASE_URL}/assistants",
        json={
            "assistant_id": assistant_id,
            "graph_id": GRAPH_ID,
            "name": name,
            "context": {"customer_id": customer_id},
            "if_exists": "do_nothing",
        },
        timeout=30,
    )
    response.raise_for_status()

    # `do_nothing` returns the existing record untouched, so an assistant left
    # over from an earlier run could still be carrying the wrong identity.
    requests.patch(
        f"{BASE_URL}/assistants/{assistant_id}",
        json={"name": name, "context": {"customer_id": customer_id}},
        timeout=30,
    ).raise_for_status()
    return name


def _stale_demo_assistants(keep: set[str]) -> list[dict]:
    """Earlier server-minted copies of the demo assistants.

    Matched on the "(customer N)" naming this script owns, so the default
    `chinook_support` assistant and anything created by hand is left alone.
    """
    everything = requests.post(
        f"{BASE_URL}/assistants/search", json={"limit": 100}, timeout=30
    ).json()
    return [
        a
        for a in everything
        if "(customer " in (a.get("name") or "") and a["assistant_id"] not in keep
    ]


def main() -> None:
    try:
        requests.get(f"{BASE_URL}/ok", timeout=5).raise_for_status()
    except requests.RequestException:
        raise SystemExit(f"no dev server at {BASE_URL} — start `langgraph dev` first")

    keep = {assistant_id_for(cid) for cid in DEMO_CUSTOMERS}
    for customer_id in DEMO_CUSTOMERS:
        print(f"  ready: {upsert_assistant(customer_id)}")

    for stale in _stale_demo_assistants(keep):
        requests.delete(f"{BASE_URL}/assistants/{stale['assistant_id']}", timeout=30)
        print(f"  removed duplicate: {stale['name']}")

    print("\nPick one from the assistant dropdown in Studio. No context panel needed.")


if __name__ == "__main__":
    main()

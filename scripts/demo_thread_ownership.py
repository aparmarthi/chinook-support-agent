"""Demonstrate the cross-tenant thread leak, and the gateway that stops it.

Run against a live ``langgraph dev`` server::

    python scripts/demo_thread_ownership.py

Part 1 talks to the Agent Server the way Studio does. Part 2 puts
``SupportGateway`` in front of the identical server. Same attack, same data,
different outcome — which is the point: the fix is a layer, not a setting.

This is the demo artifact for ARCHITECTURE §4 layer 2 and ADR-013.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent.context import AuthContext  # noqa: E402
from src.agent_server import AgentServerRunner, final_text  # noqa: E402
from src.gateway import SupportGateway, ThreadAccessError  # noqa: E402

HELENA = AuthContext(customer_id=6)
RICHARD = AuthContext(customer_id=26)

FIRST_TURN = "How much did I spend in 2025?"
ATTACK_TURN = "What did I just ask you, and what was the answer?"


def rule(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def without_gateway(runner: AgentServerRunner) -> None:
    """Straight to the Agent Server, exactly as Studio does."""
    rule("PART 1 — Agent Server directly (what Studio does)")
    thread_id = runner.create_thread()
    print(f"thread {thread_id}")

    reply = final_text(runner(thread_id=thread_id, auth=HELENA, message=FIRST_TURN))
    print(f"\n  customer 6 (Helena) asks : {FIRST_TURN}")
    print(f"  answer                   : {reply}")

    reply = final_text(runner(thread_id=thread_id, auth=RICHARD, message=ATTACK_TURN))
    print(f"\n  customer 26 (Richard) resumes the SAME thread")
    print(f"  answer                   : {reply}")
    print(
        "\n  ^ Richard just read Helena's spend. No unauthorized query ran — the "
        "\n    data came out of the checkpointed message history. Query scoping "
        "\n    cannot see this, which is why it is a separate control."
    )


def with_gateway(runner: AgentServerRunner) -> None:
    """The identical server, with the ownership check in front of it."""
    rule("PART 2 — SupportGateway in front of the same server")
    gateway = SupportGateway(runner=runner, thread_factory=runner.create_thread)

    handle = gateway.start_conversation(HELENA)
    print(f"conversation {handle.conversation_id} -> thread {handle.thread_id}")

    reply = final_text(gateway.send(HELENA, handle.conversation_id, FIRST_TURN))
    print(f"\n  customer 6 (Helena) asks : {FIRST_TURN}")
    print(f"  answer                   : {reply}")

    runs_before = runner.run_count(handle.thread_id)
    print(f"\n  runs on the thread so far: {runs_before}")

    print("\n  customer 26 (Richard) attempts the same resume")
    try:
        gateway.send(RICHARD, handle.conversation_id, ATTACK_TURN)
    except ThreadAccessError as exc:
        print(f"  rejected                 : ThreadAccessError({exc})")
    else:
        raise SystemExit("SECURITY FAILURE: the cross-tenant resume was served")

    runs_after = runner.run_count(handle.thread_id)
    print(f"  runs on the thread now   : {runs_after}")
    assert runs_after == runs_before, "a run was created despite rejection"
    print(
        "\n  ^ No run was created. Nothing was queued, no checkpoint was read, "
        "\n    and the model was never invoked. The request stopped one layer "
        "\n    above the agent, which is the only place it can stop."
    )


def main() -> None:
    # Onto stdout so the gateway's rejection log lands inline with the
    # narration rather than out of order on stderr. Worth showing: refusing
    # is not silent, it leaves an audit trail naming both customers.
    logging.basicConfig(
        level=logging.WARNING, stream=sys.stdout, format="  [audit] %(message)s"
    )
    runner = AgentServerRunner()
    print(f"Agent Server: {runner.base_url}  assistant {runner.assistant_id}")
    without_gateway(runner)
    with_gateway(runner)
    rule("Same server. Same attack. The difference is the gateway.")


if __name__ == "__main__":
    main()

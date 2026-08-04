"""The refund write path (W3), and the approval gate in front of it.

Four properties, stated as counts of rows in `support.db` because that is the
only thing a customer would actually feel:

1. Pending approval writes nothing.
2. Rejection writes nothing, ever.
3. Approval writes exactly one row.
4. Resuming the same approval twice still writes exactly one row.

Property 4 is the one that is easy to skip and expensive to get wrong. An
approval that a reconnect can replay is a refund the customer can get twice.
"""

from __future__ import annotations

import pytest
from conftest import ScriptedChatModel, refund_tool_call
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.agent.context import AuthContext
from src.agent.graph import build_agent
from src.agent.tools.support import create_refund_request, escalate_to_human
from src.data.db import CustomerRepository

HELENA = AuthContext(customer_id=6)
RICHARD = AuthContext(customer_id=26)

# A real line on Helena's invoice #404, confirmed in the Chinook data.
HELENA_LINE = 2201
# A line belonging to another customer.
FOREIGN_LINE = 1


@pytest.fixture
def agent_and_saver():
    """An agent whose tool calls are scripted, so the test controls the turn."""

    def _build(*responses: AIMessage):
        saver = InMemorySaver()
        model = ScriptedChatModel(responses=list(responses))
        return build_agent(model=model, checkpointer=saver), saver

    return _build


@pytest.fixture
def count_write_attempts(monkeypatch: pytest.MonkeyPatch):
    """Count calls that reached the repository, not rows that survived.

    Row counts alone cannot distinguish "the key stopped a duplicate" from
    "nothing ever tried to write twice". These tests need to tell those apart.
    """
    attempts: list[str] = []
    original = CustomerRepository.create_refund_request

    def spy(self, invoice_line_id, reason, idempotency_key):
        attempts.append(idempotency_key)
        return original(self, invoice_line_id, reason, idempotency_key)

    monkeypatch.setattr(CustomerRepository, "create_refund_request", spy)
    return lambda: len(attempts)


def _refund_turn(text: str = "Refund that, I bought it by accident."):
    return {"messages": [{"role": "user", "content": text}]}


def _script(line_id: int = HELENA_LINE, call_id: str = "call_refund_1"):
    return (
        refund_tool_call(line_id, "Bought by accident", call_id),
        AIMessage(content="I've filed that for you."),
    )


# ----------------------------------------------------------------------
# 1. Nothing is written while approval is pending


def test_pending_approval_writes_nothing(temp_support_db, agent_and_saver) -> None:
    agent, _ = agent_and_saver(*_script())
    config = {"configurable": {"thread_id": "pending"}}

    result = agent.invoke(_refund_turn(), config=config, context=HELENA)

    assert "__interrupt__" in result, "the refund tool ran without asking anyone"
    assert CustomerRepository(6).count_refund_requests() == 0


def test_the_approval_card_shows_the_charge_not_the_arguments(
    temp_support_db, agent_and_saver
) -> None:
    """A reviewer who has to go look up the line ID will stop looking."""
    agent, _ = agent_and_saver(*_script())
    config = {"configurable": {"thread_id": "card"}}

    result = agent.invoke(_refund_turn(), config=config, context=HELENA)
    description = result["__interrupt__"][0].value["action_requests"][0]["description"]

    assert "So Cruel" in description
    assert "$0.99" in description
    assert "does not move money" in description


# ----------------------------------------------------------------------
# 2. Rejection writes nothing


def test_rejection_writes_nothing(temp_support_db, agent_and_saver) -> None:
    agent, _ = agent_and_saver(*_script())
    config = {"configurable": {"thread_id": "rejected"}}
    agent.invoke(_refund_turn(), config=config, context=HELENA)

    result = agent.invoke(
        Command(resume={"decisions": [{"type": "reject", "message": "Out of policy."}]}),
        config=config,
        context=HELENA,
    )

    assert CustomerRepository(6).count_refund_requests() == 0
    assert "__interrupt__" not in result, "rejection left the turn hanging"


def test_rejection_tells_the_model_why(temp_support_db, agent_and_saver) -> None:
    """The model has to learn the outcome, or it will cheerfully try again."""
    agent, _ = agent_and_saver(*_script())
    config = {"configurable": {"thread_id": "rejected-msg"}}
    agent.invoke(_refund_turn(), config=config, context=HELENA)

    result = agent.invoke(
        Command(resume={"decisions": [{"type": "reject", "message": "Out of policy."}]}),
        config=config,
        context=HELENA,
    )

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert any("Out of policy." in str(m.content) for m in tool_messages)


# ----------------------------------------------------------------------
# 3 & 4. Approval writes exactly one row, however many times it is resumed


def test_approval_writes_exactly_one_row(temp_support_db, agent_and_saver) -> None:
    agent, _ = agent_and_saver(*_script())
    config = {"configurable": {"thread_id": "approved"}}
    agent.invoke(_refund_turn(), config=config, context=HELENA)

    agent.invoke(
        Command(resume={"decisions": [{"type": "approve"}]}),
        config=config,
        context=HELENA,
    )

    assert CustomerRepository(6).count_refund_requests() == 1


def test_resuming_a_finished_approval_never_reaches_the_tool(
    temp_support_db, agent_and_saver, count_write_attempts
) -> None:
    """The first defense is the checkpoint: a finished turn has nothing to resume.

    This is worth pinning because it is the layer people assume covers
    everything. It does not — see the replay test below — but it does cover the
    ordinary double click, and it covers it without touching the database.
    """
    agent, _ = agent_and_saver(*_script())
    config = {"configurable": {"thread_id": "double"}}
    agent.invoke(_refund_turn(), config=config, context=HELENA)
    resume = Command(resume={"decisions": [{"type": "approve"}]})

    agent.invoke(resume, config=config, context=HELENA)
    agent.invoke(resume, config=config, context=HELENA)

    assert count_write_attempts() == 1, "the second resume re-entered the tool"
    assert CustomerRepository(6).count_refund_requests() == 1


def test_replaying_the_approval_checkpoint_writes_only_one_row(
    temp_support_db, agent_and_saver, count_write_attempts
) -> None:
    """The case the checkpoint does not cover, and the reason the key exists.

    Resuming from the checkpoint taken *before* the write — a crash between the
    insert and the checkpoint commit, a duplicate delivery, an operator
    replaying a thread — genuinely re-enters the tool. The assertion that makes
    this test worth having is the pair: two attempts reached the database and
    one row came out. Drop the idempotency key and the row count becomes two
    while the attempt count stays the same.
    """
    agent, saver = agent_and_saver(*_script())
    config = {"configurable": {"thread_id": "replay"}}
    agent.invoke(_refund_turn(), config=config, context=HELENA)
    pre_write_checkpoint = saver.get_tuple(config).config
    resume = Command(resume={"decisions": [{"type": "approve"}]})

    agent.invoke(resume, config=pre_write_checkpoint, context=HELENA)
    agent.invoke(resume, config=pre_write_checkpoint, context=HELENA)

    assert count_write_attempts() == 2, "the replay never re-entered the tool"
    assert CustomerRepository(6).count_refund_requests() == 1


def test_the_replay_tells_the_customer_no_duplicate_was_made(
    temp_support_db, agent_and_saver
) -> None:
    """Silently swallowing the second attempt would read as a failed refund."""
    agent, saver = agent_and_saver(*_script())
    config = {"configurable": {"thread_id": "replay-msg"}}
    agent.invoke(_refund_turn(), config=config, context=HELENA)
    pre_write_checkpoint = saver.get_tuple(config).config
    resume = Command(resume={"decisions": [{"type": "approve"}]})

    agent.invoke(resume, config=pre_write_checkpoint, context=HELENA)
    result = agent.invoke(resume, config=pre_write_checkpoint, context=HELENA)

    last_tool_message = [
        m for m in result["messages"] if isinstance(m, ToolMessage)
    ][-1]
    assert "already filed" in str(last_tool_message.content)


def test_idempotency_is_enforced_by_the_database_not_by_luck(
    temp_support_db,
) -> None:
    """The same key twice collides at the constraint, below any agent logic."""
    repo = CustomerRepository(6)
    key = "thread-abc:call_refund_1"

    first = repo.create_refund_request(HELENA_LINE, "Accident", key)
    second = repo.create_refund_request(HELENA_LINE, "Accident", key)

    assert first is not None and first.created is True
    assert second is not None and second.created is False
    assert second.refund_request_id == first.refund_request_id
    assert repo.count_refund_requests() == 1


def test_a_different_tool_call_can_still_file_a_second_refund(
    temp_support_db,
) -> None:
    """Idempotency must not become a silent one-refund-per-lifetime rule."""
    repo = CustomerRepository(6)

    repo.create_refund_request(HELENA_LINE, "Accident", "thread-abc:call_1")
    repo.create_refund_request(HELENA_LINE, "Changed my mind", "thread-abc:call_2")

    assert repo.count_refund_requests() == 2


# ----------------------------------------------------------------------
# Tenancy on the write path


def test_cannot_refund_another_customers_purchase(temp_support_db, run_tool) -> None:
    output = run_tool(
        [create_refund_request],
        "create_refund_request",
        {"invoice_line_id": FOREIGN_LINE, "reason": "Not mine"},
        RICHARD.customer_id,
    )

    assert "No purchase with that line number" in output
    assert CustomerRepository(26).count_refund_requests() == 0
    assert CustomerRepository(6).count_refund_requests() == 0


def test_a_missing_line_and_a_foreign_line_look_identical(
    temp_support_db, run_tool
) -> None:
    """Otherwise the error message becomes a way to enumerate other customers."""
    foreign = run_tool(
        [create_refund_request],
        "create_refund_request",
        {"invoice_line_id": FOREIGN_LINE, "reason": "Not mine"},
        RICHARD.customer_id,
    )
    nonexistent = run_tool(
        [create_refund_request],
        "create_refund_request",
        {"invoice_line_id": 99_999_999, "reason": "Not mine"},
        RICHARD.customer_id,
    )

    assert foreign == nonexistent


# ----------------------------------------------------------------------
# Handoff — the tool that deliberately does nothing


def test_escalation_is_not_gated_by_approval(temp_support_db) -> None:
    """Only the write is gated.

    Putting an approval in front of a read-only handoff would double the
    reviewer's queue with items that cannot cause harm, which is how the one
    item that can gets approved without being read.
    """
    escalation = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "escalate_to_human",
                "args": {"summary": "Duplicate charge.", "urgency": "high"},
                "id": "call_escalate_1",
                "type": "tool_call",
            }
        ],
    )
    agent = build_agent(
        model=ScriptedChatModel(
            responses=[escalation, AIMessage(content="Handing you over.")]
        ),
        checkpointer=InMemorySaver(),
    )

    result = agent.invoke(
        {"messages": [{"role": "user", "content": "I want a person."}]},
        config={"configurable": {"thread_id": "escalate"}},
        context=HELENA,
    )

    assert "__interrupt__" not in result
    assert any(isinstance(m, ToolMessage) for m in result["messages"])


def test_escalation_names_the_customers_own_rep(temp_support_db, run_tool) -> None:
    output = run_tool(
        [escalate_to_human],
        "escalate_to_human",
        {"summary": "Charged twice for the same album.", "urgency": "high"},
        HELENA.customer_id,
    )

    profile = CustomerRepository(6).get_profile()
    assert profile.support_rep_name in output
    assert "Helena" in output


def test_escalation_queues_a_durable_handoff(temp_support_db, run_tool) -> None:
    """The claim in the reply has to have a row behind it.

    This tool used to return prose and write nothing, which made the
    escalation eval weaker than it looked: it graded "a tool was called", so
    "I've handed this to Steve" passed while nothing existed anywhere. The
    fabricated-handoff story in the demo is about exactly that gap, one level
    up, so leaving it here would have been the same bug in the fix.
    """
    output = run_tool(
        [escalate_to_human],
        "escalate_to_human",
        {"summary": "Please refund everything.", "urgency": "high"},
        HELENA.customer_id,
    )

    repo = CustomerRepository(6)
    assert repo.count_handoff_requests() == 1
    assert "queued" in output


def test_escalation_still_files_no_refund(temp_support_db, run_tool) -> None:
    """Queuing a handoff is not a money-touching write, so it stays ungated."""
    run_tool(
        [escalate_to_human],
        "escalate_to_human",
        {"summary": "Please refund everything.", "urgency": "high"},
        HELENA.customer_id,
    )

    assert CustomerRepository(6).count_refund_requests() == 0


def test_a_retried_escalation_does_not_queue_twice(temp_support_db, run_tool) -> None:
    """Same idempotency discipline as refunds, for the same reason."""
    args = {"summary": "Charged twice.", "urgency": "normal"}
    first = run_tool(
        [escalate_to_human], "escalate_to_human", args, HELENA.customer_id
    )
    second = run_tool(
        [escalate_to_human], "escalate_to_human", args, HELENA.customer_id
    )

    assert CustomerRepository(6).count_handoff_requests() == 1
    assert "No duplicate was created" in second
    assert "No duplicate" not in first


def test_the_model_never_sees_a_customer_id_field() -> None:
    assert set(create_refund_request.args) == {"invoice_line_id", "reason"}
    assert set(escalate_to_human.args) == {"summary", "urgency"}

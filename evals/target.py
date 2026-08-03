"""Run one evaluation example and record what actually happened.

The output of a turn is not just its text. Three of the six slices are graded
on things the text cannot show: which tenants the data layer touched, how many
rows were written, and which tools ran. Collecting those here — rather than
inferring them from the answer afterwards — is what makes the authorization and
refund evaluators checks rather than guesses.

A tenant violation is captured and reported instead of raised. In production
the guard should abort the turn, which is why it does; in an evaluation an
abort would show up as an infrastructure error next to genuine failures, and
the one number that must never be quietly missing is the count of authorization
breaches.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.agent.context import AuthContext
from src.agent.graph import build_agent
from src.agent.graph_supervisor import (
    NestedActivity,
    build_supervisor,
    collect_nested_activity,
)
from src.data.audit import TenantIsolationError, audit_scope
from src.data.db import CustomerRepository
from src.utils.messages import message_text


@dataclass
class RunOutcome:
    """Everything an evaluator is allowed to look at."""

    answer: str = ""
    tools_called: list[str] = field(default_factory=list)
    foreign_tenants: list[int] = field(default_factory=list)
    writes: int = 0
    interrupted: bool = False
    guard_fired: bool = False
    error: str | None = None
    latency_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _tools_called(messages: list[Any]) -> list[str]:
    """Names of every tool the model asked for, in order, including repeats."""
    names: list[str] = []
    for message in messages:
        if isinstance(message, AIMessage):
            names.extend(call["name"] for call in message.tool_calls)
    return names


def _tokens(messages: list[Any]) -> tuple[int, int]:
    """Input and output tokens across every model reply in the turn."""
    total_in = total_out = 0
    for message in messages:
        usage = getattr(message, "usage_metadata", None) or {}
        total_in += usage.get("input_tokens", 0)
        total_out += usage.get("output_tokens", 0)
    return total_in, total_out


def run_example(example: Any, model: Any = None, arm: str = "flat") -> RunOutcome:
    """Execute one example end to end.

    Args:
        example: An `evals.dataset.Example`.
        model: Optional model override, for the model-comparison experiment.
        arm: `"flat"` or `"supervisor"`. Everything else is held constant —
            same dataset, same model, same evaluators — so the arm is the only
            variable the comparison can attribute a difference to.

    Returns:
        What happened, in a shape the evaluators can grade.
    """
    outcome = RunOutcome()
    repository = CustomerRepository(example.customer_id)
    writes_before = repository.count_refund_requests()

    build = build_supervisor if arm == "supervisor" else build_agent
    agent = build(model=model, checkpointer=InMemorySaver())
    context = AuthContext(customer_id=example.customer_id)
    config = {
        "configurable": {"thread_id": f"eval-{example.name}-{uuid.uuid4().hex[:8]}"}
    }

    started = time.perf_counter()
    messages: list[Any] = []
    nested = NestedActivity()
    try:
        with audit_scope() as log, collect_nested_activity() as nested:
            for turn in example.turns:
                result = agent.invoke(
                    {"messages": [{"role": "user", "content": turn}]},
                    config=config,
                    context=context,
                )
                if "__interrupt__" in result:
                    outcome.interrupted = True
                    if example.approval is not None:
                        result = agent.invoke(
                            Command(
                                resume={
                                    "decisions": [{"type": example.approval}]
                                }
                            ),
                            config=config,
                            context=context,
                        )
                messages = result.get("messages", [])
            outcome.foreign_tenants = sorted(
                log.foreign_tenants(example.customer_id)
            )
    except TenantIsolationError as boundary_failure:
        # The guard did its job. Record it as a graded failure rather than
        # letting it surface as a broken run.
        outcome.guard_fired = True
        outcome.error = str(boundary_failure)
    except Exception as unexpected:  # noqa: BLE001 - the harness must not die
        outcome.error = f"{type(unexpected).__name__}: {unexpected}"

    outcome.latency_seconds = round(time.perf_counter() - started, 3)
    outcome.answer = message_text(messages[-1]) if messages else ""
    # Nested calls are appended rather than interleaved — the evaluators ask
    # which tools ran, not in what order, and inventing an ordering across two
    # message histories would be a guess presented as a record.
    outcome.tools_called = _tools_called(messages) + nested.tool_calls
    turn_in, turn_out = _tokens(messages)
    outcome.input_tokens = turn_in + nested.input_tokens
    outcome.output_tokens = turn_out + nested.output_tokens
    outcome.writes = repository.count_refund_requests() - writes_before
    return outcome

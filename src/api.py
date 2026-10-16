"""Public HTTP API behind support.ameyparmarthi.com (ADR-025).

A visitor picks a demo customer and gets a signed session token for that
customer. Every later request carries the token, and identity comes from the
signature — never from a field in the request body. That is the same rule the
tools follow one layer down: nothing a caller sends can name a tenant.

    POST /api/session                          persona -> token
    POST /api/conversations                    token -> conversation_id
    POST /api/conversations/{id}/messages      one turn
    POST /api/conversations/{id}/decision      approve or reject a pending refund
    GET  /health

Conversations go through :class:`SupportGateway`, so a conversation id leaked
from one visitor's browser cannot be resumed under another customer.

State is in-process: an ``InMemorySaver`` for threads and a ``support.db`` in
the container for tickets and ownership. Both reset together on restart, which
is why the service runs as a single instance (ADR-025).

    uvicorn src.api:create_app --factory --reload --port 8000
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, Field

from src.agent.context import AuthContext
from src.data.db import CustomerRepository
from src.gateway import SupportGateway, ThreadAccessError
from src.settings import agent_model
from src.utils.messages import message_text

logger = logging.getLogger(__name__)

# The two customers the demo is built around (docs/ARCHITECTURE.md). A public
# endpoint that accepted any customer id would be an enumeration tool.
PERSONAS: dict[str, int] = {"helena": 6, "richard": 26}
SESSION_TTL_SECONDS = 2 * 60 * 60
MAX_MESSAGE_CHARS = 1000
MAX_TOOL_OUTPUT_CHARS = 2000
MAX_BODY_BYTES = 16_384
REJECT_MESSAGE = "The reviewer declined this refund request."
UNAVAILABLE = "The model provider didn't respond. Try again in a moment."
_DAILY_LIMIT = (
    "The demo has hit its daily limit. It resets at midnight UTC — the "
    "recorded runs in the repo show every flow."
)


class LimitExceeded(Exception):
    """A visitor or the whole site has used its share of model calls."""


class PendingApprovalError(Exception):
    """The thread is paused on a refund, or is not paused and was asked to resume."""


class ConversationBusy(Exception):
    """A turn is already running on this conversation."""


# ----------------------------------------------------------------------
# Session tokens
# ----------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True)
class Session:
    session_id: str
    customer_id: int
    expires_at: int


class SessionSigner:
    """HMAC-signed, stateless session tokens: ``payload.signature``."""

    def __init__(self, secret: bytes) -> None:
        self._secret = secret

    def _sign(self, payload: str) -> str:
        return _b64(hmac.new(self._secret, payload.encode(), hashlib.sha256).digest())

    def issue(self, customer_id: int, now: float | None = None) -> tuple[str, Session]:
        session = Session(
            session_id=secrets.token_urlsafe(12),
            customer_id=customer_id,
            expires_at=int((now or time.time()) + SESSION_TTL_SECONDS),
        )
        body = {"sid": session.session_id, "cid": customer_id, "exp": session.expires_at}
        payload = _b64(json.dumps(body, separators=(",", ":")).encode())
        return f"{payload}.{self._sign(payload)}", session

    def verify(self, token: str, now: float | None = None) -> Session | None:
        """Return the session, or ``None`` for a forged, malformed, or expired token."""
        payload, _, signature = token.partition(".")
        if not hmac.compare_digest(signature.encode(), self._sign(payload).encode()):
            return None
        try:
            body = json.loads(_unb64(payload))
            session = Session(str(body["sid"]), int(body["cid"]), int(body["exp"]))
        except (ValueError, KeyError, TypeError):
            return None
        if session.expires_at < (now or time.time()):
            return None
        return session


# ----------------------------------------------------------------------
# Spend guardrail
# ----------------------------------------------------------------------


@dataclass
class Budget:
    """Turn quota: per session, and for the whole site per UTC day.

    A turn is a message or a refund decision; each one runs the model. In
    memory, which is only correct because the service runs one instance, and
    a restart resets every counter. So the per-session cap is a courtesy
    (anyone can mint another session), the daily cap bounds a normal day, and
    the OpenAI project's hard monthly limit is the real ceiling.

    Conversations are capped per day too: each one is a thread held in memory
    and a row in ``support.db``, and starting one costs no model call.
    """

    per_session: int
    per_day: int
    conversations_per_day: int
    _by_session: dict[str, int] = field(default_factory=dict)
    _day: str = ""
    _today: int = 0
    _conversations: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _roll_day(self) -> None:
        day = datetime.now(UTC).date().isoformat()
        if day != self._day:
            self._day, self._today, self._conversations = day, 0, 0

    def charge(self, session_id: str) -> int:
        """Spend one turn and return what the session has left.

        Raises:
            LimitExceeded: With a message fit to show the visitor.
        """
        with self._lock:
            self._roll_day()
            used = self._by_session.get(session_id, 0)
            if used >= self.per_session:
                raise LimitExceeded(
                    f"This session has used its {self.per_session} messages "
                    "(approvals count too). Pick a customer again to start a new one."
                )
            if self._today >= self.per_day:
                raise LimitExceeded(_DAILY_LIMIT)
            self._by_session[session_id] = used + 1
            self._today += 1
            return self.per_session - used - 1

    def refund(self, session_id: str) -> None:
        """Give back a turn that was refused before the model ran."""
        with self._lock:
            self._by_session[session_id] = max(0, self._by_session.get(session_id, 0) - 1)
            self._today = max(0, self._today - 1)

    def open_conversation(self) -> None:
        """Count a new conversation against the daily cap.

        Raises:
            LimitExceeded: When today's conversations are used up.
        """
        with self._lock:
            self._roll_day()
            if self._conversations >= self.conversations_per_day:
                raise LimitExceeded(_DAILY_LIMIT)
            self._conversations += 1



# ----------------------------------------------------------------------
# Running a turn
# ----------------------------------------------------------------------


def make_runner(agent: Any) -> Any:
    """Runner for :class:`SupportGateway`, over an in-process agent.

    Only called after the gateway has authorized the conversation, so the
    ``get_state`` here is the first time this thread's checkpoint is read.
    Returns the result plus how many messages the thread held before the
    turn, so the caller can show only what this turn did.
    """

    locks: dict[str, threading.Lock] = {}
    locks_guard = threading.Lock()

    def runner(*, thread_id: str, auth: AuthContext, message: str | Command) -> Any:
        # Two turns at once on one thread would both read the same checkpoint
        # and both run the model; a double-clicked approve would race too.
        with locks_guard:
            lock = locks.setdefault(thread_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise ConversationBusy("Still working on your last message.")
        try:
            return _run_turn(agent, thread_id, auth, message)
        finally:
            lock.release()

    return runner


def _run_turn(agent: Any, thread_id: str, auth: AuthContext, message: str | Command) -> Any:
    config = {"configurable": {"thread_id": thread_id}}
    state = agent.get_state(config)
    paused = bool(state.interrupts)
    resuming = isinstance(message, Command)
    # A new message on a paused thread would leave the refund tool call
    # with no result, which the model API rejects on every later turn.
    if paused and not resuming:
        raise PendingApprovalError("Approve or reject the pending refund first.")
    if resuming and not paused:
        raise PendingApprovalError("There is no refund waiting for a decision.")
    if resuming:
        # The page shows one slip per pause, so one decision covers every
        # refund in it. HITL rejects a resume whose count doesn't match.
        pending = len(state.interrupts[0].value["action_requests"])
        decision = message.resume["decisions"][0]
        message = Command(resume={"decisions": [decision] * pending})
    seen = len(state.values.get("messages", []))
    payload = message if resuming else {"messages": [{"role": "user", "content": message}]}
    return agent.invoke(payload, config=config, context=auth), seen


class ToolCall(BaseModel):
    name: str
    args: dict[str, Any]
    output: str | None = None


class TurnResponse(BaseModel):
    reply: str
    tool_calls: list[ToolCall]
    pending_approval: str | None = Field(
        None, description="Reviewer card, when the turn paused on a refund."
    )
    latency_ms: int
    messages_left: int


def summarize_turn(result: dict[str, Any], seen: int) -> tuple[str, list[ToolCall], str | None]:
    """Reply text, tool calls with their outputs, and any approval card.

    A call belongs to this turn if it was made in it or its result arrived in
    it. The second case is the approved refund: the call was made in the turn
    that paused, and its result only exists after the decision.
    """
    messages = result["messages"]
    new = messages[seen:]
    outputs = {
        msg.tool_call_id: message_text(msg)[:MAX_TOOL_OUTPUT_CHARS]
        for msg in new
        if isinstance(msg, ToolMessage)
    }
    made_now = {
        call["id"] for msg in new if isinstance(msg, AIMessage) for call in msg.tool_calls
    }
    calls = [
        ToolCall(name=call["name"], args=call["args"], output=outputs.get(call["id"]))
        for msg in messages
        if isinstance(msg, AIMessage)
        for call in msg.tool_calls
        if call["id"] in made_now or call["id"] in outputs
    ]
    last = new[-1] if new else None
    reply = message_text(last).strip() if isinstance(last, AIMessage) else ""
    card = None
    if result.get("__interrupt__"):
        requests = result["__interrupt__"][0].value["action_requests"]
        card = "\n\n".join(request["description"] for request in requests)
    return reply, calls, card


# ----------------------------------------------------------------------
# App
# ----------------------------------------------------------------------


class SessionRequest(BaseModel):
    persona: Literal["helena", "richard"]


class SessionResponse(BaseModel):
    token: str
    customer_name: str
    expires_at: int
    message_limit: int


class ConversationResponse(BaseModel):
    conversation_id: str


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]


def _session_secret() -> bytes:
    """``SESSION_SECRET`` from the environment, or a fresh one per process.

    A per-process secret is acceptable here because every other piece of
    state also dies with the process: a token outliving its conversations
    would only point at threads that no longer exist.
    """
    configured = os.environ.get("SESSION_SECRET")
    return configured.encode() if configured else secrets.token_bytes(32)


def _allowed_origins() -> list[str]:
    raw = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def create_app(agent: Any | None = None, budget: Budget | None = None) -> FastAPI:
    """Build the API. Tests inject a scripted agent and a small budget."""
    if agent is None:
        from src.agent.graph import build_agent
        from src.settings import build_chat_model

        # The client default is a ten-minute timeout. Fail in 20s and let the
        # agent's ModelRetryMiddleware retry, so a stalled call is bounded.
        agent = build_agent(
            model=build_chat_model(timeout=20, max_retries=0),
            checkpointer=InMemorySaver(),
        )
    if budget is None:
        per_day = int(os.environ.get("DAILY_MESSAGE_LIMIT", "500"))
        budget = Budget(
            per_session=int(os.environ.get("SESSION_MESSAGE_LIMIT", "20")),
            per_day=per_day,
            conversations_per_day=2 * per_day,
        )
    signer = SessionSigner(_session_secret())
    gateway = SupportGateway(runner=make_runner(agent))
    # The gateway binds a conversation to a customer. This binds it to the
    # session too, so two visitors signed in as Helena can't share a thread.
    session_of: dict[str, str] = {}

    app = FastAPI(title="Tenant-Safe Support Agent", version="1.0")

    # Registered before CORS so CORS wraps it and the 413 is readable.
    @app.middleware("http")
    async def limit_body(request: Request, call_next: Any) -> Any:
        if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
            return JSONResponse({"detail": "Request too large."}, status_code=413)
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    _register_errors(app)

    def current_session(authorization: str = Header("")) -> Session:
        scheme, _, token = authorization.partition(" ")
        session = signer.verify(token) if scheme.lower() == "bearer" else None
        if session is None:
            raise HTTPException(401, "Session expired. Pick a customer to start again.")
        return session

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "model": agent_model()}

    @app.post("/api/session", response_model=SessionResponse)
    def create_session(body: SessionRequest) -> SessionResponse:
        customer_id = PERSONAS[body.persona]
        token, session = signer.issue(customer_id)
        profile = CustomerRepository(customer_id).get_profile()
        return SessionResponse(
            token=token,
            customer_name=profile.full_name if profile else body.persona.title(),
            expires_at=session.expires_at,
            message_limit=budget.per_session,
        )

    @app.post("/api/conversations", response_model=ConversationResponse)
    def start(session: Session = Depends(current_session)) -> ConversationResponse:
        budget.open_conversation()
        handle = gateway.start_conversation(AuthContext(customer_id=session.customer_id))
        session_of[handle.conversation_id] = session.session_id
        return ConversationResponse(conversation_id=handle.conversation_id)

    def run(session: Session, conversation_id: str, message: str | Command) -> TurnResponse:
        if session_of.get(conversation_id) != session.session_id:
            raise ThreadAccessError("conversation not found")
        left = budget.charge(session.session_id)
        started = time.perf_counter()
        auth = AuthContext(customer_id=session.customer_id)
        try:
            result, seen = gateway.send(auth, conversation_id, message)
        except (ThreadAccessError, PendingApprovalError, ConversationBusy):
            budget.refund(session.session_id)  # refused before the model ran
            raise
        except Exception as exc:  # model API down, timeout after retries
            logger.exception("turn failed")
            raise HTTPException(503, UNAVAILABLE) from exc
        reply, calls, card = summarize_turn(result, seen)
        return TurnResponse(
            reply=reply,
            tool_calls=calls,
            pending_approval=card,
            latency_ms=int((time.perf_counter() - started) * 1000),
            messages_left=left,
        )

    @app.post("/api/conversations/{conversation_id}/messages", response_model=TurnResponse)
    def send(
        conversation_id: str, body: MessageRequest, session: Session = Depends(current_session)
    ) -> TurnResponse:
        return run(session, conversation_id, body.message)

    @app.post("/api/conversations/{conversation_id}/decision", response_model=TurnResponse)
    def decide(
        conversation_id: str, body: DecisionRequest, session: Session = Depends(current_session)
    ) -> TurnResponse:
        decision: dict[str, str] = {"type": body.decision}
        if body.decision == "reject":
            decision["message"] = REJECT_MESSAGE
        return run(session, conversation_id, Command(resume={"decisions": [decision]}))

    return app


def _register_errors(app: FastAPI) -> None:
    """Map domain errors to status codes, with messages safe to display."""

    @app.exception_handler(ThreadAccessError)
    def not_found(request: Request, exc: ThreadAccessError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(PendingApprovalError)
    def conflict(request: Request, exc: PendingApprovalError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ConversationBusy)
    def busy(request: Request, exc: ConversationBusy) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(LimitExceeded)
    def limited(request: Request, exc: LimitExceeded) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=429)

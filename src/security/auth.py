"""Agent Server authentication and thread-level authorization.

This is the boundary ``SupportGateway`` reimplements by hand. The Agent Server
runs these handlers itself, outside the graph, before a run is created and
therefore before a checkpoint is read — which is the ordering the gateway's
docstring claims and the gateway can only deliver for callers that go through
it. Anything speaking the Agent Server protocol directly, Studio included,
bypasses the gateway but cannot bypass these.

Two handlers, in order::

    request ──▶ @auth.authenticate      credential ──▶ principal
                       │
                       ▼
                @auth.on.threads.*      principal owns this thread?
                       │                no ──▶ denied. No run, no checkpoint.
                       ▼
                run created ──▶ checkpoint loaded ──▶ graph executes

The principal here is a *customer*, not a human operator, because the tenant
this system isolates is the account whose invoices are in the thread. That is
also the reason Studio is special-cased rather than made to fit: a Studio
request authenticates the developer, and no developer is a customer. See
``docs/decisions.md`` ADR-022.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from langgraph_sdk import Auth
from langgraph_sdk.auth import is_studio_user

logger = logging.getLogger(__name__)

auth = Auth()

# Static tokens standing in for a verified session credential. A real
# deployment resolves the customer from a signed JWT; what matters
# architecturally is only that the server derives identity from something the
# caller cannot choose, which a bearer token it must already hold satisfies and
# a `customer_id` in the request body does not.
#
# The operator is not a customer. It exists because authentication applies to
# every route, including the ones that provision Studio assistants, so setup
# tooling needs an identity of its own rather than borrowing a customer's.
OPERATOR = "operator"

_DEMO_TOKENS: dict[str, str] = {
    os.getenv("DEMO_TOKEN_HELENA", "demo-helena"): "customer:6",
    os.getenv("DEMO_TOKEN_RICHARD", "demo-richard"): "customer:26",
    os.getenv("DEMO_TOKEN_OPERATOR", "demo-operator"): OPERATOR,
}

_PERMISSIONS: dict[str, list[str]] = {OPERATOR: ["assistants:write"]}


@auth.authenticate
async def authenticate(
    authorization: str | None = None, headers: dict[bytes, bytes] | None = None
) -> dict[str, Any]:
    """Resolve the bearer token to the customer it belongs to.

    Args:
        authorization: The ``Authorization`` header, if the client sent one.
        headers: Raw headers, used only as a fallback for clients that send the
            credential lowercased or under a different case.

    Returns:
        The authenticated principal, whose ``identity`` the authorization
        handlers below use as the thread owner.

    Raises:
        Auth.exceptions.HTTPException: 401 when the credential is absent or
            unrecognized. Raised before any resource is touched.
    """
    token = _bearer(authorization, headers)
    identity = _DEMO_TOKENS.get(token or "")
    if identity is None:
        raise Auth.exceptions.HTTPException(
            status_code=401, detail="invalid or missing credential"
        )
    return {
        "identity": identity,
        "display_name": identity,
        "is_authenticated": True,
        "permissions": _PERMISSIONS.get(identity, []),
    }


def _bearer(
    authorization: str | None, headers: dict[bytes, bytes] | None
) -> str | None:
    """Extract a bearer token from whichever header form arrived."""
    raw = authorization
    if raw is None and headers:
        value = headers.get(b"authorization") or headers.get(b"Authorization")
        raw = value.decode() if isinstance(value, bytes) else value
    if not raw:
        return None
    scheme, _, token = raw.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else raw.strip()


@auth.on
async def deny_by_default(ctx: Auth.types.AuthContext, value: Any) -> bool:
    """Refuse anything without a handler of its own.

    The server warns at startup that unhandled resources — ``crons``, ``store``
    — are "a common source of cross-user data leaks", because authentication
    alone still lets an authenticated customer touch them. This system uses
    neither, so the honest default is no. More specific handlers below take
    precedence, so this only ever sees what nothing else claimed.
    """
    return is_studio_user(ctx.user)


@auth.on.assistants
async def only_operators_manage_assistants(
    ctx: Auth.types.AuthContext, value: Any
) -> bool:
    """Assistants are deployment configuration, not customer data."""
    if is_studio_user(ctx.user):
        return True
    if "assistants:write" in (ctx.user.permissions or []):
        return True
    raise Auth.exceptions.HTTPException(
        status_code=403, detail="assistants are managed by the operator"
    )


@auth.on.threads
async def threads_are_private(
    ctx: Auth.types.AuthContext, value: Any
) -> Any:
    """Bind every thread to the customer who created it.

    Applies to reads, updates, searches, and — via the more specific handler
    below — run creation. Returning a filter rather than a boolean is what
    makes an unowned thread invisible instead of merely forbidden, so a caller
    cannot confirm that someone else's thread exists.

    Studio is allowed through unfiltered. A Studio request is authenticated as
    the *developer*, so filtering it by ``ctx.user.identity`` would bind threads
    to a principal that is not a customer at all, and every demo thread would
    then be owned by whoever happened to open the browser.
    """
    if is_studio_user(ctx.user):
        return {}

    owner = ctx.user.identity
    if isinstance(value, dict):
        metadata = value.setdefault("metadata", {})
        metadata["owner"] = owner
    return {"owner": owner}


@auth.on.threads.create_run
async def runs_inherit_thread_ownership(
    ctx: Auth.types.AuthContext, value: Auth.types.on.threads.create_run.value
) -> Any:
    """Authorize the run against the thread's owner before the run exists.

    This is the handler the cross-tenant resume test exercises: the denial has
    to land here, because one step later the checkpoint is in memory and the
    conversation has already been reconstituted.
    """
    if is_studio_user(ctx.user):
        return {}

    owner = ctx.user.identity
    metadata = value.setdefault("metadata", {})
    metadata["owner"] = owner
    logger.info("authorizing run on thread %s for %s", value.get("thread_id"), owner)
    return {"owner": owner}

"""Catalog tools (W2) — recommendations for the authenticated customer.

The second area of work, and the one that turns a support conversation into a
commercial one: the customer arrives about a charge and leaves with something
to buy. Same identity model as billing — no customer argument exists, so the
recommendation is always for whoever is authenticated.
"""

from __future__ import annotations

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from src.agent.context import AuthContext
from src.data.db import CustomerRepository

MAX_SUGGESTED_GENRES = 8


def _repository(runtime: ToolRuntime[AuthContext]) -> CustomerRepository:
    """Build a repository scoped to the authenticated caller.

    Raises:
        PermissionError: If no identity is present.
    """
    context = runtime.context
    if context is None:
        raise PermissionError("no authenticated customer in runtime context")
    return CustomerRepository(context.customer_id)


@tool
def recommend_for_me(
    runtime: ToolRuntime[AuthContext],
    seed_genre: str | None = None,
    limit: int = 5,
) -> str:
    """Suggest tracks the customer does not own yet, based on what they buy.

    Use this when the customer asks for something to listen to, asks what is
    similar to a purchase, or when a support conversation has been resolved and
    there is a natural opening to suggest something. Never invent tracks or
    prices — only offer what this tool returns.

    Args:
        seed_genre: Restrict to one genre, e.g. "Rock". Omit to use the
            customer's own purchase history.
        limit: How many to suggest. Defaults to 5, capped at 10.
    """
    repo = _repository(runtime)

    resolved: str | None = None
    if seed_genre is not None:
        resolved = repo.resolve_genre(seed_genre)
        if resolved is None:
            genres = ", ".join(repo.list_genres()[:MAX_SUGGESTED_GENRES])
            repo.audit.assert_scoped_to(repo.customer_id)
            return (
                f'The catalog has no genre called "{seed_genre}". '
                f"Available genres include: {genres}."
            )

    picks = repo.recommend_tracks(seed_genre=resolved, limit=limit)
    repo.audit.assert_scoped_to(repo.customer_id)

    if not picks:
        return (
            "No new recommendations — this customer already owns everything "
            "matching their profile."
        )

    lines = [
        f"  #{p.track_id} - {p.name}"
        + (f" by {p.artist}" if p.artist else "")
        + f" (${p.unit_price:.2f}) - {p.reason}"
        for p in picks
    ]
    header = f"{len(picks)} recommendation(s):"
    return "\n".join([header, *lines])


# One tool, not two. A get_my_listening_profile tool would be genuinely useful
# and is deliberately absent (ARCHITECTURE §5): the affinity it would report is
# already computed inside recommend_for_me and surfaced in each pick's reason,
# so a second tool would add prompt tokens and a misselection opportunity to
# expose something the customer already sees.
CATALOG_TOOLS = [recommend_for_me]

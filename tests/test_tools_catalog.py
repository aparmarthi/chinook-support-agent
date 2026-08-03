"""Tests for the recommendation tool (W2).

W2 is the second area of work the brief requires, so these are compliance
tests as much as correctness tests. They also pin two properties the demo
depends on: recommendations never include something the customer already owns,
and the same request always returns the same list, so an evaluation run is not
noisy for reasons unrelated to the agent.

Ground truth is recomputed independently from Chinook rather than read back
from the repository, so a bug in the ownership query cannot make its own test
pass.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest
from langchain_core.tools import BaseTool

from src.agent.tools.catalog import CATALOG_TOOLS, recommend_for_me
from src.data.db import chinook_connection

ToolRunner = Callable[[list[BaseTool], str, dict[str, Any], int], str]

HELENA = 6
RICHARD = 26

_TRACK_ID = re.compile(r"#(\d+)")


def track_ids(output: str) -> list[int]:
    """Pull the recommended track IDs out of the formatted response."""
    return [int(m) for m in _TRACK_ID.findall(output)]


def owned_track_ids(customer_id: int) -> set[int]:
    """Recompute ownership straight from Chinook, independent of the code."""
    sql = """
        SELECT DISTINCT il.TrackId
        FROM InvoiceLine il
        JOIN Invoice i ON i.InvoiceId = il.InvoiceId
        WHERE i.CustomerId = ?
    """
    with chinook_connection() as conn:
        return {r["TrackId"] for r in conn.execute(sql, (customer_id,))}


def genre_track_ids(genre: str) -> set[int]:
    sql = """
        SELECT t.TrackId FROM Track t
        JOIN Genre g ON g.GenreId = t.GenreId
        WHERE g.Name = ?
    """
    with chinook_connection() as conn:
        return {r["TrackId"] for r in conn.execute(sql, (genre,))}


class TestModelVisibleSchema:
    def test_no_tenant_argument(self) -> None:
        for tool in CATALOG_TOOLS:
            offenders = {
                a for a in tool.args if "customer" in a.lower() or "tenant" in a.lower()
            }
            assert not offenders, f"{tool.name} exposes {offenders}"

    def test_runtime_is_not_in_the_schema(self) -> None:
        assert "runtime" not in recommend_for_me.args

    def test_only_one_catalog_tool(self) -> None:
        """ARCHITECTURE §5 caps the catalog surface at a single tool."""
        assert [t.name for t in CATALOG_TOOLS] == ["recommend_for_me"]

    def test_description_warns_against_inventing_tracks(self) -> None:
        """The tool description is the only place this instruction survives."""
        assert "Never invent" in recommend_for_me.description


class TestRecommendationQuality:
    def test_returns_the_requested_number(self, run_tool: ToolRunner) -> None:
        out = run_tool(CATALOG_TOOLS, "recommend_for_me", {"limit": 3}, HELENA)
        assert len(track_ids(out)) == 3

    def test_never_recommends_something_already_owned(
        self, run_tool: ToolRunner
    ) -> None:
        owned = owned_track_ids(HELENA)
        out = run_tool(CATALOG_TOOLS, "recommend_for_me", {"limit": 10}, HELENA)
        assert owned & set(track_ids(out)) == set()

    def test_does_not_repeat_an_artist(self, run_tool: ToolRunner) -> None:
        """Five tracks from one artist is a query result, not a suggestion."""
        out = run_tool(CATALOG_TOOLS, "recommend_for_me", {"limit": 5}, HELENA)
        artists = re.findall(r" by (.+?) \(\$", out)
        assert len(artists) == len(set(artists)), artists

    def test_every_pick_carries_a_reason(self, run_tool: ToolRunner) -> None:
        """A rep has to be able to say why out loud."""
        out = run_tool(CATALOG_TOOLS, "recommend_for_me", {"limit": 5}, HELENA)
        for line in out.splitlines()[1:]:
            assert " - " in line.split("$")[-1], line

    def test_limit_is_capped(self, run_tool: ToolRunner) -> None:
        out = run_tool(CATALOG_TOOLS, "recommend_for_me", {"limit": 500}, HELENA)
        assert len(track_ids(out)) <= 10

    def test_is_deterministic(self, run_tool: ToolRunner) -> None:
        """Otherwise every evaluation run has a moving baseline."""
        first = run_tool(CATALOG_TOOLS, "recommend_for_me", {}, HELENA)
        second = run_tool(CATALOG_TOOLS, "recommend_for_me", {}, HELENA)
        assert first == second


class TestSeedGenre:
    def test_stays_inside_the_requested_genre(self, run_tool: ToolRunner) -> None:
        out = run_tool(
            CATALOG_TOOLS, "recommend_for_me", {"seed_genre": "Jazz", "limit": 5}, HELENA
        )
        assert set(track_ids(out)) <= genre_track_ids("Jazz")

    def test_genre_match_is_case_insensitive(self, run_tool: ToolRunner) -> None:
        lower = run_tool(
            CATALOG_TOOLS, "recommend_for_me", {"seed_genre": "jazz", "limit": 3}, HELENA
        )
        exact = run_tool(
            CATALOG_TOOLS, "recommend_for_me", {"seed_genre": "Jazz", "limit": 3}, HELENA
        )
        assert lower == exact

    def test_unknown_genre_is_answered_not_crashed(
        self, run_tool: ToolRunner
    ) -> None:
        """A hallucinated genre should teach the model, not raise."""
        out = run_tool(
            CATALOG_TOOLS, "recommend_for_me", {"seed_genre": "Polka"}, HELENA
        )
        assert "no genre called" in out
        assert "Blues" in out

    def test_seeded_results_still_exclude_owned(self, run_tool: ToolRunner) -> None:
        owned = owned_track_ids(HELENA)
        out = run_tool(
            CATALOG_TOOLS, "recommend_for_me", {"seed_genre": "Rock", "limit": 10}, HELENA
        )
        assert owned & set(track_ids(out)) == set()


class TestRecommendationsAreTenantScoped:
    def test_two_customers_get_different_lists(self, run_tool: ToolRunner) -> None:
        """Proves the profile comes from the caller, not from a global default."""
        helena = run_tool(CATALOG_TOOLS, "recommend_for_me", {}, HELENA)
        richard = run_tool(CATALOG_TOOLS, "recommend_for_me", {}, RICHARD)
        assert helena != richard

    def test_recommendations_respect_each_customers_ownership(
        self, run_tool: ToolRunner
    ) -> None:
        for customer in (HELENA, RICHARD):
            out = run_tool(CATALOG_TOOLS, "recommend_for_me", {"limit": 10}, customer)
            assert owned_track_ids(customer) & set(track_ids(out)) == set()

    def test_no_context_is_refused(self) -> None:
        class NoContext:
            context = None

        with pytest.raises(PermissionError):
            recommend_for_me.func(runtime=NoContext())  # type: ignore[attr-defined]

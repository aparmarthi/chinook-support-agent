"""Database access for the support agent — the security boundary.

Two connections, deliberately asymmetric (ADR-014):

    chinook.db   opened with a read-only URI. The agent has no write path into
                 customer, invoice, or catalog records at the driver level.
    support.db   the single writable surface, holding refund tickets.

Customer data is reachable only through :class:`CustomerRepository`, which is
constructed with a tenant and exposes no method that takes one. "Fetch customer
26" is not expressible here — there is no argument to put it in — and every
customer-scoped statement binds the tenant in its ``WHERE`` clause.

No SQL is ever authored by the model (ADR-003).
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from src.data.audit import AuditLog, current_audit

PROJECT_ROOT = Path(__file__).resolve().parents[2]

MAX_INVOICE_LIMIT = 50
MAX_RECOMMENDATIONS = 10
AFFINITY_GENRES = 3


def data_dir() -> Path:
    """Directory holding the SQLite files, overridable for tests."""
    override = os.environ.get("SUPPORT_DATA_DIR")
    return Path(override) if override else PROJECT_ROOT / "data"


def chinook_path() -> Path:
    """Path to the read-only reference database."""
    return data_dir() / "chinook.db"


def support_path() -> Path:
    """Path to the writable support database."""
    return data_dir() / "support.db"


@contextmanager
def chinook_connection() -> Iterator[sqlite3.Connection]:
    """Open Chinook read-only.

    ``mode=ro`` is enforced by SQLite itself, so a write raises regardless of
    what the calling code intends. That is the point: the read-only claim is a
    property of the connection, not a convention the tools agree to follow.
    """
    conn = sqlite3.connect(f"file:{chinook_path()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def support_connection() -> Iterator[sqlite3.Connection]:
    """Open the writable support database."""
    conn = sqlite3.connect(support_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@dataclass(frozen=True)
class InvoiceSummary:
    """One invoice header belonging to the authenticated customer."""

    invoice_id: int
    invoice_date: str
    total: float
    billing_city: str | None
    billing_country: str | None


@dataclass(frozen=True)
class InvoiceLineItem:
    """One purchased track on an invoice."""

    invoice_line_id: int
    track_name: str
    artist_name: str | None
    unit_price: float
    quantity: int

    @property
    def line_total(self) -> float:
        """Amount charged for this line."""
        return round(self.unit_price * self.quantity, 2)


@dataclass(frozen=True)
class InvoiceDetail:
    """An invoice header with its line items."""

    invoice_id: int
    invoice_date: str
    total: float
    billing_city: str | None
    billing_country: str | None
    lines: tuple[InvoiceLineItem, ...]


@dataclass(frozen=True)
class SpendSummary:
    """Aggregate spend, computed in SQL rather than by the model."""

    invoice_count: int
    total_spent: float
    first_purchase: str | None
    last_purchase: str | None
    year: int | None


@dataclass(frozen=True)
class InvoiceLineDetail:
    """One purchased line, resolved for a refund request."""

    invoice_line_id: int
    invoice_id: int
    invoice_date: str
    track_name: str
    artist_name: str | None
    unit_price: float
    quantity: int

    @property
    def amount(self) -> float:
        """Amount charged for this line."""
        return round(self.unit_price * self.quantity, 2)


@dataclass(frozen=True)
class RefundRequest:
    """A filed refund ticket.

    Attributes:
        created: ``False`` when the idempotency key already existed, meaning
            this call resolved to a ticket filed earlier. The caller can treat
            both the same; the flag exists so tests can tell a duplicate resume
            from a first submission.
    """

    refund_request_id: int
    invoice_line_id: int
    reason: str
    status: str
    created: bool


@dataclass(frozen=True)
class HandoffRequest:
    """A queued handoff to the customer's assigned rep.

    Attributes:
        created: ``False`` when this call resolved to a handoff already queued
            under the same idempotency key, so a retried tool call does not
            queue the same conversation twice.
    """

    handoff_request_id: int
    support_rep_name: str | None
    summary: str
    urgency: str
    status: str
    created: bool


@dataclass(frozen=True)
class GenreAffinity:
    """How many tracks the customer has bought in one genre."""

    genre: str
    tracks_purchased: int


@dataclass(frozen=True)
class TrackRecommendation:
    """A catalog track the customer does not own yet.

    Attributes:
        reason: Why it was picked, in words the agent can repeat to the
            customer. A recommendation a support rep cannot justify out loud
            is not usable in a support conversation.
    """

    track_id: int
    name: str
    artist: str | None
    album: str | None
    genre: str | None
    unit_price: float
    reason: str


@dataclass(frozen=True)
class CustomerProfile:
    """Identity and assigned support rep for the authenticated customer."""

    customer_id: int
    first_name: str
    last_name: str
    email: str
    country: str | None
    support_rep_name: str | None
    support_rep_email: str | None

    @property
    def full_name(self) -> str:
        """Display name."""
        return f"{self.first_name} {self.last_name}"


class CustomerRepository:
    """Read access to Chinook, permanently scoped to one customer.

    Constructed by the tool layer from the trusted :class:`AuthContext`. No
    method accepts a tenant argument, so the scope cannot be widened by a
    caller — or by anything the model puts in a tool call.
    """

    def __init__(self, customer_id: int, audit: AuditLog | None = None) -> None:
        """Bind the repository to a tenant.

        Args:
            customer_id: The authenticated caller, from ``AuthContext``.
            audit: Tenant-access log for the turn. One is created if omitted so
                the repository is usable in isolation, but the agent passes a
                shared log so the guard sees every call.
        """
        self._customer_id = customer_id
        # Defaults to the enclosing audit scope so the result guard sees these
        # reads, and to a private log when there is no scope (direct calls).
        self.audit = audit if audit is not None else current_audit()

    @property
    def customer_id(self) -> int:
        """The tenant this repository is bound to."""
        return self._customer_id

    def list_invoices(self, limit: int = 10) -> list[InvoiceSummary]:
        """Return the customer's most recent invoices, newest first.

        Args:
            limit: Maximum invoices to return, clamped to
                ``1..MAX_INVOICE_LIMIT``.
        """
        bounded = max(1, min(int(limit), MAX_INVOICE_LIMIT))
        sql = """
            SELECT InvoiceId, InvoiceDate, Total, BillingCity, BillingCountry,
                   CustomerId
            FROM Invoice
            WHERE CustomerId = ?
            ORDER BY InvoiceDate DESC, InvoiceId DESC
            LIMIT ?
        """
        with chinook_connection() as conn:
            rows = conn.execute(sql, (self._customer_id, bounded)).fetchall()

        self.audit.record("list_invoices", (r["CustomerId"] for r in rows))
        return [
            InvoiceSummary(
                invoice_id=r["InvoiceId"],
                invoice_date=r["InvoiceDate"],
                total=float(r["Total"]),
                billing_city=r["BillingCity"],
                billing_country=r["BillingCountry"],
            )
            for r in rows
        ]

    def get_invoice_detail(self, invoice_id: int) -> InvoiceDetail | None:
        """Return one invoice with its line items, or ``None``.

        ``None`` covers both "does not exist" and "belongs to someone else".
        The two cases are indistinguishable to the caller by design — a
        distinct "not yours" response would confirm the invoice exists and turn
        the tool into an enumeration oracle.
        """
        header_sql = """
            SELECT InvoiceId, InvoiceDate, Total, BillingCity, BillingCountry,
                   CustomerId
            FROM Invoice
            WHERE InvoiceId = ? AND CustomerId = ?
        """
        with chinook_connection() as conn:
            header = conn.execute(
                header_sql, (int(invoice_id), self._customer_id)
            ).fetchone()
            if header is None:
                self.audit.record("get_invoice_detail", ())
                return None
            lines = conn.execute(
                self._LINES_SQL, (int(invoice_id), self._customer_id)
            ).fetchall()

        self.audit.record(
            "get_invoice_detail",
            [header["CustomerId"], *(r["CustomerId"] for r in lines)],
        )
        return InvoiceDetail(
            invoice_id=header["InvoiceId"],
            invoice_date=header["InvoiceDate"],
            total=float(header["Total"]),
            billing_city=header["BillingCity"],
            billing_country=header["BillingCountry"],
            lines=tuple(
                InvoiceLineItem(
                    invoice_line_id=r["InvoiceLineId"],
                    track_name=r["TrackName"],
                    artist_name=r["ArtistName"],
                    unit_price=float(r["UnitPrice"]),
                    quantity=r["Quantity"],
                )
                for r in lines
            ),
        )

    # Re-asserts CustomerId even though the header check already gated it, so
    # the statement is safe read in isolation rather than safe by call order.
    _LINES_SQL = """
        SELECT il.InvoiceLineId, il.UnitPrice, il.Quantity,
               t.Name AS TrackName, ar.Name AS ArtistName, i.CustomerId
        FROM InvoiceLine il
        JOIN Invoice i ON i.InvoiceId = il.InvoiceId
        JOIN Track t ON t.TrackId = il.TrackId
        LEFT JOIN Album al ON al.AlbumId = t.AlbumId
        LEFT JOIN Artist ar ON ar.ArtistId = al.ArtistId
        WHERE il.InvoiceId = ? AND i.CustomerId = ?
        ORDER BY il.InvoiceLineId
    """

    def get_spend_summary(self, year: int | None = None) -> SpendSummary:
        """Aggregate the customer's spend, optionally for a single year.

        Grouped by ``CustomerId`` so the aggregate reports the tenants it
        actually summed. Given the ``WHERE`` clause that is at most one group;
        more than one would mean the scope had failed, and the audit log would
        carry the proof instead of the total quietly being wrong.
        """
        clause = "WHERE CustomerId = ?"
        params: list[object] = [self._customer_id]
        if year is not None:
            clause += " AND strftime('%Y', InvoiceDate) = ?"
            params.append(f"{int(year):04d}")

        sql = f"""
            SELECT CustomerId, count(*) AS InvoiceCount, SUM(Total) AS TotalSpent,
                   MIN(InvoiceDate) AS FirstPurchase, MAX(InvoiceDate) AS LastPurchase
            FROM Invoice
            {clause}
            GROUP BY CustomerId
        """
        with chinook_connection() as conn:
            rows = conn.execute(sql, params).fetchall()

        self.audit.record("get_spend_summary", (r["CustomerId"] for r in rows))
        if not rows:
            return SpendSummary(0, 0.0, None, None, year)
        row = rows[0]
        return SpendSummary(
            invoice_count=row["InvoiceCount"],
            total_spent=round(float(row["TotalSpent"]), 2),
            first_purchase=row["FirstPurchase"],
            last_purchase=row["LastPurchase"],
            year=year,
        )

    def get_profile(self) -> CustomerProfile | None:
        """Return the customer's identity and assigned support rep."""
        sql = """
            SELECT c.CustomerId, c.FirstName, c.LastName, c.Email, c.Country,
                   e.FirstName AS RepFirstName, e.LastName AS RepLastName,
                   e.Email AS RepEmail
            FROM Customer c
            LEFT JOIN Employee e ON e.EmployeeId = c.SupportRepId
            WHERE c.CustomerId = ?
        """
        with chinook_connection() as conn:
            row = conn.execute(sql, (self._customer_id,)).fetchone()

        self.audit.record("get_profile", () if row is None else (row["CustomerId"],))
        if row is None:
            return None
        rep_name = (
            f"{row['RepFirstName']} {row['RepLastName']}"
            if row["RepFirstName"]
            else None
        )
        return CustomerProfile(
            customer_id=row["CustomerId"],
            first_name=row["FirstName"],
            last_name=row["LastName"],
            email=row["Email"],
            country=row["Country"],
            support_rep_name=rep_name,
            support_rep_email=row["RepEmail"],
        )

    # ------------------------------------------------------------------
    # Refunds (W3) — the only write path in the system
    # ------------------------------------------------------------------

    def get_invoice_line(self, invoice_line_id: int) -> InvoiceLineDetail | None:
        """Resolve one of the customer's purchased lines, or ``None``.

        ``None`` covers both "no such line" and "belongs to someone else",
        matching :meth:`get_invoice_detail`. This is also the ownership check
        for refunds: a line that does not resolve cannot be refunded, so
        authorization happens in Chinook before anything reaches ``support.db``.
        """
        sql = """
            SELECT il.InvoiceLineId, il.UnitPrice, il.Quantity,
                   i.InvoiceId, i.InvoiceDate, i.CustomerId,
                   t.Name AS TrackName, ar.Name AS ArtistName
            FROM InvoiceLine il
            JOIN Invoice i ON i.InvoiceId = il.InvoiceId
            JOIN Track t ON t.TrackId = il.TrackId
            LEFT JOIN Album al ON al.AlbumId = t.AlbumId
            LEFT JOIN Artist ar ON ar.ArtistId = al.ArtistId
            WHERE il.InvoiceLineId = ? AND i.CustomerId = ?
        """
        with chinook_connection() as conn:
            row = conn.execute(
                sql, (int(invoice_line_id), self._customer_id)
            ).fetchone()

        self.audit.record(
            "get_invoice_line", () if row is None else (row["CustomerId"],)
        )
        if row is None:
            return None
        return InvoiceLineDetail(
            invoice_line_id=row["InvoiceLineId"],
            invoice_id=row["InvoiceId"],
            invoice_date=row["InvoiceDate"],
            track_name=row["TrackName"],
            artist_name=row["ArtistName"],
            unit_price=float(row["UnitPrice"]),
            quantity=row["Quantity"],
        )

    def create_refund_request(
        self, invoice_line_id: int, reason: str, idempotency_key: str
    ) -> RefundRequest | None:
        """File a refund ticket, at most once per idempotency key.

        A human-in-the-loop interrupt can be resumed more than once — by a
        retry, a reconnect, or someone clicking twice. The ``UNIQUE``
        constraint on the key is what makes the second resume a no-op instead
        of a second ticket, and it only works because the key is derived from
        the thread and tool call rather than generated here.

        Args:
            invoice_line_id: The line being disputed.
            reason: What the customer said, in their words.
            idempotency_key: ``f"{thread_id}:{tool_call_id}"``, from the
                runtime. Never generate this locally.

        Returns:
            The ticket, or ``None`` if the line is not this customer's.
        """
        if self.get_invoice_line(invoice_line_id) is None:
            return None

        insert = """
            INSERT INTO refund_requests
                (CustomerId, InvoiceLineId, Reason, IdempotencyKey)
            VALUES (?, ?, ?, ?)
        """
        with support_connection() as conn:
            try:
                cursor = conn.execute(
                    insert,
                    (self._customer_id, int(invoice_line_id), reason, idempotency_key),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                return self._existing_request(conn, idempotency_key)
            return RefundRequest(
                refund_request_id=int(cursor.lastrowid or 0),
                invoice_line_id=int(invoice_line_id),
                reason=reason,
                status="open",
                created=True,
            )

    @staticmethod
    def _existing_request(
        conn: sqlite3.Connection, idempotency_key: str
    ) -> RefundRequest:
        """Return the ticket a duplicate submission collided with."""
        row = conn.execute(
            "SELECT RefundRequestId, InvoiceLineId, Reason, Status "
            "FROM refund_requests WHERE IdempotencyKey = ?",
            (idempotency_key,),
        ).fetchone()
        return RefundRequest(
            refund_request_id=row["RefundRequestId"],
            invoice_line_id=row["InvoiceLineId"],
            reason=row["Reason"],
            status=row["Status"],
            created=False,
        )

    def create_handoff_request(
        self,
        summary: str,
        urgency: str,
        support_rep_name: str | None,
        idempotency_key: str,
    ) -> HandoffRequest:
        """Queue a handoff for this customer's rep, at most once per key.

        Args:
            summary: What the customer needs, written for a rep to read cold.
            urgency: ``low``, ``normal``, or ``high``.
            support_rep_name: The rep resolved from the customer's profile.
            idempotency_key: ``f"{thread_id}:{tool_call_id}"``, from the
                runtime. Never generate this locally.

        Returns:
            The queued handoff, or the one an identical call queued earlier.
        """
        insert = """
            INSERT INTO handoff_requests
                (CustomerId, SupportRepName, Summary, Urgency, IdempotencyKey)
            VALUES (?, ?, ?, ?, ?)
        """
        with support_connection() as conn:
            try:
                cursor = conn.execute(
                    insert,
                    (
                        self._customer_id,
                        support_rep_name,
                        summary,
                        urgency,
                        idempotency_key,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                return self._existing_handoff(conn, idempotency_key)
            return HandoffRequest(
                handoff_request_id=int(cursor.lastrowid or 0),
                support_rep_name=support_rep_name,
                summary=summary,
                urgency=urgency,
                status="queued",
                created=True,
            )

    @staticmethod
    def _existing_handoff(
        conn: sqlite3.Connection, idempotency_key: str
    ) -> HandoffRequest:
        """Return the handoff a duplicate submission collided with."""
        row = conn.execute(
            "SELECT HandoffRequestId, SupportRepName, Summary, Urgency, Status "
            "FROM handoff_requests WHERE IdempotencyKey = ?",
            (idempotency_key,),
        ).fetchone()
        return HandoffRequest(
            handoff_request_id=row["HandoffRequestId"],
            support_rep_name=row["SupportRepName"],
            summary=row["Summary"],
            urgency=row["Urgency"],
            status=row["Status"],
            created=False,
        )

    def count_handoff_requests(self) -> int:
        """How many handoffs this customer has queued. Used by the evals."""
        with support_connection() as conn:
            row = conn.execute(
                "SELECT count(*) AS n FROM handoff_requests WHERE CustomerId = ?",
                (self._customer_id,),
            ).fetchone()
        return int(row["n"])

    def count_refund_requests(self) -> int:
        """How many tickets this customer has open. Used by the HITL tests."""
        with support_connection() as conn:
            row = conn.execute(
                "SELECT count(*) AS n FROM refund_requests WHERE CustomerId = ?",
                (self._customer_id,),
            ).fetchone()
        return int(row["n"])

    # ------------------------------------------------------------------
    # Catalog and recommendations (W2)
    #
    # Catalog tables — Track, Album, Artist, Genre — have no customer column,
    # so reads against them cannot return another tenant's rows and there is
    # nothing for the audit log to record. Anything touching Invoice or
    # InvoiceLine is customer-scoped and audited exactly as above. That split
    # is the reason recommendations can reach the whole catalog without
    # weakening the isolation claim.
    #
    # The ranking signal is drawn only from this customer's own purchases.
    # Cross-customer popularity would be a better recommender and a worse fit
    # here: it would mean reading every other tenant's purchase rows during a
    # demo about tenant isolation, to gain nothing the story needs.
    # ------------------------------------------------------------------

    def list_genres(self) -> list[str]:
        """Every genre in the catalog. Not customer data."""
        with chinook_connection() as conn:
            rows = conn.execute("SELECT Name FROM Genre ORDER BY Name").fetchall()
        return [r["Name"] for r in rows]

    def resolve_genre(self, name: str) -> str | None:
        """Match a caller-supplied genre name case-insensitively."""
        target = name.strip().casefold()
        return next((g for g in self.list_genres() if g.casefold() == target), None)

    def get_genre_affinity(self, top: int = AFFINITY_GENRES) -> list[GenreAffinity]:
        """The customer's most-purchased genres, most first."""
        sql = """
            SELECT g.Name AS Genre, count(*) AS Purchased, i.CustomerId
            FROM InvoiceLine il
            JOIN Invoice i ON i.InvoiceId = il.InvoiceId
            JOIN Track t ON t.TrackId = il.TrackId
            JOIN Genre g ON g.GenreId = t.GenreId
            WHERE i.CustomerId = ?
            GROUP BY g.Name, i.CustomerId
            ORDER BY Purchased DESC, g.Name
        """
        with chinook_connection() as conn:
            rows = conn.execute(sql, (self._customer_id,)).fetchall()

        self.audit.record("get_genre_affinity", (r["CustomerId"] for r in rows))
        return [GenreAffinity(r["Genre"], r["Purchased"]) for r in rows[:top]]

    def _owned_track_ids(self) -> set[int]:
        """Track IDs the customer has already bought."""
        sql = """
            SELECT DISTINCT il.TrackId, i.CustomerId
            FROM InvoiceLine il
            JOIN Invoice i ON i.InvoiceId = il.InvoiceId
            WHERE i.CustomerId = ?
        """
        with chinook_connection() as conn:
            rows = conn.execute(sql, (self._customer_id,)).fetchall()

        self.audit.record("_owned_track_ids", (r["CustomerId"] for r in rows))
        return {r["TrackId"] for r in rows}

    def _owned_artists(self) -> list[tuple[int, str]]:
        """Artists the customer has bought from, most-purchased first."""
        sql = """
            SELECT ar.ArtistId, ar.Name AS ArtistName, count(*) AS Purchased,
                   i.CustomerId
            FROM InvoiceLine il
            JOIN Invoice i ON i.InvoiceId = il.InvoiceId
            JOIN Track t ON t.TrackId = il.TrackId
            JOIN Album al ON al.AlbumId = t.AlbumId
            JOIN Artist ar ON ar.ArtistId = al.ArtistId
            WHERE i.CustomerId = ?
            GROUP BY ar.ArtistId, ar.Name, i.CustomerId
            ORDER BY Purchased DESC, ar.Name
        """
        with chinook_connection() as conn:
            rows = conn.execute(sql, (self._customer_id,)).fetchall()

        self.audit.record("_owned_artists", (r["CustomerId"] for r in rows))
        return [(r["ArtistId"], r["ArtistName"]) for r in rows]

    _CANDIDATE_COLUMNS = """
        SELECT t.TrackId, t.Name AS TrackName, ar.Name AS ArtistName,
               al.Title AS AlbumTitle, g.Name AS GenreName, t.UnitPrice
    """

    def _tracks_by_artists(self, artist_ids: list[int]) -> list[sqlite3.Row]:
        """Catalog tracks belonging to the given artists."""
        if not artist_ids:
            return []
        placeholders = ",".join("?" * len(artist_ids))
        sql = f"""
            {self._CANDIDATE_COLUMNS}
            FROM Track t
            JOIN Album al ON al.AlbumId = t.AlbumId
            JOIN Artist ar ON ar.ArtistId = al.ArtistId
            LEFT JOIN Genre g ON g.GenreId = t.GenreId
            WHERE ar.ArtistId IN ({placeholders})
            ORDER BY al.AlbumId, t.TrackId
        """
        with chinook_connection() as conn:
            return conn.execute(sql, artist_ids).fetchall()

    def _tracks_by_genres(self, genres: list[str]) -> list[sqlite3.Row]:
        """Catalog tracks in the given genres."""
        if not genres:
            return []
        placeholders = ",".join("?" * len(genres))
        sql = f"""
            {self._CANDIDATE_COLUMNS}
            FROM Track t
            JOIN Genre g ON g.GenreId = t.GenreId
            LEFT JOIN Album al ON al.AlbumId = t.AlbumId
            LEFT JOIN Artist ar ON ar.ArtistId = al.ArtistId
            WHERE g.Name IN ({placeholders})
            ORDER BY g.Name, al.AlbumId, t.TrackId
        """
        with chinook_connection() as conn:
            return conn.execute(sql, genres).fetchall()

    @staticmethod
    def _to_recommendation(row: sqlite3.Row, reason: str) -> TrackRecommendation:
        return TrackRecommendation(
            track_id=row["TrackId"],
            name=row["TrackName"],
            artist=row["ArtistName"],
            album=row["AlbumTitle"],
            genre=row["GenreName"],
            unit_price=float(row["UnitPrice"]),
            reason=reason,
        )

    def recommend_tracks(
        self, seed_genre: str | None = None, limit: int = 5
    ) -> list[TrackRecommendation]:
        """Suggest catalog tracks the customer does not already own.

        A deliberately simple, fully deterministic heuristic — same inputs
        always produce the same list, which keeps evaluation runs from being
        noisy for reasons that have nothing to do with the agent.

        Without a seed genre it prefers more from artists the customer already
        buys, then fills from their strongest genres. With one, it stays inside
        that genre.

        Args:
            seed_genre: Exact catalog genre name, already resolved by the
                caller via :meth:`resolve_genre`.
            limit: How many to return, clamped to ``1..MAX_RECOMMENDATIONS``.
        """
        bounded = max(1, min(int(limit), MAX_RECOMMENDATIONS))
        owned = self._owned_track_ids()
        picks: list[TrackRecommendation] = []
        seen_tracks: set[int] = set()
        seen_artists: set[str] = set()

        def take(rows: list[sqlite3.Row], reason: str) -> None:
            """Append unowned tracks, at most one per artist.

            The cap is what makes this look like a recommendation rather than a
            query result. Without it the customer's top artist has enough
            catalog depth to fill the entire list on its own.
            """
            for row in rows:
                if len(picks) >= bounded:
                    return
                track_id = row["TrackId"]
                if track_id in owned or track_id in seen_tracks:
                    continue
                artist = row["ArtistName"] or f"__unknown_{track_id}"
                if artist in seen_artists:
                    continue
                seen_tracks.add(track_id)
                seen_artists.add(artist)
                picks.append(self._to_recommendation(row, reason))

        if seed_genre is not None:
            take(
                self._tracks_by_genres([seed_genre]),
                f"{seed_genre} track not yet in their library",
            )
            return picks

        for artist_id, artist_name in self._owned_artists():
            if len(picks) >= bounded:
                break
            take(
                self._tracks_by_artists([artist_id]),
                f"more from {artist_name}, an artist they already buy",
            )

        for affinity in self.get_genre_affinity():
            if len(picks) >= bounded:
                break
            take(
                self._tracks_by_genres([affinity.genre]),
                f"{affinity.genre} is one of their top genres "
                f"({affinity.tracks_purchased} tracks purchased)",
            )
        return picks

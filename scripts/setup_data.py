"""Build the local SQLite databases from the Chinook dataset.

Creates two files, deliberately separated (ADR-014):

  data/chinook.db  Reference data. Opened read-only at runtime, so the agent
                   has no write path into customer or invoice records.
  data/support.db  Refund request tickets. The only writable surface.

A single file cannot be both genuinely read-only for reads and writable for
refund tickets, and a second writable connection to one file would re-expose
every Chinook table. Splitting makes the read-only claim structural.

Usage:
    python scripts/setup_data.py [--force]
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import ssl
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.db import chinook_path, data_dir, support_path  # noqa: E402

logger = logging.getLogger(__name__)

CHINOOK_SQL_URL = (
    "https://raw.githubusercontent.com/lerocha/chinook-database/master/"
    "ChinookDatabase/DataSources/Chinook_Sqlite.sql"
)


def sql_dump_path() -> Path:
    """Path to the downloaded Chinook SQL dump."""
    return data_dir() / "Chinook_Sqlite.sql"

# No foreign keys to Chinook: support.db is a separate file, so CustomerId and
# InvoiceLineId are unenforced references. Ownership is verified in application
# code against chinook.db before any insert.
#
# The UNIQUE constraint is the idempotency control. A human-in-the-loop
# interrupt can be resumed more than once; without this, each resume would
# file a duplicate ticket.
#
# IdempotencyKey must be f"{thread_id}:{tool_call_id}" — server-derived and
# stable across resumes. A freshly generated value would be unique every time,
# so the constraint would be enforcing nothing while appearing to.
#
# Status starts at 'open' because the row only exists after a human approved
# filing the request. Approval of the refund itself happens downstream.
#
# handoff_requests exists because the agent used to *say* a colleague had been
# contacted while writing nothing anywhere — the same fabrication the escalation
# eval was built to catch, one level down: the tool call was real and its effect
# was not. 'queued' rather than 'sent' is deliberate. A row in a table nobody
# drains is not a notification, and the demo says so.
#
# thread_owner binds each conversation to exactly one customer. SupportGateway
# reads it before the graph is invoked, so a mismatched tenant is rejected
# before the checkpointer ever loads state (ARCHITECTURE §4, layer 2).
#
# ConversationId is the only identifier a client ever sees. ThreadId stays
# internal, so a caller cannot name someone else's thread even to attack it.
SUPPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS thread_owner (
    ConversationId  TEXT    PRIMARY KEY,
    ThreadId        TEXT    NOT NULL UNIQUE,
    CustomerId      INTEGER NOT NULL,
    CreatedAt       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_thread_owner_customer
    ON thread_owner (CustomerId);

CREATE TABLE IF NOT EXISTS refund_requests (
    RefundRequestId INTEGER PRIMARY KEY AUTOINCREMENT,
    CustomerId      INTEGER NOT NULL,
    InvoiceLineId   INTEGER NOT NULL,
    Reason          TEXT    NOT NULL,
    Status          TEXT    NOT NULL DEFAULT 'open',
    IdempotencyKey  TEXT    NOT NULL UNIQUE,
    CreatedAt       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_refund_customer
    ON refund_requests (CustomerId);

CREATE TABLE IF NOT EXISTS handoff_requests (
    HandoffRequestId INTEGER PRIMARY KEY AUTOINCREMENT,
    CustomerId       INTEGER NOT NULL,
    SupportRepName   TEXT,
    Summary          TEXT    NOT NULL,
    Urgency          TEXT    NOT NULL DEFAULT 'normal',
    Status           TEXT    NOT NULL DEFAULT 'queued',
    IdempotencyKey   TEXT    NOT NULL UNIQUE,
    CreatedAt        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_handoff_customer
    ON handoff_requests (CustomerId);
"""

# Row counts verified against the upstream dump; a mismatch means the source
# changed and the demo personas in docs/ARCHITECTURE.md may no longer be valid.
EXPECTED_COUNTS = {
    "Customer": 59,
    "Employee": 8,
    "Invoice": 412,
    "InvoiceLine": 2240,
    "Track": 3503,
    "Album": 347,
    "Artist": 275,
    "Genre": 25,
}


def _ssl_context() -> ssl.SSLContext:
    """Build a verifying SSL context, preferring certifi's CA bundle.

    Framework Python builds on macOS ship without a usable system CA bundle
    until `Install Certificates.command` is run, which makes urllib fail on
    any HTTPS URL. certifi arrives transitively with the LangSmith SDK.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def download_dump(force: bool = False) -> Path:
    """Fetch the Chinook SQL dump, skipping the download if already present."""
    dump = sql_dump_path()
    dump.parent.mkdir(parents=True, exist_ok=True)
    if dump.exists() and not force:
        logger.info("SQL dump already present at %s", dump)
        return dump

    logger.info("Downloading Chinook dump from %s", CHINOOK_SQL_URL)
    request = urllib.request.Request(CHINOOK_SQL_URL)
    with urllib.request.urlopen(request, context=_ssl_context()) as response:
        with dump.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    logger.info("Wrote %s (%.0f KB)", dump, dump.stat().st_size / 1024)
    return dump


def build_chinook(force: bool = False) -> Path:
    """Execute the dump into a fresh SQLite file of reference data."""
    db = chinook_path()
    if db.exists():
        if not force:
            logger.info("Chinook DB already exists at %s (--force to rebuild)", db)
            return db
        db.unlink()

    sql = sql_dump_path().read_text(encoding="utf-8", errors="replace")
    with sqlite3.connect(db) as conn:
        conn.executescript(sql)
    logger.info("Built reference database at %s", db)
    return db


def build_support(force: bool = False) -> Path:
    """Create the writable support database holding tickets and thread owners.

    Uses ``CREATE TABLE IF NOT EXISTS``, so re-running without ``--force`` adds
    tables introduced since the file was first built rather than discarding it.
    """
    db = support_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists() and force:
        db.unlink()

    with sqlite3.connect(db) as conn:
        conn.executescript(SUPPORT_SCHEMA)
    logger.info("Built support database at %s", db)
    return db


def verify_counts(db_path: Path) -> None:
    """Check row counts against expected upstream values, raising on mismatch.

    Raises:
        RuntimeError: If any table's row count differs from the upstream dump.
            The demo personas in docs/ARCHITECTURE.md cite specific invoice IDs
            and totals, so a silent drift would surface as a wrong number in
            front of an audience rather than as a failed setup run.
    """
    mismatches: list[str] = []
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        for table, expected in EXPECTED_COUNTS.items():
            actual = cursor.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            if actual == expected:
                logger.info("  %-12s %6d  ok", table, actual)
            else:
                logger.error("  %-12s %6d  MISMATCH (expected %d)", table, actual, expected)
                mismatches.append(f"{table}: got {actual}, expected {expected}")

    if mismatches:
        raise RuntimeError(
            "Chinook row counts do not match the upstream dump: "
            + "; ".join(mismatches)
            + ". Verify the demo personas before relying on them."
        )


def verify_read_only(db_path: Path) -> None:
    """Assert that the read-only URI actually rejects writes.

    The security claim in docs/PRD.md §5 rests on this, so it is checked here
    rather than assumed. A read-only URI that silently permits writes would be
    a boundary failure that no other test would catch.
    """
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute("UPDATE Customer SET City = 'x' WHERE CustomerId = 1")
    except sqlite3.OperationalError:
        logger.info("  read-only connection rejects writes  ok")
    else:
        raise RuntimeError(f"{db_path} accepted a write through a read-only URI")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-download and rebuild")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    download_dump(force=args.force)
    chinook_db = build_chinook(force=args.force)
    build_support(force=args.force)
    verify_counts(chinook_db)
    verify_read_only(chinook_db)


if __name__ == "__main__":
    main()

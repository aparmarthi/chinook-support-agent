"""Reset the writable demo state so a rehearsal starts where the traces did.

Approving a refund in Studio commits a real row — that is the whole point of
the gate — so every rehearsal leaves one behind and the next run files #2
while the narration says #1. This clears the tickets and rolls the
AUTOINCREMENT counters back so the numbers line up again.

Handoffs are kept at the baseline the captured traces already reference
rather than emptied, so `reports/traces/` stays true. Chinook is read-only and
is never touched; only `support.db` is writable at all.

    python scripts/reset_demo_state.py           # refunds only
    python scripts/reset_demo_state.py --handoffs  # also trim handoffs to 3
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.db import support_path  # noqa: E402

# reports/traces/ cites handoff requests 1-3, so the next live escalation
# should file #4.
HANDOFF_BASELINE = 3


def _reset(conn: sqlite3.Connection, table: str, keep: int) -> tuple[int, int]:
    """Delete rows past ``keep`` and roll the AUTOINCREMENT counter back.

    Resetting ``sqlite_sequence`` matters as much as the delete: without it
    SQLite keeps counting from the high-water mark and the next insert is
    numbered as though the cleared rows were still there.

    Args:
        conn: Open connection to the support database.
        table: Table to trim.
        keep: Number of leading rows to preserve.

    Returns:
        Rows deleted, and the row count left behind.
    """
    deleted = conn.execute(f"DELETE FROM {table} WHERE rowid > ?", (keep,)).rowcount
    conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = ?", (keep, table))
    remaining = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return deleted, remaining


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--handoffs",
        action="store_true",
        help=f"also trim handoff_requests back to {HANDOFF_BASELINE}",
    )
    args = parser.parse_args()

    targets = [("refund_requests", 0)]
    if args.handoffs:
        targets.append(("handoff_requests", HANDOFF_BASELINE))

    with sqlite3.connect(support_path()) as conn:
        for table, keep in targets:
            deleted, remaining = _reset(conn, table, keep)
            print(f"{table}: deleted {deleted}, {remaining} remaining")


if __name__ == "__main__":
    main()

"""Push `evals/dataset.py` into LangSmith, idempotently.

    python scripts/upload_dataset.py            # create or sync
    python scripts/upload_dataset.py --dry-run  # show what would change

The local file stays the source of truth. Ground truth here was read out of
Chinook, and a dataset is worse than useless if its expected values drift from
the database — so this pushes one direction and never pulls. Examples added in
the UI from a real trace are the exception worth knowing about: they will show
up here as unmanaged and are reported rather than deleted, because that motion
(trace → dataset) is the point of having the dataset in LangSmith at all.

Each example's slice becomes a LangSmith **split**, so the UI can filter to
"authorization" and the counts on screen match the counts in `evals/run.py`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402
from langsmith import Client  # noqa: E402

from evals.dataset import EXAMPLES, SLICE_SIZES, Example  # noqa: E402

load_dotenv()

DATASET_NAME = "chinook-support-v1"
DESCRIPTION = (
    "Chinook support agent: 30 examples across 6 slices. Every expected value "
    "was read from the database, not written from memory. Safety properties "
    "(tenant isolation, write counts, approval gating) are graded by code, "
    "never by a judge."
)


def _payload(example: Example) -> dict[str, Any]:
    """One example in LangSmith's shape.

    Inputs carry what the agent is given; outputs carry what a grader checks.
    `customer_id` is an input because the harness authenticates with it — the
    agent itself never sees it, which is the property the authorization slice
    exists to test.
    """
    return {
        "inputs": {
            "turns": list(example.turns),
            "customer_id": example.customer_id,
            "approval": example.approval,
        },
        "outputs": {
            "expect_facts": list(example.expect_facts),
            "forbid_facts": list(example.forbid_facts),
            "expect_tools": list(example.expect_tools),
            "forbid_tools": list(example.forbid_tools),
            "expect_writes": example.expect_writes,
        },
        "metadata": {
            "name": example.name,
            "slice": example.slice,
            "notes": example.notes,
        },
        "split": example.slice,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dataset", default=DATASET_NAME)
    args = parser.parse_args()

    client = Client()
    local = {e.name: _payload(e) for e in EXAMPLES}

    if client.has_dataset(dataset_name=args.dataset):
        dataset = client.read_dataset(dataset_name=args.dataset)
        existing = {
            (e.metadata or {}).get("name"): e
            for e in client.list_examples(dataset_id=dataset.id)
        }
    else:
        if args.dry_run:
            print(f"would create dataset {args.dataset!r} with {len(local)} examples")
            return
        dataset = client.create_dataset(args.dataset, description=DESCRIPTION)
        existing = {}

    to_create = [name for name in local if name not in existing]
    to_update = [
        name
        for name in local
        if name in existing and _differs(existing[name], local[name])
    ]
    unmanaged = [name for name in existing if name not in local]

    if args.dry_run:
        print(f"dataset {args.dataset!r}: {len(existing)} example(s) on the server")
        print(f"  create {len(to_create)}: {to_create}")
        print(f"  update {len(to_update)}: {to_update}")
        print(f"  unmanaged (left alone) {len(unmanaged)}: {unmanaged}")
        return

    if to_create:
        client.create_examples(
            dataset_id=dataset.id,
            examples=[local[name] for name in to_create],
        )
    if to_update:
        client.update_examples(
            dataset_id=dataset.id,
            updates=[
                {"id": existing[name].id, **local[name]} for name in to_update
            ],
        )

    print(f"dataset {args.dataset!r}")
    print(f"  created {len(to_create)}, updated {len(to_update)}")
    if unmanaged:
        print(
            f"  {len(unmanaged)} example(s) exist only in LangSmith and were left "
            f"alone — add them to evals/dataset.py to bring them under version "
            f"control: {unmanaged}"
        )
    for name, size in SLICE_SIZES.items():
        print(f"  split {name:14} {size}")
    print(f"\n  https://smith.langchain.com/datasets/{dataset.id}")


def _differs(remote: Any, local: dict[str, Any]) -> bool:
    """Whether the server copy has drifted from the file."""
    return (
        dict(remote.inputs or {}) != local["inputs"]
        or dict(remote.outputs or {}) != local["outputs"]
        or (remote.metadata or {}).get("notes") != local["metadata"]["notes"]
    )


if __name__ == "__main__":
    main()

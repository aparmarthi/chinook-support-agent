"""Run the evaluation set and report counts.

    python -m evals.run                  # everything
    python -m evals.run --slice refund   # one slice
    python -m evals.run --dry-run        # no model calls, checks the harness

Results are reported as **counts against a named denominator** — "6/6 billing"
— because a percentage over a six-example slice implies a precision the sample
does not have, and anyone in the room can do the division and find the
denominator was invented.

Writes go to a throwaway `support.db` so a run never pollutes demo data, and so
refund counts start from a known zero.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path


def _isolate_databases() -> Path:
    """Point the writable database at a temp dir, keeping real Chinook.

    Must run before anything imports the data layer, since the path helpers
    read the environment at call time and the agent module builds a model at
    import.
    """
    scratch = Path(tempfile.mkdtemp(prefix="evals-"))
    real_chinook = Path(__file__).resolve().parent.parent / "data" / "chinook.db"
    if not real_chinook.exists():
        raise SystemExit("data/chinook.db is missing — run scripts/setup_data.py")
    (scratch / "chinook.db").symlink_to(real_chinook)
    os.environ["SUPPORT_DATA_DIR"] = str(scratch)
    return scratch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--slice",
        dest="only",
        action="append",
        help="run only this slice; repeat to run several",
    )
    parser.add_argument(
        "--arm",
        default="flat",
        choices=("flat", "supervisor"),
        help="which architecture to run (ADR-002 experiment)",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="run each example N times; reported per-example so variance is visible",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="also grade tone with an LLM, on examples that passed the code checks",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="grade empty outcomes to check the harness without spending money",
    )
    parser.add_argument(
        "--out",
        default="evals/results",
        help="directory for the timestamped result file",
    )
    args = parser.parse_args()

    _isolate_databases()

    # Imported after the environment is set, not before.
    from evals.dataset import EXAMPLES, SLICE_SIZES
    from evals.evaluators import BLOCKING_KEYS, grade
    from evals.target import RunOutcome, run_example

    judge_tone = None
    if args.judge:
        from evals.judges import judge_tone
    from scripts.setup_data import build_support

    build_support()

    examples = [e for e in EXAMPLES if not args.only or e.slice in args.only]
    if not examples:
        raise SystemExit(f"no examples in slice(s) {args.only!r}")

    total = len(examples) * args.repeat
    print(f"running {len(examples)} example(s) x{args.repeat} on the {args.arm} arm\n")
    rows = []
    index = 0
    for example in examples:
        for attempt in range(1, args.repeat + 1):
            index += 1
            outcome = (
                RunOutcome(answer="", error="dry run")
                if args.dry_run
                else run_example(example, arm=args.arm)
            )
            scored = grade(outcome.as_dict(), example)
            failed = [k for k, v in scored.items() if v["score"] == 0.0]
            blocking = sorted(set(failed) & BLOCKING_KEYS)

            # Gated on purpose: an answer that failed a code check has already
            # been decided, and grading its prose would put a warmth score on
            # the same scoreboard as an authorization failure.
            tone = None
            if args.judge and not failed and not args.dry_run:
                tone = judge_tone(outcome.as_dict(), example)
                scored["tone"] = tone

            mark = "FAIL" if failed else "pass"
            flag = "  <<< BLOCKING" if blocking else ""
            suffix = f" (run {attempt}/{args.repeat})" if args.repeat > 1 else ""
            print(f"  [{index:2}/{total}] {mark}  {example.name}{suffix}{flag}")
            for key in failed:
                print(f"           {key}: {scored[key]['comment']}")
            if tone and tone["failed_checks"]:
                print(
                    f"           tone {tone['score']:.2f}: "
                    f"{', '.join(tone['failed_checks'])} — {tone['comment']}"
                )

            rows.append(
                {
                    "name": example.name,
                    "slice": example.slice,
                    "arm": args.arm,
                    "attempt": attempt,
                    "customer_id": example.customer_id,
                    "outcome": outcome.as_dict(),
                    "scores": scored,
                    "failed": failed,
                    "blocking": blocking,
                }
            )

    _report(rows, SLICE_SIZES, args)


def _report_tone(rows: list[dict]) -> None:
    """Style scores, kept apart from the code checks and from the pass count.

    Reported per failing check rather than as one average, because "3.7 out of
    4" says nothing actionable and "nine replies opened with process instead of
    the answer" is a prompt edit.
    """
    graded = [
        r["scores"]["tone"]
        for r in rows
        if r["scores"].get("tone", {}).get("score") is not None
    ]
    if not graded:
        return
    print(f"\nTONE (judge, {len(graded)} replies that passed every code check)")
    mean = sum(t["score"] for t in graded) / len(graded)
    print(f"  mean {mean:.2f} of 1.00")
    misses: dict[str, int] = defaultdict(int)
    for verdict in graded:
        for check in verdict["failed_checks"]:
            misses[check] += 1
    if not misses:
        print("  every reply passed all four checks")
    for check, count in sorted(misses.items(), key=lambda kv: -kv[1]):
        print(f"  {check:24} failed {count}/{len(graded)}")


def _unstable_examples(rows: list[dict]) -> list[tuple[str, int, int]]:
    """Examples that did not give the same verdict on every run."""
    tally: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        tally[row["name"]].append(not row["failed"])
    return [
        (name, sum(results), len(results))
        for name, results in tally.items()
        if 0 < sum(results) < len(results)
    ]


def _report(rows: list[dict], slice_sizes: dict[str, int], args) -> None:
    """Print counts per slice and per evaluator, then save the run."""
    per_slice: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        per_slice[row["slice"]].append(row)

    print("\n" + "=" * 58)
    print(f"ARM: {args.arm}")
    print("BY SLICE (counts, not percentages)")
    for name, slice_rows in per_slice.items():
        clean = sum(1 for r in slice_rows if not r["failed"])
        denominator = len(slice_rows)
        expected = slice_sizes.get(name, 0) * args.repeat
        note = "" if denominator == expected else f"  (partial run of {expected})"
        print(f"  {name:14} {clean}/{denominator}{note}")

    print("\nBY EVALUATOR")
    totals: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        for key, result in row["scores"].items():
            if result["score"] is not None:
                totals[key].append(result["score"])
    for key, scores in sorted(totals.items()):
        if key == "tone":
            continue  # fractional; reported below rather than rounded to a count
        marker = " (blocking)" if key in {"no_cross_tenant_access"} else ""
        print(f"  {key:26} {int(sum(scores))}/{len(scores)}{marker}")

    _report_tone(rows)

    blocked = [r["name"] for r in rows if r["blocking"]]
    print("\nSAFETY")
    if blocked:
        print(f"  {len(blocked)} BLOCKING failure(s): {blocked}")
    else:
        print("  no blocking failures")

    latencies = sorted(r["outcome"]["latency_seconds"] for r in rows)
    if latencies and any(latencies):
        median = latencies[len(latencies) // 2]
        print(f"\nLATENCY  median {median:.1f}s   max {latencies[-1]:.1f}s")

    # Pre-registered comparison metric: a proxy for the model flailing through
    # delegation rather than acting.
    calls = sorted(len(r["outcome"]["tools_called"]) for r in rows)
    if calls:
        print(
            f"TOOL CALLS  median {calls[len(calls) // 2]}   "
            f"max {calls[-1]}   total {sum(calls)}"
        )

    # Flakiness is a result, not noise to be re-rolled away.
    if args.repeat > 1:
        unstable = _unstable_examples(rows)
        print("\nSTABILITY")
        if unstable:
            for name, passes, runs in unstable:
                print(f"  {name}: passed {passes}/{runs}")
        else:
            print(f"  every example gave the same verdict across {args.repeat} runs")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stamp}-{args.arm}.json"
    path.write_text(
        json.dumps({"generated": stamp, "arm": args.arm, "rows": rows}, indent=2)
    )
    print(f"\nsaved {path}")


if __name__ == "__main__":
    main()

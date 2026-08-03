"""Score the flat-vs-supervisor experiment against pre-registered thresholds.

    python -m evals.compare --flat evals/results/<ts>-flat.json \
                            --supervisor evals/results/<ts>-supervisor.json \
                            --repeats evals/results/repeats

The thresholds live in `docs/ARCHITECTURE.md` §7 and were written **before** the
supervisor existed. They are transcribed here as code so the verdict is computed
rather than argued: the failure mode of "ship the measured winner" is reading
the numbers and constructing a justification for whichever arm you already
preferred, and the defense against that is a decision rule you cannot quietly
revise while looking at the results.

Flat is the incumbent. The supervisor ships only if it clears **every** bar.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

# Uncached OpenAI rates for the model both arms share, per million tokens.
# Cost is compared between arms, so an error here cancels; it is priced anyway
# because "50% more tokens" and "a fifth of a cent" are different arguments.
INPUT_PER_MTOK = 0.20
OUTPUT_PER_MTOK = 1.20


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text())["rows"]


def _cost(row: dict) -> float:
    outcome = row["outcome"]
    return (
        outcome["input_tokens"] / 1_000_000 * INPUT_PER_MTOK
        + outcome["output_tokens"] / 1_000_000 * OUTPUT_PER_MTOK
    )


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else 0.0


def _passes(rows: list[dict]) -> int:
    return sum(1 for r in rows if not r["failed"])


def _by_slice(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["slice"]].append(row)
    return grouped


def _routing_errors(rows: list[dict]) -> int:
    return sum(1 for r in rows if "used_expected_tools" in r["failed"])


def _security_failures(rows: list[dict]) -> int:
    return sum(1 for r in rows if r["blocking"])


def _verdict(met: bool) -> str:
    return "PASS" if met else "FAIL"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flat", required=True, type=Path)
    parser.add_argument("--supervisor", required=True, type=Path)
    parser.add_argument(
        "--repeats",
        type=Path,
        help="directory of --repeat runs, for mixed-intent variance",
    )
    args = parser.parse_args()

    flat, sup = _load(args.flat), _load(args.supervisor)
    results: list[tuple[str, str, bool]] = []

    # --- Primary metric -------------------------------------------------
    flat_mixed = _passes([r for r in flat if r["slice"] == "mixed_intent"])
    sup_mixed = _passes([r for r in sup if r["slice"] == "mixed_intent"])
    flat_total, sup_total = _passes(flat), _passes(sup)
    mixed_gain = sup_mixed - flat_mixed
    total_gain = sup_total - flat_total
    results.append(
        (
            "Mixed-intent completion (+2 slice or +3 overall)",
            f"flat {flat_mixed}, supervisor {sup_mixed} "
            f"(slice {mixed_gain:+d}, overall {total_gain:+d})",
            mixed_gain >= 2 or total_gain >= 3,
        )
    )

    # --- Routing --------------------------------------------------------
    flat_routing, sup_routing = _routing_errors(flat), _routing_errors(sup)
    results.append(
        (
            "Routing errors (strictly fewer)",
            f"flat {flat_routing}, supervisor {sup_routing}"
            + (
                "  [neither arm made one, so the hypothesised gain had "
                "nothing to act on]"
                if flat_routing == sup_routing == 0
                else ""
            ),
            sup_routing < flat_routing,
        )
    )

    # --- Latency --------------------------------------------------------
    flat_p50 = _median([r["outcome"]["latency_seconds"] for r in flat])
    sup_p50 = _median([r["outcome"]["latency_seconds"] for r in sup])
    results.append(
        (
            "p50 latency (<= +2.0s)",
            f"flat {flat_p50:.1f}s, supervisor {sup_p50:.1f}s "
            f"({sup_p50 - flat_p50:+.1f}s)",
            sup_p50 - flat_p50 <= 2.0,
        )
    )

    # --- Cost -----------------------------------------------------------
    flat_cost = sum(_cost(r) for r in flat) / len(flat)
    sup_cost = sum(_cost(r) for r in sup) / len(sup)
    ratio = sup_cost / flat_cost if flat_cost else 0.0
    results.append(
        (
            "Cost per conversation (<= +50%)",
            f"flat ${flat_cost:.4f}, supervisor ${sup_cost:.4f} "
            f"({ratio:.2f}x)",
            ratio <= 1.5,
        )
    )

    # --- Tool calls -----------------------------------------------------
    flat_calls = _median([len(r["outcome"]["tools_called"]) for r in flat])
    sup_calls = _median([len(r["outcome"]["tools_called"]) for r in sup])
    results.append(
        (
            "Tool calls per conversation (<= +2)",
            f"flat {flat_calls:.0f}, supervisor {sup_calls:.0f} "
            f"({sup_calls - flat_calls:+.0f})",
            sup_calls - flat_calls <= 2,
        )
    )

    # --- Security -------------------------------------------------------
    flat_sec, sup_sec = _security_failures(flat), _security_failures(sup)
    results.append(
        (
            "Security failures (exactly zero, both arms)",
            f"flat {flat_sec}, supervisor {sup_sec}",
            flat_sec == 0 and sup_sec == 0,
        )
    )

    # --- No per-workflow regression --------------------------------------
    flat_slices, sup_slices = _by_slice(flat), _by_slice(sup)
    regressions = [
        f"{name} {_passes(sup_slices.get(name, []))} < {_passes(rows)}"
        for name, rows in flat_slices.items()
        if _passes(sup_slices.get(name, [])) < _passes(rows)
    ]
    results.append(
        (
            "No per-workflow regression",
            "none" if not regressions else "; ".join(regressions),
            not regressions,
        )
    )

    # --- Overall ----------------------------------------------------------
    results.append(
        (
            "Overall task resolution (non-decreasing)",
            f"flat {flat_total}/{len(flat)}, supervisor {sup_total}/{len(sup)}",
            sup_total >= flat_total,
        )
    )

    width = max(len(name) for name, _, _ in results)
    print("=" * (width + 46))
    print("FLAT vs SUPERVISOR — thresholds pre-registered in ARCHITECTURE.md §7")
    print("Flat is the incumbent; the supervisor must clear every bar.")
    print("=" * (width + 46))
    for name, detail, met in results:
        print(f"  {_verdict(met):4}  {name:{width}}  {detail}")

    if args.repeats:
        _report_repeats(args.repeats)

    cleared = all(met for _, _, met in results)
    failed = [name for name, _, met in results if not met]
    print()
    if cleared:
        print("VERDICT: supervisor clears every bar. Ship the supervisor.")
    else:
        print(f"VERDICT: supervisor fails {len(failed)} bar(s) — {', '.join(failed)}.")
        print("Flat ships. The supervisor is deleted, not kept as a maybe.")


def _report_repeats(directory: Path) -> None:
    """Per-example stability on the primary slice, three runs per arm."""
    files = sorted(directory.glob("*.json"))
    if not files:
        return
    per_arm: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for path in files:
        payload = json.loads(path.read_text())
        for row in payload["rows"]:
            per_arm[payload["arm"]][row["name"]].append(not row["failed"])

    print("\nMIXED-INTENT STABILITY (3 runs per arm; variance, not significance)")
    names = sorted({n for arm in per_arm.values() for n in arm})
    for name in names:
        cells = "   ".join(
            f"{arm} {sum(per_arm[arm][name])}/{len(per_arm[arm][name])}"
            for arm in sorted(per_arm)
            if per_arm[arm][name]
        )
        print(f"  {name:38} {cells}")


if __name__ == "__main__":
    main()

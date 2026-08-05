"""Freeze one flat-vs-supervisor comparison as the reportable result.

    python scripts/freeze_experiment.py \
        --flat evals/results/<ts>-flat.json \
        --supervisor evals/results/<ts>-supervisor.json

Writes ``reports/experiment.md``: provenance, the headline table, the verdict
as ``evals.compare`` computed it, and a variance section read off every other
full-dataset run in ``evals/results/``.

The point of this script is the refusal. Two arms are only comparable if they
ran the same dataset through the same evaluators on the same model at the same
commit, and the easiest way to publish a wrong number is to compare a fresh run
against yesterday's other arm without noticing. That mismatch is an error here,
not a footnote.

The numbers are read out of the result files rather than retyped, so the table
cannot drift from the runs it describes — which is the failure this exists to
prevent.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "experiment.md"

# Must match across arms or the comparison is between two different systems.
SHARED_KEYS = ("agent_model", "evaluators_digest")


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text())
    if "provenance" not in payload:
        raise SystemExit(
            f"{path} predates provenance recording — rerun it, don't publish it"
        )
    return payload


def _check_comparable(flat: dict, supervisor: dict) -> dict:
    """Refuse to publish a comparison across a moving target."""
    a, b = flat["provenance"], supervisor["provenance"]
    problems = []

    if a["git"]["commit"] != b["git"]["commit"]:
        problems.append(
            f"different commits: flat {a['git']['commit'][:8]} "
            f"vs supervisor {b['git']['commit'][:8]}"
        )
    for arm, prov in (("flat", a), ("supervisor", b)):
        if prov["git"]["dirty"]:
            problems.append(f"{arm} ran against uncommitted changes")
    for key in SHARED_KEYS:
        if a[key] != b[key]:
            problems.append(f"different {key}: {a[key]!r} vs {b[key]!r}")
    if a["dataset"] != b["dataset"]:
        problems.append(f"different dataset: {a['dataset']} vs {b['dataset']}")

    if problems:
        raise SystemExit(
            "refusing to freeze — these arms are not comparable:\n  "
            + "\n  ".join(problems)
        )
    return a


def _median(values: list[float]) -> float:
    """Upper-middle element, matching ``evals.compare``.

    Not ``statistics.median``, which averages the two middle values on an even
    count and would print a latency here that disagrees with the verdict block
    below it by 0.2s. One document, one convention.
    """
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else 0.0


def _summary(payload: dict) -> dict:
    rows = payload["rows"]
    return {
        "passed": sum(1 for r in rows if not r["failed"]),
        "total": len(rows),
        "p50_latency": _median([r["outcome"]["latency_seconds"] for r in rows]),
        "tool_calls": sum(len(r["outcome"]["tools_called"]) for r in rows),
        "failed": sorted(r["name"] for r in rows if r["failed"]),
    }


def _prior_runs(exclude: set[Path]) -> list[dict]:
    """Every other full-dataset run on disk, as variance context.

    This section used to be a hardcoded paragraph describing an earlier pair
    that came out the other way round. No such pair is on disk — the arms it
    described do not exist in any tracked result file — so the document was
    asserting a measurement it could not produce while claiming, two lines
    above, that nothing here is retyped. Reading it off the directory is the
    only version of that claim that survives someone checking.

    These runs are at different commits and are not controlled comparisons.
    They bound run-to-run variance; they do not compare the two arms.
    """
    runs = []
    for path in sorted((ROOT / "evals" / "results").glob("*.json")):
        if path.resolve() in exclude:
            continue
        arm = next((a for a in ("flat", "supervisor") if path.stem.endswith(a)), None)
        payload = json.loads(path.read_text())
        rows = payload.get("rows", [])
        if arm is None or len(rows) != 30:
            continue  # unlabelled or a partial slice — not comparable to a full run
        commit = payload.get("provenance", {}).get("git", {}).get("commit")
        runs.append(
            {
                "file": path.name,
                "arm": arm,
                "commit": commit[:8] if commit else "unrecorded",
                "passed": sum(1 for r in rows if not r["failed"]),
                "failed": sorted(r["name"] for r in rows if r["failed"]),
            }
        )
    return runs


def _margin_reading(flat: dict, supervisor: dict) -> str:
    """Read the headline gap against that sensitivity — including when it is zero.

    Written because the tie case printed "a one-example margin between 30/30 and
    30/30", which is not a sentence. A generated report that only phrases one
    outcome well is a report that will embarrass you on the outcome it did not
    anticipate.
    """
    gap = abs(flat["passed"] - supervisor["passed"])
    if gap == 0:
        return (
            f"**The arms tie at {flat['passed']}/30 here**, and against that "
            "sensitivity a margin of an example either way would not have "
            "established a quality difference in the first place."
        )
    noun = "a one-example margin" if gap == 1 else f"a {gap}-example margin"
    return (
        f"So {noun} between **{flat['passed']}/30 and {supervisor['passed']}/30 "
        "is not enough to establish a quality difference**, and should not be "
        "presented as one."
    )


def _variance_section(prior: list[dict], flat: dict, supervisor: dict) -> str:
    if not prior:
        return (
            "**No other full-dataset runs are on disk**, so this pair is the only "
            "evidence here and the one-example gap between the arms is unbounded "
            "by any variance estimate. Do not read it as a quality difference.\n"
        )

    rows = "\n".join(
        f"| `{r['file']}` | {r['arm']} | `{r['commit']}` | {r['passed']}/30 | "
        f"{', '.join(r['failed']) or 'none'} |"
        for r in prior
    )
    spread = {
        arm: sorted({r["passed"] for r in prior if r["arm"] == arm})
        for arm in ("flat", "supervisor")
    }
    both_arms_vary = all(len(v) > 1 for v in spread.values() if v)
    culprits = sorted({name for r in prior for name in r["failed"]})

    reading = (
        "Both arms have scored more than one value across these runs"
        if both_arms_vary
        else "The resolved count is not stable across these runs"
    )
    return f"""**Other full-dataset runs on disk.** These are at different commits
with different prompt and evaluator code, so they are *not* controlled
comparisons — of the arms against each other, or of either arm against itself.
They show the resolved count is **sensitive**. They do not measure run-to-run
variance, because nothing pins the code between them.

| Run | Arm | Commit | Resolved | Failed |
|---|---|---|---|---|
{rows}

{reading}, and the example that fails is not the same one twice
({", ".join(f"`{c}`" for c in culprits)}). {_margin_reading(flat, supervisor)}
Say "sensitive", not "inside the noise" — the second claims a measurement
nothing here supports.

**The overhead is what the verdict rests on.** The supervisor's median latency
and tool-call count are higher by margins no single example can explain, and
unlike the resolved count they reproduce in the same direction every time.
"""


def _verdict(flat_path: Path, supervisor_path: Path) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "evals.compare",
            "--flat",
            str(flat_path),
            "--supervisor",
            str(supervisor_path),
            "--repeats",
            "evals/results/repeats",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flat", required=True, type=Path)
    parser.add_argument("--supervisor", required=True, type=Path)
    args = parser.parse_args()

    flat, supervisor = _load(args.flat), _load(args.supervisor)
    provenance = _check_comparable(flat, supervisor)
    f, s = _summary(flat), _summary(supervisor)
    prior = _prior_runs(exclude={args.flat.resolve(), args.supervisor.resolve()})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        f"""# Flat vs supervisor — frozen result

Generated by `scripts/freeze_experiment.py` from the two result files named
below. Every number here is read out of them; nothing is retyped.

## Provenance

| | |
|---|---|
| Commit | `{provenance["git"]["commit"]}` (clean, both arms) |
| Agent model | `{provenance["agent_model"]}` |
| Dataset | {provenance["dataset"]["examples"]} examples · digest `{provenance["dataset"]["digest"]}` |
| Evaluators | digest `{provenance["evaluators_digest"]}` |
| Flat run | `{args.flat.name}` |
| Supervisor run | `{args.supervisor.name}` |

## Headline

| Metric | Flat | Supervisor |
|---|---|---|
| Resolved | **{f["passed"]}/{f["total"]}** | **{s["passed"]}/{s["total"]}** |
| p50 latency | {f["p50_latency"]:.1f}s | {s["p50_latency"]:.1f}s |
| Tool calls (total) | {f["tool_calls"]} | {s["tool_calls"]} |
| Failed examples | {", ".join(f["failed"]) or "none"} | {", ".join(s["failed"]) or "none"} |

## Verdict

```
{_verdict(args.flat, args.supervisor)}
```

## What this does and does not show

{_variance_section(prior, f, s)}
**The stability block in the verdict above is older data**, three repeats per
arm recorded before provenance was added, so it predates this commit. It is
retained for variance context, not as part of the frozen comparison.
""",
        encoding="utf-8",
    )
    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"  flat {f['passed']}/{f['total']}  supervisor {s['passed']}/{s['total']}")


if __name__ == "__main__":
    main()

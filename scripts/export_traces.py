"""Export the demo's traces to the repo before LangSmith's retention window closes.

Free-plan traces are kept 14 days. Block 5.3 — the fabricated handoff and its
fix, side by side — is the strongest moment in the demo and it rests entirely
on runs recorded on 3 August. Screenshots are the right artifact for showing on
screen; this is the durable record of what they contained, which is the part
that has to survive being asked "are you sure it called nothing?".

Writes one JSON per run under `reports/traces/` and a readable summary that can
be read aloud if the UI is unavailable. Also re-checks the claim the demo makes
about each before-trace: the reply asserts an action and the trajectory is
empty.

Usage:
    python scripts/export_traces.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langsmith import Client

load_dotenv()

OUT = Path("reports/traces")

RUNS: dict[str, str] = {
    "before-duplicate-charge": "019fc63c-4062-7032-8fc2-7dffc5ce4798",
    "before-missing-download": "019fc63c-47dd-7483-a7d2-f4f96c3130bf",
    "before-account-deletion": "019fc63c-5df2-7c83-9c5a-56875f0fa3c7",
    "after-duplicate-charge": "019fc63e-9d5b-7222-923e-58db84179650",
    "block2-billing": "019fcb48-9d4d-7901-b046-6f34870b65ea",
    "block2-discovery": "019fcb48-c4ab-7eb1-bcce-b06288d832bc",
    "block2-refund-asks-first": "019fcb48-e427-7f23-814e-37fd0305d1d9",
    "block3-injection": "019fcb48-f004-77b1-8583-9d452eeef096",
}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") for b in content if isinstance(b, dict)
        ).strip()
    return str(content)


def _final_answer(outputs: dict[str, Any] | None) -> str:
    messages = (outputs or {}).get("messages") or []
    if not messages:
        return ""
    last = messages[-1]
    return _text(last.get("content") if isinstance(last, dict) else last)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = Client()
    summary: list[str] = ["# Exported demo traces", ""]

    for label, run_id in RUNS.items():
        root = client.read_run(run_id)
        children = list(client.list_runs(trace_id=run_id, is_root=False))
        tools = sorted({r.name for r in children if r.run_type == "tool"})

        payload = {
            "label": label,
            "run_id": run_id,
            "recorded": str(root.start_time),
            "inputs": root.inputs,
            "outputs": root.outputs,
            "tools_called": tools,
            "child_runs": [
                {"name": r.name, "type": r.run_type, "error": r.error}
                for r in children
            ],
        }
        (OUT / f"{label}.json").write_text(json.dumps(payload, indent=2, default=str))

        answer = _final_answer(root.outputs)
        summary += [
            f"## {label}",
            "",
            f"- run: `{run_id}` · recorded {root.start_time:%Y-%m-%d %H:%M} UTC",
            f"- tools called: {', '.join(tools) if tools else '**none**'}",
            f"- reply: {answer[:400]}",
            "",
        ]
        print(f"{label:28} tools={tools or 'NONE'}")

    (OUT / "SUMMARY.md").write_text("\n".join(summary))
    print(f"\nwrote {len(RUNS)} traces + SUMMARY.md to {OUT}/")


if __name__ == "__main__":
    main()

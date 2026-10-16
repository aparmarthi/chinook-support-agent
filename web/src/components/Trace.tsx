import type { ToolCall } from "@/lib/api";

// The two tools that write to support.db. Everything else only reads.
const WRITES = new Set(["create_refund_request", "escalate_to_human"]);

function signature(call: ToolCall) {
  const args = Object.entries(call.args)
    .map(([key, value]) => `${key}=${JSON.stringify(value)}`)
    .join(", ");
  return `${call.name}(${args})`;
}

export function Trace({ calls }: { calls: ToolCall[] }) {
  if (calls.length === 0) {
    return <p className="text-xs text-ink-dim">No tools called. Answered from the conversation.</p>;
  }
  return (
    <ol className="space-y-1" aria-label="Tools the agent called">
      {calls.map((call, index) => {
        const write = WRITES.has(call.name);
        return (
          <li key={index}>
            <details className="group">
              <summary className="flex cursor-pointer list-none items-start gap-2 rounded px-1 py-0.5 font-mono text-[12.5px] leading-5 text-ink-dim hover:bg-white/[0.04] hover:text-ink">
                <span
                  aria-hidden
                  className={`mt-[7px] size-1.5 shrink-0 rounded-full ${write ? "bg-paper" : "bg-signal"}`}
                />
                <span className="sr-only">{write ? "Write:" : "Read:"}</span>
                <span className="[overflow-wrap:anywhere]">{signature(call)}</span>
              </summary>
              <pre className="mt-1 mb-2 ml-4 max-h-64 overflow-auto rounded border border-line bg-black/30 p-3 font-mono text-xs leading-5 whitespace-pre-wrap text-ink-dim">
                {call.output ?? "Waiting on the reviewer. This call has not run."}
              </pre>
            </details>
          </li>
        );
      })}
    </ol>
  );
}

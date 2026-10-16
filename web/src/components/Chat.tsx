"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api, ApiError, type Session, type Turn } from "@/lib/api";
import type { Persona } from "@/lib/personas";
import { ApprovalSlip, type Decision } from "./ApprovalSlip";
import { Trace } from "./Trace";

type Entry =
  | { kind: "user"; text: string }
  | { kind: "agent"; turn: Turn; decision: Decision | null }
  | { kind: "notice"; text: string };

type Props = {
  persona: Persona;
  session: Session;
  conversationId: string;
  onExpired: () => void;
  onSwitch: () => void;
};

export function Chat({ persona, session, conversationId: initialId, onExpired, onSwitch }: Props) {
  const [conversationId, setConversationId] = useState(initialId);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [left, setLeft] = useState(session.message_limit);
  const [failed, setFailed] = useState<{ text: string; retry: () => void } | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const pending = entries.some((e) => e.kind === "agent" && e.turn.pending_approval && !e.decision);
  const outOfMessages = left <= 0;

  useEffect(() => {
    if (entries.length) endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [entries, busy]);

  async function run(call: (id: string) => Promise<Turn>) {
    setBusy(true);
    setFailed(null);
    try {
      const turn = await call(conversationId);
      setLeft(turn.messages_left);
      setEntries((prev) => [...prev, { kind: "agent", turn, decision: null }]);
    } catch (err) {
      await recover(err, () => run(call));
    } finally {
      setBusy(false);
    }
  }

  async function recover(err: unknown, retry: () => void) {
    const status = err instanceof ApiError ? err.status : 0;
    const text = err instanceof Error ? err.message : "Something went wrong.";
    if (status === 401) return onExpired();
    if (status === 404) {
      // The server restarted, and conversations live in its memory.
      let fresh: string;
      try {
        fresh = (await api.startConversation(session.token)).conversation_id;
      } catch (reopenErr) {
        if (reopenErr instanceof ApiError && reopenErr.status === 401) return onExpired();
        const reason = reopenErr instanceof Error ? reopenErr.message : text;
        return setFailed({ text: reason, retry });
      }
      setConversationId(fresh);
      setEntries((prev) => [
        ...prev,
        { kind: "notice", text: "The server restarted, so this conversation was reset. Send that again." },
      ]);
      return;
    }
    if (status === 429) {
      setLeft(0);
      setEntries((prev) => [...prev, { kind: "notice", text }]);
      return;
    }
    setFailed({ text, retry });
  }

  function send(text: string) {
    const message = text.trim();
    if (!message || busy || pending || outOfMessages) return;
    setDraft("");
    setEntries((prev) => [...prev, { kind: "user", text: message }]);
    run((id) => api.send(session.token, id, message));
  }

  function decide(index: number, decision: Decision) {
    setEntries((prev) =>
      prev.map((e, i) => (i === index && e.kind === "agent" ? { ...e, decision } : e)),
    );
    run((id) => api.decide(session.token, id, decision));
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-10 border-b border-line bg-console/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-6 gap-y-2 px-6 py-3 text-sm">
          <p className="flex items-center gap-2">
            <span aria-hidden className="size-2 rounded-full bg-signal" />
            Signed in as <strong className="font-semibold">{persona.name}</strong>
          </p>
          <div className="flex items-center gap-5 text-ink-dim">
            <span aria-live="polite">
              {left} {left === 1 ? "message" : "messages"} left
            </span>
            <button type="button" onClick={onSwitch} className="font-semibold text-ink hover:text-signal">
              Switch customer
            </button>
          </div>
        </div>
      </header>

      <div className="mx-auto grid w-full max-w-6xl flex-1 gap-10 px-6 py-8 lg:grid-cols-[minmax(0,1fr)_320px]">
        <main className="flex min-w-0 flex-col">
          <div className="flex-1 space-y-8" aria-live="polite">
            {entries.length === 0 && (
              <div>
                <p className="max-w-[60ch] text-ink-dim">
                  Ask about {persona.name.split(" ")[0]}&apos;s account in your own words, or start with
                  one of these. Each reply shows the tools the agent called; open one to see exactly
                  what came back from the database.
                </p>
                <div className="mt-6 lg:hidden">
                  <Probes persona={persona} disabled={busy} onPick={send} />
                </div>
              </div>
            )}
            {entries.map((entry, index) => (
              <EntryView key={index} entry={entry} busy={busy} onDecide={(d) => decide(index, d)} />
            ))}
            {busy && (
              <p className="typing text-2xl leading-none text-ink-dim" aria-label="The agent is working">
                <span>.</span>
                <span>.</span>
                <span>.</span>
              </p>
            )}
            {failed && (
              <div role="alert" className="text-sm text-[#fca5a5]">
                {failed.text}{" "}
                <button type="button" onClick={failed.retry} className="font-semibold underline">
                  Try again
                </button>
              </div>
            )}
            <div ref={endRef} className="scroll-mb-40" />
          </div>

          <form
            className="sticky bottom-0 mt-8 bg-console pt-2 pb-6"
            onSubmit={(e) => {
              e.preventDefault();
              send(draft);
            }}
          >
            <label htmlFor="message" className="sr-only">
              Message the support agent
            </label>
            <div className="flex gap-2 rounded-lg border border-line bg-raised p-2 focus-within:border-signal/50">
              <textarea
                id="message"
                rows={2}
                maxLength={1000}
                value={draft}
                disabled={pending || outOfMessages}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send(draft);
                  }
                }}
                placeholder={
                  outOfMessages
                    ? "This session is out of messages."
                    : pending
                      ? "Approve or decline the refund first."
                      : "Ask about invoices, music, or a refund"
                }
                className="flex-1 resize-none bg-transparent px-2 py-1.5 text-[15px] placeholder:text-ink-dim/70 focus:outline-none disabled:cursor-not-allowed"
              />
              <button
                type="submit"
                disabled={busy || pending || outOfMessages || !draft.trim()}
                className="self-end rounded bg-ink px-4 py-2 text-sm font-semibold text-console hover:bg-white disabled:opacity-40"
              >
                Send
              </button>
            </div>
          </form>
        </main>

        <aside className="lg:sticky lg:top-20 lg:self-start" aria-labelledby="probes">
          <h2 id="probes" className="font-semibold">
            Things to try
          </h2>
          <div className="mt-4">
            <Probes persona={persona} disabled={busy || pending || outOfMessages} onPick={send} />
          </div>
          <p className="mt-6 flex items-center gap-4 text-xs text-ink-dim">
            <span className="flex items-center gap-1.5">
              <span aria-hidden className="size-1.5 rounded-full bg-signal" /> reads
            </span>
            <span className="flex items-center gap-1.5">
              <span aria-hidden className="size-1.5 rounded-full bg-paper" /> writes a ticket
            </span>
          </p>
        </aside>
      </div>
    </div>
  );
}

function Probes({
  persona,
  disabled,
  onPick,
}: {
  persona: Persona;
  disabled: boolean;
  onPick: (prompt: string) => void;
}) {
  return (
    <ul className="space-y-2">
      {persona.probes.map((probe) => (
        <li key={probe.prompt}>
          <button
            type="button"
            disabled={disabled}
            onClick={() => onPick(probe.prompt)}
            className="w-full rounded-md border border-line px-4 py-3 text-left hover:border-signal/50 hover:bg-white/[0.03] disabled:opacity-50"
          >
            <span className="block text-sm leading-snug">{probe.prompt}</span>
            <span className="mt-1.5 block text-xs leading-snug text-ink-dim">{probe.tests}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function EntryView({
  entry,
  busy,
  onDecide,
}: {
  entry: Entry;
  busy: boolean;
  onDecide: (decision: Decision) => void;
}) {
  if (entry.kind === "user") {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-lg bg-raised px-4 py-3 text-[15px] leading-relaxed whitespace-pre-wrap">
          {entry.text}
        </p>
      </div>
    );
  }
  if (entry.kind === "notice") {
    return <p className="border-l-2 border-line pl-4 text-sm text-ink-dim">{entry.text}</p>;
  }
  const { turn } = entry;
  return (
    <article className="max-w-[68ch]">
      <Trace calls={turn.tool_calls} />
      {turn.reply && (
        <div className="reply mt-3 text-[15px] leading-relaxed">
          <ReactMarkdown>{turn.reply}</ReactMarkdown>
        </div>
      )}
      {turn.pending_approval && (
        <ApprovalSlip card={turn.pending_approval} decision={entry.decision} busy={busy} onDecide={onDecide} />
      )}
      <p className="mt-2 text-xs text-ink-dim">{(turn.latency_ms / 1000).toFixed(1)}s</p>
    </article>
  );
}

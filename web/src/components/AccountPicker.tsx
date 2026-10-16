import type { PersonaKey } from "@/lib/api";
import { PERSONAS } from "@/lib/personas";
import { Footer } from "./Footer";

type Props = {
  busy: PersonaKey | null;
  error: string | null;
  onPick: (key: PersonaKey) => void;
};

const ENFORCED = [
  ["Scoped to one customer.", "The agent's tools are bound to whoever signed in. None of them accepts a customer id."],
  ["Refunds wait for a person.", "Nothing is filed until a reviewer approves it, and a retried approval can't file twice."],
  ["Read-only store data.", "The catalog and invoices open read-only. The agent's only writes are support tickets."],
];

export function AccountPicker({ busy, error, onPick }: Props) {
  return (
    <main className="mx-auto flex min-h-screen max-w-5xl flex-col px-6 pt-16 pb-10 sm:pt-24">
      <p className="text-sm text-ink-dim">Tenant-Safe Support Agent, on the Chinook music-store dataset</p>
      <h1 className="mt-4 max-w-3xl text-4xl leading-[1.08] font-semibold tracking-tight text-balance sm:text-[56px]">
        Sign in as a customer. Then try to see someone else&apos;s account.
      </h1>
      <p className="mt-6 max-w-[62ch] text-lg leading-relaxed text-ink-dim">
        This agent handles support for a digital music store: billing questions, recommendations,
        refunds, and handoffs to a person. It can only read the account you sign in as. That limit is
        in the code, not the prompt, so asking nicely won&apos;t move it.
      </p>

      <div className="mt-12 grid gap-4 sm:grid-cols-2">
        {PERSONAS.map((p) => (
          <button
            key={p.key}
            type="button"
            disabled={busy !== null}
            onClick={() => onPick(p.key)}
            className="group rounded-lg border border-line bg-raised p-6 text-left transition-colors hover:border-signal/50 disabled:opacity-60"
          >
            <span className="block text-2xl font-semibold">{p.name}</span>
            <span className="mt-1 block text-ink-dim">{p.place}</span>
            <span className="mt-1 block text-sm text-ink-dim">{p.history}</span>
            <span className="mt-6 inline-block text-sm font-semibold text-signal group-hover:underline">
              {busy === p.key ? "Signing in…" : `Sign in as ${p.name.split(" ")[0]}`}
            </span>
          </button>
        ))}
      </div>
      {error && (
        <p role="alert" className="mt-4 text-sm text-[#fca5a5]">
          {error}
        </p>
      )}

      <dl className="mt-16 grid gap-8 border-t border-line pt-8 sm:grid-cols-3">
        {ENFORCED.map(([title, body]) => (
          <div key={title}>
            <dt className="font-semibold">{title}</dt>
            <dd className="mt-2 text-sm leading-relaxed text-ink-dim">{body}</dd>
          </div>
        ))}
      </dl>

      <Footer />
    </main>
  );
}

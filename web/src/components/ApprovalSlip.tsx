export type Decision = "approve" | "reject";

type Props = {
  card: string;
  decision: Decision | null;
  busy: boolean;
  onDecide: (decision: Decision) => void;
};

export function ApprovalSlip({ card, decision, busy, onDecide }: Props) {
  return (
    <section
      aria-label="Refund approval"
      className="slip relative mt-4 max-w-md rounded-b-md px-6 pt-7 pb-5 text-paper-ink shadow-[0_18px_40px_-20px_rgba(0,0,0,0.8)]"
    >
      <h3 className="text-[15px] font-semibold">Refund needs approval</h3>
      <p className="mt-1 text-sm text-paper-ink/70">
        You&apos;re the reviewer here. In production this slip goes to a support rep, never to the
        customer who asked.
      </p>
      <pre className="mt-4 border-y border-dashed border-paper-ink/25 py-3 font-mono text-[12.5px] leading-6 whitespace-pre-wrap">
        {card}
      </pre>

      {decision === null ? (
        <div className="mt-4 flex flex-wrap gap-3">
          <button
            type="button"
            disabled={busy}
            onClick={() => onDecide("approve")}
            className="rounded bg-ballpoint px-4 py-2 text-sm font-semibold text-white hover:bg-[#2340b0] disabled:opacity-50 focus-visible:outline-ballpoint"
          >
            Approve refund
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onDecide("reject")}
            className="rounded border border-paper-ink/30 px-4 py-2 text-sm font-semibold hover:bg-paper-ink/5 disabled:opacity-50 focus-visible:outline-ballpoint"
          >
            Decline
          </button>
        </div>
      ) : (
        <p
          role="status"
          className="stamp pointer-events-none absolute right-6 bottom-5 rounded-sm border-[3px] border-ballpoint px-3 py-1 text-lg font-bold tracking-wide text-ballpoint"
        >
          {decision === "approve" ? "Approved" : "Declined"}
        </p>
      )}
      {decision !== null && <div className="h-10" aria-hidden />}
    </section>
  );
}

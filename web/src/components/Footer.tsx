export function Footer() {
  return (
    <footer className="mt-auto flex flex-col gap-2 pt-16 text-sm text-ink-dim sm:flex-row sm:justify-between">
      <p>Conversations are traced in LangSmith for debugging. Don&apos;t type anything personal.</p>
      <p className="flex gap-5">
        <a className="hover:text-ink" href="https://github.com/aparmarthi/tenant-safe-support-agent">
          Source on GitHub
        </a>
        <a className="hover:text-ink" href="https://ameyparmarthi.com">
          Built by Amey Parmarthi
        </a>
      </p>
    </footer>
  );
}

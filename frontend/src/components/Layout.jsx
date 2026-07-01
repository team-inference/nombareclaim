export default function Layout({ children }) {
  return (
    <div className="min-h-screen bg-paper">
      <header className="sticky top-0 z-20 border-b border-line bg-surface/90 backdrop-blur-sm">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
          <div className="flex items-baseline gap-3">
            <div className="flex items-center gap-2">
              <span className="flex h-7 w-7 items-center justify-center rounded-md bg-action text-paper">
                <svg width="14" height="14" viewBox="0 0 32 32" fill="none" aria-hidden="true">
                  <path
                    d="M9 21V11l9 10V11"
                    stroke="currentColor"
                    strokeWidth="2.6"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </span>
              <span className="font-display text-lg font-semibold tracking-tight text-ink">
                NombaReclaim
              </span>
            </div>
            <span className="hidden text-sm text-ink-faint sm:inline">
              Turning failed payments into recovered revenue
            </span>
          </div>
          <span className="rounded-full border border-line px-3 py-1 font-ledger text-xs text-ink-muted">
            Team Inference
          </span>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
    </div>
  )
}

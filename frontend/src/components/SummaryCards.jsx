import { formatNaira } from '../lib/format'
import { useCountUp } from '../lib/useCountUp'

function Card({ label, value, accent = false, sub }) {
  return (
    <div
      className={`rounded-lg border p-5 transition-colors ${
        accent ? 'border-action bg-action-soft' : 'border-line bg-surface'
      }`}
    >
      <p
        className={`text-xs font-medium uppercase tracking-wide ${
          accent ? 'text-action' : 'text-ink-faint'
        }`}
      >
        {label}
      </p>
      <p
        className={`font-ledger mt-2 text-[1.75rem] font-semibold leading-none ${
          accent ? 'text-action' : 'text-ink'
        }`}
      >
        {value}
      </p>
      {sub && <p className="mt-1.5 text-xs text-ink-faint">{sub}</p>}
    </div>
  )
}

export default function SummaryCards({ summary, loading }) {
  // Hooks must run unconditionally — fall back to 0 while summary is
  // absent, the loading/empty branches below decide what's shown.
  const totalFailed = useCountUp(summary?.total_failed_amount ?? 0)
  const estimatedRecoverable = useCountUp(summary?.estimated_recoverable_amount ?? 0)
  const recovered = useCountUp(summary?.recovered_amount ?? 0)
  const recoveryRate = useCountUp(summary?.recovery_rate ?? 0)

  if (loading) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-[104px] animate-pulse rounded-lg border border-line bg-surface" />
        ))}
      </div>
    )
  }

  if (!summary) return null

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Card
        label="Total Failed This Month"
        value={formatNaira(Math.round(totalFailed), summary.currency)}
        sub={`${summary.total_failed_count} transactions`}
      />
      <Card
        label="Estimated Recoverable"
        value={formatNaira(Math.round(estimatedRecoverable), summary.currency)}
        sub="High-probability failures"
      />
      <Card
        label="Recovered So Far"
        value={formatNaira(Math.round(recovered), summary.currency)}
        sub={`This ${summary.period}`}
      />
      <Card
        label="Recovery Rate"
        value={`${recoveryRate.toFixed(1)}%`}
        sub="Of failed payments won back"
        accent
      />
    </div>
  )
}

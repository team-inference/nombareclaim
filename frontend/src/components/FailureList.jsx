import { formatNaira, classificationLabel, formatDate } from '../lib/format'
import StatusBadge from './StatusBadge'

export default function FailureList({ failures, loading, selectedId, onSelect, statusFilter, onStatusFilterChange, justUpdatedId }) {
  const filters = [
    { value: '', label: 'All' },
    { value: 'NEW', label: 'New' },
    { value: 'CLASSIFIED', label: 'Classified' },
    { value: 'RECOVERY_TRIGGERED', label: 'Recovery Sent' },
    { value: 'RECOVERED', label: 'Recovered' },
    { value: 'EXPIRED', label: 'Expired' },
  ]

  return (
    <div className="rounded-lg border border-line bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4">
        <p className="font-display text-sm font-semibold text-ink">Failed Transactions</p>
        <div className="flex flex-wrap gap-1.5">
          {filters.map((f) => (
            <button
              key={f.value}
              onClick={() => onStatusFilterChange(f.value)}
              className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                statusFilter === f.value
                  ? 'bg-ink text-paper'
                  : 'bg-paper text-ink-muted hover:bg-status-new-bg'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="divide-y divide-line">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="h-16 animate-pulse px-5 py-4">
              <div className="h-4 w-2/3 rounded bg-line/60" />
            </div>
          ))}
        </div>
      ) : failures.length === 0 ? (
        <div className="px-5 py-14 text-center">
          <p className="text-sm font-medium text-ink">No failed transactions here</p>
          <p className="mt-1 text-sm text-ink-faint">
            {statusFilter
              ? 'Try a different filter, or check back once new events come in.'
              : 'Once a payment fails, it will show up in this list within moments.'}
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-faint">
                <th className="px-5 py-3 font-medium">Amount</th>
                <th className="px-5 py-3 font-medium">Reason</th>
                <th className="px-5 py-3 font-medium">Score</th>
                <th className="px-5 py-3 font-medium">Status</th>
                <th className="px-5 py-3 font-medium">Date</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {failures.map((f) => (
                <tr
                  key={f.id}
                  onClick={() => onSelect(f.id)}
                  className={`cursor-pointer transition-colors hover:bg-paper ${
                    selectedId === f.id ? 'bg-action-soft' : ''
                  } ${justUpdatedId === f.id ? 'animate-row-flash' : ''}`}
                >
                  <td className="font-ledger px-5 py-3.5 font-medium text-ink">
                    {formatNaira(f.amount, f.currency)}
                  </td>
                  <td className="px-5 py-3.5 text-ink-muted">{classificationLabel(f.classification)}</td>
                  <td className="font-ledger px-5 py-3.5 text-ink-muted">
                    {f.recovery_score ?? '—'}
                  </td>
                  <td className="px-5 py-3.5">
                    <StatusBadge status={f.status} />
                  </td>
                  <td className="px-5 py-3.5 text-ink-faint">{formatDate(f.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

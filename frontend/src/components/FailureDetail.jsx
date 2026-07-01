import { useEffect, useState } from 'react'
import { formatNaira, classificationLabel, formatDateTime } from '../lib/format'
import StatusBadge from './StatusBadge'
import { getFailureById, triggerRecovery } from '../api/failures'

export default function FailureDetail({ eventId, onClose, onRecovered }) {
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [triggering, setTriggering] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getFailureById(eventId)
      .then((d) => {
        if (!cancelled) setDetail(d)
      })
      .catch(() => {
        if (!cancelled) setError('Could not load this transaction. Try again.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [eventId])

  async function handleTrigger() {
    if (triggering || !detail) return
    setTriggering(true)
    setError(null)
    try {
      const result = await triggerRecovery(detail.id)
      setDetail((prev) => ({
        ...prev,
        status: result.status,
        recovery_checkout_url: result.recovery_checkout_url,
      }))
      onRecovered?.(detail.id)
    } catch {
      setError('Could not trigger recovery. Try again in a moment.')
    } finally {
      setTriggering(false)
    }
  }

  return (
    <aside className="rounded-lg border border-line bg-surface">
      <div className="flex items-center justify-between border-b border-line px-5 py-4">
        <p className="font-display text-sm font-semibold text-ink">Transaction Detail</p>
        <button
          onClick={onClose}
          aria-label="Close detail panel"
          className="rounded p-1 text-ink-faint hover:bg-paper hover:text-ink"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </button>
      </div>

      <div className="px-5 py-5">
        {loading ? (
          <div className="space-y-3">
            <div className="h-6 w-1/2 animate-pulse rounded bg-line/60" />
            <div className="h-4 w-1/3 animate-pulse rounded bg-line/60" />
            <div className="h-20 animate-pulse rounded bg-line/60" />
          </div>
        ) : !detail ? (
          <p className="text-sm text-ink-faint">Nothing to show.</p>
        ) : (
          <div className="space-y-5">
            <div>
              <p className="font-ledger text-2xl font-semibold text-ink">
                {formatNaira(detail.amount, detail.currency)}
              </p>
              <p className="mt-1 text-xs text-ink-faint font-ledger">{detail.nomba_transaction_id}</p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={detail.status} />
              <span className="text-xs text-ink-faint">
                {classificationLabel(detail.classification)}
              </span>
            </div>

            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">
                Recovery Score
              </p>
              <div className="mt-1.5 flex items-center gap-2">
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-status-new-bg">
                  <div
                    className="h-full rounded-full bg-action"
                    style={{ width: `${detail.recovery_score ?? 0}%` }}
                  />
                </div>
                <span className="font-ledger text-sm font-medium text-ink">
                  {detail.recovery_score ?? '—'}
                </span>
              </div>
            </div>

            {detail.recovery_message && (
              <div>
                <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">
                  Recovery Message
                </p>
                <p className="mt-1.5 rounded-md bg-paper p-3 text-sm text-ink-muted">
                  {detail.recovery_message}
                </p>
              </div>
            )}

            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">
                Failed On
              </p>
              <p className="mt-1 text-sm text-ink-muted">{formatDateTime(detail.created_at)}</p>
            </div>

            {error && (
              <p className="rounded-md bg-status-expired-bg px-3 py-2 text-sm text-status-expired-fg">
                {error}
              </p>
            )}

            {detail.status === 'RECOVERED' ? (
              <div className="rounded-md bg-action-soft px-3 py-2.5 text-sm font-medium text-action">
                Payment recovered
                {detail.recovered_at ? ` on ${formatDateTime(detail.recovered_at)}` : ''}.
              </div>
            ) : detail.recovery_checkout_url ? (
              <div className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">
                  Recovery Checkout Link
                </p>
                <a
                  href={detail.recovery_checkout_url}
                  target="_blank"
                  rel="noreferrer"
                  className="block truncate rounded-md border border-line bg-paper px-3 py-2 text-sm text-action hover:underline"
                >
                  {detail.recovery_checkout_url}
                </a>
              </div>
            ) : (
              <button
                onClick={handleTrigger}
                disabled={triggering}
                className="w-full rounded-md bg-action px-4 py-2.5 text-sm font-medium text-paper transition-colors hover:bg-action-hover disabled:cursor-not-allowed disabled:opacity-60"
              >
                {triggering ? 'Sending recovery link…' : 'Trigger Recovery'}
              </button>
            )}
          </div>
        )}
      </div>
    </aside>
  )
}

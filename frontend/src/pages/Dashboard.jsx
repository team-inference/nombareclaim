import { useCallback, useEffect, useState } from 'react'
import SummaryCards from '../components/SummaryCards'
import RecoveryRateChart from '../components/RecoveryRateChart'
import ClassificationBreakdownChart from '../components/ClassificationBreakdownChart'
import FailureList from '../components/FailureList'
import FailureDetail from '../components/FailureDetail'
import { getSummary, getFailures, getRecoveryTrend, getClassificationBreakdown, getExportUrl } from '../api/failures'

export default function Dashboard() {
  const [summary, setSummary] = useState(null)
  const [summaryLoading, setSummaryLoading] = useState(true)
  const [trend, setTrend] = useState(null)
  const [trendLoading, setTrendLoading] = useState(true)
  const [breakdown, setBreakdown] = useState(null)
  const [breakdownLoading, setBreakdownLoading] = useState(true)

  const [failures, setFailures] = useState([])
  const [failuresLoading, setFailuresLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState('')
  const [selectedId, setSelectedId] = useState(null)

  const [error, setError] = useState(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [justUpdatedId, setJustUpdatedId] = useState(null)

  const loadAll = useCallback(() => {
    setError(null)

    setSummaryLoading(true)
    getSummary()
      .then(setSummary)
      .catch(() => setError('Could not reach the server. The dashboard may be showing stale data.'))
      .finally(() => setSummaryLoading(false))

    setTrendLoading(true)
    getRecoveryTrend()
      .then(setTrend)
      .finally(() => setTrendLoading(false))

    setBreakdownLoading(true)
    getClassificationBreakdown()
      .then(setBreakdown)
      .finally(() => setBreakdownLoading(false))

    setFailuresLoading(true)
    getFailures({ status: statusFilter || undefined })
      .then((res) => setFailures(res.results))
      .catch(() => setError('Could not reach the server. The dashboard may be showing stale data.'))
      .finally(() => setFailuresLoading(false))
  }, [statusFilter])

  useEffect(() => {
    loadAll()
  }, [loadAll, refreshKey])

  function handleRefresh() {
    setRefreshKey((k) => k + 1)
  }

  function handleRecovered(id) {
    // A manual refresh is the accepted hackathon-scope solution here
    // (no polling/websockets) — reload the list + summary so a
    // triggered/recovered status shows up without a full page reload.
    setRefreshKey((k) => k + 1)
    setJustUpdatedId(id)
    window.setTimeout(() => setJustUpdatedId(null), 1800)
  }

  return (
    <div className="space-y-6">
      {error && (
        <div className="flex items-center justify-between rounded-md border border-status-expired-fg/30 bg-status-expired-bg px-4 py-3 text-sm text-status-expired-fg">
          <span>{error}</span>
          <button onClick={handleRefresh} className="font-medium underline underline-offset-2">
            Retry
          </button>
        </div>
      )}

      <div className="flex items-center justify-between">
        <h1 className="font-display text-xl font-semibold text-ink">Dashboard</h1>
        <div className="flex items-center gap-2">
          <a
            href={getExportUrl()}
            className="flex items-center gap-1.5 rounded-md border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink-muted hover:bg-paper"
          >
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path
                d="M8 2v8m0 0l3-3m-3 3L5 7M3 12h10"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            Export CSV
          </a>
          <button
            onClick={handleRefresh}
            className="flex items-center gap-1.5 rounded-md border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink-muted hover:bg-paper"
          >
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path
                d="M13.5 8a5.5 5.5 0 11-1.6-3.9M13.5 3v3.5H10"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            Refresh
          </button>
        </div>
      </div>

      <SummaryCards summary={summary} loading={summaryLoading} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <RecoveryRateChart data={trend} loading={trendLoading} />
        <ClassificationBreakdownChart data={breakdown} loading={breakdownLoading} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <FailureList
            failures={failures}
            loading={failuresLoading}
            selectedId={selectedId}
            onSelect={setSelectedId}
            statusFilter={statusFilter}
            justUpdatedId={justUpdatedId}
            onStatusFilterChange={(v) => {
              setStatusFilter(v)
              setSelectedId(null)
            }}
          />
        </div>
        <div>
          {selectedId ? (
            <FailureDetail
              eventId={selectedId}
              onClose={() => setSelectedId(null)}
              onRecovered={handleRecovered}
            />
          ) : (
            <div className="flex h-full min-h-[220px] items-center justify-center rounded-lg border border-dashed border-line bg-surface p-6 text-center">
              <p className="text-sm text-ink-faint">
                Select a transaction from the list to see its recovery details.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

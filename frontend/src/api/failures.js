import { apiClient } from './client'
import { mockSummary, mockFailures, mockFailureDetail, mockTrend, mockBreakdown } from '../mocks/mockData'

// Small helper to fake realistic network latency in mock mode, so
// loading states are actually visible and testable during dev.
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

export async function getSummary() {
  if (apiClient.useMocks) {
    await wait(250)
    return mockSummary
  }
  return apiClient.get('/api/summary')
}

export async function getFailures({ status, page = 1, pageSize = 20 } = {}) {
  if (apiClient.useMocks) {
    await wait(300)
    let results = [...mockFailures]
    if (status) results = results.filter((f) => f.status === status)
    results.sort((a, b) => (b.recovery_score ?? 0) - (a.recovery_score ?? 0))
    const total = results.length
    const start = (page - 1) * pageSize
    return {
      results: results.slice(start, start + pageSize),
      total,
      page,
      page_size: pageSize,
    }
  }
  const params = new URLSearchParams()
  if (status) params.set('status', status)
  params.set('page', String(page))
  params.set('page_size', String(pageSize))
  return apiClient.get(`/api/failures?${params.toString()}`)
}

export async function getFailureById(id) {
  if (apiClient.useMocks) {
    await wait(200)
    const detail = mockFailureDetail(id)
    if (!detail) throw new Error('not found')
    return detail
  }
  return apiClient.get(`/api/failures/${id}`)
}

export async function triggerRecovery(id) {
  if (apiClient.useMocks) {
    await wait(700)
    const result = {
      id,
      status: 'RECOVERY_TRIGGERED',
      recovery_checkout_url: `https://checkout.nomba.com/pay/mock-${id}`,
      triggered_at: new Date().toISOString(),
    }
    // Mutate the in-memory fixture too, so a subsequent list refresh
    // (and the detail panel re-fetch) both see the same state — mocks
    // otherwise silently diverge from what a real backend would do,
    // which would look like a bug on a demo run against mocks alone.
    const entry = mockFailures.find((f) => f.id === id)
    if (entry) {
      entry.status = result.status
      entry.recovery_checkout_url = result.recovery_checkout_url
    }
    return result
  }
  return apiClient.post(`/api/failures/${id}/trigger-recovery`)
}

export async function getRecoveryTrend() {
  // As of this build, /api/summary/trend is a real backend endpoint
  // (cumulative recovery rate over the last N days) — the mock
  // fallback below now only triggers on an actual network/API error,
  // not silently by design as it did previously.
  if (apiClient.useMocks) {
    await wait(200)
    return mockTrend
  }
  try {
    return await apiClient.get('/api/summary/trend')
  } catch {
    return mockTrend
  }
}

export async function getClassificationBreakdown() {
  if (apiClient.useMocks) {
    await wait(220)
    return mockBreakdown
  }
  return apiClient.get('/api/analytics/breakdown')
}

export function getExportUrl() {
  return `${apiClient.baseUrl}/api/export`
}

// Static fixtures matching the API contract exactly (see
// 00_master_prompt_team_brief.md "shared API contract"). Used whenever
// VITE_USE_MOCKS=true, so the dashboard is fully buildable and
// demoable before the real backend is wired up.

export const mockSummary = {
  total_failed_count: 42,
  total_failed_amount: 847000,
  estimated_recoverable_amount: 312000,
  recovered_amount: 45000,
  recovery_rate: 34.2,
  currency: 'NGN',
  period: 'month',
}

export const mockFailures = [
  {
    id: 'f1a2b3c4-0001',
    nomba_transaction_id: 'NMB-TXN-88213',
    amount: 65000,
    currency: 'NGN',
    classification: 'NETWORK_TIMEOUT',
    recovery_score: 92,
    status: 'CLASSIFIED',
    recovery_message:
      "Hi! Your ₦65,000 payment didn't complete due to a network hiccup on our end, not yours. Please try again with this fresh link.",
    created_at: '2026-06-28T09:14:00Z',
    recovered_at: null,
  },
  {
    id: 'f1a2b3c4-0002',
    nomba_transaction_id: 'NMB-TXN-88214',
    amount: 18500,
    currency: 'NGN',
    classification: 'INSUFFICIENT_FUNDS',
    recovery_score: 78,
    status: 'CLASSIFIED',
    recovery_message:
      "Hi! Your ₦18,500 payment didn't go through, looks like a funds issue. Here's a fresh link to complete it whenever you're ready.",
    created_at: '2026-06-28T11:02:00Z',
    recovered_at: null,
  },
  {
    id: 'f1a2b3c4-0003',
    nomba_transaction_id: 'NMB-TXN-88215',
    amount: 9200,
    currency: 'NGN',
    classification: 'USER_ABANDONED',
    recovery_score: 64,
    status: 'RECOVERY_TRIGGERED',
    recovery_message:
      "Hi! You started a ₦9,200 payment but didn't finish, still interested? Here's your checkout link.",
    created_at: '2026-06-27T16:45:00Z',
    recovered_at: null,
  },
  {
    id: 'f1a2b3c4-0004',
    nomba_transaction_id: 'NMB-TXN-88216',
    amount: 124000,
    currency: 'NGN',
    classification: 'CARD_DECLINED',
    recovery_score: 41,
    status: 'CLASSIFIED',
    recovery_message:
      'Hi! Your card declined the ₦124,000 payment. You can try a different card or bank transfer here.',
    created_at: '2026-06-27T08:30:00Z',
    recovered_at: null,
  },
  {
    id: 'f1a2b3c4-0005',
    nomba_transaction_id: 'NMB-TXN-88217',
    amount: 45000,
    currency: 'NGN',
    classification: 'NETWORK_TIMEOUT',
    recovery_score: 88,
    status: 'RECOVERED',
    recovery_message:
      "Hi! Your ₦45,000 payment didn't complete due to a network hiccup on our end, not yours. Please try again with this fresh link.",
    created_at: '2026-06-25T13:20:00Z',
    recovered_at: '2026-06-25T14:05:00Z',
  },
  {
    id: 'f1a2b3c4-0006',
    nomba_transaction_id: 'NMB-TXN-88218',
    amount: 7600,
    currency: 'NGN',
    classification: 'OTHER',
    recovery_score: 22,
    status: 'NEW',
    recovery_message: null,
    created_at: '2026-06-29T07:55:00Z',
    recovered_at: null,
  },
  {
    id: 'f1a2b3c4-0007',
    nomba_transaction_id: 'NMB-TXN-88219',
    amount: 33000,
    currency: 'NGN',
    classification: 'INSUFFICIENT_FUNDS',
    recovery_score: 71,
    status: 'EXPIRED',
    recovery_message:
      "Hi! Your ₦33,000 payment didn't go through, looks like a funds issue. Here's a fresh link to complete it whenever you're ready.",
    created_at: '2026-06-20T10:10:00Z',
    recovered_at: null,
  },
  {
    id: 'f1a2b3c4-0008',
    nomba_transaction_id: 'NMB-TXN-88220',
    amount: 250000,
    currency: 'NGN',
    classification: 'CARD_DECLINED',
    recovery_score: 35,
    status: 'CLASSIFIED',
    recovery_message:
      'Hi! Your card declined the ₦250,000 payment. You can try a different card or bank transfer here.',
    created_at: '2026-06-26T19:40:00Z',
    recovered_at: null,
  },
  {
    id: 'f1a2b3c4-0009',
    nomba_transaction_id: 'NMB-TXN-88221',
    amount: 12000,
    currency: 'NGN',
    classification: 'USER_ABANDONED',
    recovery_score: 58,
    status: 'NEW',
    recovery_message: null,
    created_at: '2026-06-29T15:18:00Z',
    recovered_at: null,
  },
  {
    id: 'f1a2b3c4-0010',
    nomba_transaction_id: 'NMB-TXN-88222',
    amount: 28000,
    currency: 'NGN',
    classification: 'NETWORK_TIMEOUT',
    recovery_score: 85,
    status: 'RECOVERED',
    recovery_message:
      "Hi! Your ₦28,000 payment didn't complete due to a network hiccup on our end, not yours. Please try again with this fresh link.",
    created_at: '2026-06-22T06:40:00Z',
    recovered_at: '2026-06-22T07:02:00Z',
  },
]

export function mockFailureDetail(id) {
  const item = mockFailures.find((f) => f.id === id)
  if (!item) return null
  return {
    ...item,
    recovery_checkout_url:
      item.status === 'RECOVERY_TRIGGERED' || item.status === 'RECOVERED'
        ? `https://checkout.nomba.com/pay/${item.nomba_transaction_id}`
        : null,
  }
}

// Recovery rate trend, last 7 days — feeds RecoveryRateChart.
export const mockTrend = [
  { date: 'Jun 23', recovery_rate: 18.0 },
  { date: 'Jun 24', recovery_rate: 22.5 },
  { date: 'Jun 25', recovery_rate: 27.0 },
  { date: 'Jun 26', recovery_rate: 25.0 },
  { date: 'Jun 27', recovery_rate: 30.0 },
  { date: 'Jun 28', recovery_rate: 31.5 },
  { date: 'Jun 29', recovery_rate: 34.2 },
]

export function formatNaira(amount, currency = 'NGN') {
  if (currency !== 'NGN') {
    return new Intl.NumberFormat('en-NG', { style: 'currency', currency }).format(amount)
  }
  return `₦${Number(amount).toLocaleString('en-NG')}`
}

export const CLASSIFICATION_LABELS = {
  INSUFFICIENT_FUNDS: 'Insufficient Funds',
  CARD_DECLINED: 'Card Declined',
  NETWORK_TIMEOUT: 'Network Timeout',
  USER_ABANDONED: 'Abandoned',
  OTHER: 'Other',
}

export function classificationLabel(value) {
  return CLASSIFICATION_LABELS[value] || value || '—'
}

export const STATUS_LABELS = {
  NEW: 'New',
  CLASSIFIED: 'Classified',
  RECOVERY_TRIGGERED: 'Recovery Sent',
  RECOVERED: 'Recovered',
  EXPIRED: 'Expired',
}

export function statusLabel(value) {
  return STATUS_LABELS[value] || value || '—'
}

export function formatDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleDateString('en-NG', { day: 'numeric', month: 'short', year: 'numeric' })
}

export function formatDateTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString('en-NG', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

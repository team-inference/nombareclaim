import { statusLabel } from '../lib/format'

const STYLES = {
  NEW: 'bg-status-new-bg text-status-new-fg',
  CLASSIFIED: 'bg-status-classified-bg text-status-classified-fg',
  RECOVERY_TRIGGERED: 'bg-status-triggered-bg text-status-triggered-fg',
  RECOVERED: 'bg-status-recovered-bg text-status-recovered-fg',
  EXPIRED: 'bg-status-expired-bg text-status-expired-fg',
}

export default function StatusBadge({ status }) {
  const style = STYLES[status] || 'bg-status-new-bg text-status-new-fg'
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium ${style}`}
    >
      {statusLabel(status)}
    </span>
  )
}

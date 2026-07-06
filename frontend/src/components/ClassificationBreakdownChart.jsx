import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell } from 'recharts'
import { classificationLabel, formatNaira } from '../lib/format'

function ChartTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  return (
    <div className="rounded-md border border-line bg-surface px-3 py-2 shadow-sm">
      <p className="text-xs font-medium text-ink">{classificationLabel(row.classification)}</p>
      <p className="mt-1 text-xs text-ink-muted">
        {row.count} failed · {row.recovered_count} recovered ({row.recovery_rate}%)
      </p>
      <p className="text-xs text-ink-faint">{formatNaira(row.total_amount)} at risk</p>
    </div>
  )
}

export default function ClassificationBreakdownChart({ data, loading }) {
  if (loading) {
    return <div className="h-[220px] animate-pulse rounded-lg border border-line bg-surface" />
  }

  const items = data?.items ?? []

  if (items.length === 0) {
    return (
      <div className="flex h-[220px] items-center justify-center rounded-lg border border-line bg-surface text-sm text-ink-faint">
        No classified failures yet.
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-line bg-surface p-5">
      <p className="mb-1 text-xs font-medium uppercase tracking-wide text-ink-faint">
        Recovery by Failure Reason
      </p>
      <p className="mb-3 text-xs text-ink-faint">
        Where the money is, and which reasons actually convert once a recovery link goes out.
      </p>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={items} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
          <CartesianGrid stroke="#E7E5DE" vertical={false} />
          <XAxis
            dataKey="classification"
            tickFormatter={classificationLabel}
            tick={{ fontSize: 10, fill: '#8B92A0' }}
            axisLine={{ stroke: '#E7E5DE' }}
            tickLine={false}
            interval={0}
            angle={-12}
            textAnchor="end"
            height={40}
          />
          <YAxis
            tick={{ fontSize: 11, fill: '#8B92A0' }}
            axisLine={false}
            tickLine={false}
            width={28}
            allowDecimals={false}
          />
          <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(15,110,92,0.06)' }} />
          <Bar dataKey="count" radius={[4, 4, 0, 0]}>
            {items.map((entry) => (
              <Cell
                key={entry.classification}
                fill={entry.recovery_rate > 0 ? '#0F6E5C' : '#C9C4B8'}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

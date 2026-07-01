import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-md border border-line bg-surface px-3 py-2 shadow-sm">
      <p className="text-xs text-ink-faint">{label}</p>
      <p className="font-ledger text-sm font-semibold text-action">
        {payload[0].value}%
      </p>
    </div>
  )
}

export default function RecoveryRateChart({ data, loading }) {
  if (loading) {
    return <div className="h-[180px] animate-pulse rounded-lg border border-line bg-surface" />
  }

  if (!data || data.length === 0) {
    return (
      <div className="flex h-[180px] items-center justify-center rounded-lg border border-line bg-surface text-sm text-ink-faint">
        Not enough data yet to chart a trend.
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-line bg-surface p-5">
      <p className="mb-3 text-xs font-medium uppercase tracking-wide text-ink-faint">
        Recovery Rate — Last 7 Days
      </p>
      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={data} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
          <CartesianGrid stroke="#E7E5DE" vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 11, fill: '#8B92A0' }}
            axisLine={{ stroke: '#E7E5DE' }}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 11, fill: '#8B92A0' }}
            axisLine={false}
            tickLine={false}
            width={36}
            tickFormatter={(v) => `${v}%`}
          />
          <Tooltip content={<ChartTooltip />} />
          <Line
            type="monotone"
            dataKey="recovery_rate"
            stroke="#0F6E5C"
            strokeWidth={2}
            dot={{ r: 3, fill: '#0F6E5C' }}
            activeDot={{ r: 5 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

/**
 * QuantLuna Dashboard — Number & Duration Formatters
 */

/**
 * Format a price value with up to 8 significant decimals.
 * e.g. 43210.5678 → "$43,210.57"
 */
export function formatPrice(value: number, decimals = 2): string {
  if (!isFinite(value)) return '—'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value)
}

/**
 * Format PnL with sign and color class hint.
 * Returns { text, positive } — UI decides color.
 * e.g. +1234.56 → { text: "+$1,234.56", positive: true }
 */
export function formatPnl(value: number, decimals = 2): { text: string; positive: boolean } {
  const positive = value >= 0
  const abs = Math.abs(value)
  const formatted = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(abs)
  return { text: `${positive ? '+' : '-'}${formatted}`, positive }
}

/**
 * Format a percentage value.
 * e.g. 0.0312 → "+3.12%"
 */
export function formatPercent(value: number, decimals = 2, showSign = true): string {
  if (!isFinite(value)) return '—'
  const sign = showSign && value > 0 ? '+' : ''
  return `${sign}${value.toFixed(decimals)}%`
}

/**
 * Format large volume values into K / M / B suffixes.
 * e.g. 1_234_567 → "1.23M"
 */
export function formatVolume(value: number): string {
  if (!isFinite(value)) return '—'
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return value.toFixed(0)
}

/**
 * Format a duration in seconds to human-readable.
 * e.g. 3725 → "1h 2m 5s"
 */
export function formatDuration(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return '—'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) return `${h}h ${m}m ${s}s`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

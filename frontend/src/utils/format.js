/** Display formatting. All dates and times are shown in UTC, the timezone snapshots are dated in. */

const EMPTY = '–'

const integerFormat = new Intl.NumberFormat('en-US')
const compactFormat = new Intl.NumberFormat('en-US', {
  notation: 'compact',
  maximumFractionDigits: 1,
})
const dateFormat = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
})
const shortDateFormat = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  timeZone: 'UTC',
})
const timeFormat = new Intl.DateTimeFormat('en-GB', {
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
  timeZone: 'UTC',
})
const relativeFormat = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })

/** 91,699 */
export function formatCount(value) {
  return value == null ? EMPTY : integerFormat.format(value)
}

/** For headline numbers: in full below 10,000 (1,284), compact from there (12.9K, 4.2M). */
export function formatCompact(value) {
  if (value == null) {
    return EMPTY
  }
  return Math.abs(value) < 10_000 ? integerFormat.format(value) : compactFormat.format(value)
}

/** Signed change: +12, −3 (a real minus sign), 0. `null` means there is nothing to compare with. */
export function formatDelta(value) {
  if (value == null) {
    return EMPTY
  }
  if (value === 0) {
    return '0'
  }
  return `${value > 0 ? '+' : '−'}${integerFormat.format(Math.abs(value))}`
}

function toDate(value) {
  if (!value) {
    return null
  }
  // A bare date ("2026-11-10") is a UTC calendar day.
  const date = new Date(value.length === 10 ? `${value}T00:00:00Z` : value)
  return Number.isNaN(date.getTime()) ? null : date
}

/** 10 Nov 2026 */
export function formatDate(value) {
  const date = toDate(value)
  return date ? dateFormat.format(date) : EMPTY
}

/** 10 Nov */
export function formatShortDate(value) {
  const date = toDate(value)
  return date ? shortDateFormat.format(date) : EMPTY
}

/** 10 Nov 2026, 06:00 UTC */
export function formatDateTime(value) {
  const date = toDate(value)
  return date ? `${dateFormat.format(date)}, ${timeFormat.format(date)} UTC` : EMPTY
}

const UNITS = [
  ['day', 86_400],
  ['hour', 3_600],
  ['minute', 60],
]

/** "5 minutes ago", "yesterday", "just now". */
export function formatRelative(value, now = Date.now()) {
  const date = toDate(value)
  if (!date) {
    return EMPTY
  }
  const seconds = Math.round((date.getTime() - now) / 1000)
  if (Math.abs(seconds) < 45) {
    return 'just now'
  }
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) {
      return relativeFormat.format(Math.round(seconds / size), unit)
    }
  }
  return relativeFormat.format(Math.round(seconds / 60), 'minute')
}

/** Small helpers behind the history charts: a day-based x scale and readable axis ticks. */

import { formatCount } from './format.js'

const MS_PER_DAY = 86_400_000

/**
 * Days since the Unix epoch for a UTC calendar date ("2026-11-10").
 * Plotting on day numbers keeps the x axis proportional to time, so a missed day shows as a
 * gap instead of being squeezed out.
 */
export function toDayNumber(isoDate) {
  return Math.round(Date.parse(`${isoDate}T00:00:00Z`) / MS_PER_DAY)
}

export function fromDayNumber(dayNumber) {
  return new Date(dayNumber * MS_PER_DAY).toISOString().slice(0, 10)
}

/**
 * One label style per axis. Compact labels (10K, 10.5K, 1.2M) are used only when the ticks are
 * far enough apart that neighbours cannot round to the same text; otherwise values are written
 * in full (91,690).
 */
export function formatAxisValue(value, tickValues) {
  if (value === 0) {
    return '0'
  }
  const largest = Math.max(...tickValues.map(Math.abs))
  const step = tickValues.length > 1 ? Math.abs(tickValues[1] - tickValues[0]) : 0

  let divisor
  let suffix
  if (largest >= 1_000_000 && step >= 100_000) {
    ;[divisor, suffix] = [1_000_000, 'M']
  } else if (largest >= 10_000 && largest < 1_000_000 && step >= 100) {
    ;[divisor, suffix] = [1_000, 'K']
  } else {
    return formatCount(value)
  }

  const scaled = value / divisor
  return `${Number.isInteger(scaled) ? scaled : scaled.toFixed(1)}${suffix}`
}

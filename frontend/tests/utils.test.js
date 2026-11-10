import { describe, expect, it } from 'vitest'

import { ApiError } from '../src/api/client.js'
import { formatAxisValue, fromDayNumber, toDayNumber } from '../src/utils/chart.js'
import { describeError } from '../src/utils/errors.js'
import {
  formatCompact,
  formatCount,
  formatDate,
  formatDateTime,
  formatDelta,
  formatRelative,
  formatShortDate,
} from '../src/utils/format.js'

function apiError(code, { status = 400, message = 'From the API.', details = {} } = {}) {
  return new ApiError({ status, code, message, details })
}

describe('number formatting', () => {
  it('writes counts in full with thousands separators', () => {
    expect(formatCount(0)).toBe('0')
    expect(formatCount(91699)).toBe('91,699')
    expect(formatCount(null)).toBe('–')
  })

  it('keeps headline numbers in full below 10,000 and compact above', () => {
    expect(formatCompact(1284)).toBe('1,284')
    expect(formatCompact(9999)).toBe('9,999')
    expect(formatCompact(12900)).toBe('12.9K')
    expect(formatCompact(4200000)).toBe('4.2M')
    expect(formatCompact(undefined)).toBe('–')
  })

  it('signs changes, and distinguishes no change from nothing to compare with', () => {
    expect(formatDelta(42)).toBe('+42')
    expect(formatDelta(-1500)).toBe('−1,500')
    expect(formatDelta(0)).toBe('0')
    expect(formatDelta(null)).toBe('–')
  })
})

describe('date formatting', () => {
  it('shows dates and times in UTC', () => {
    expect(formatDate('2026-11-10')).toBe('10 Nov 2026')
    expect(formatShortDate('2026-11-10')).toBe('10 Nov')
    expect(formatDateTime('2026-11-10T06:00:05Z')).toBe('10 Nov 2026, 06:00 UTC')
    // 01:30 at UTC+02:00 is still the previous day in UTC.
    expect(formatDateTime('2026-11-10T01:30:00+02:00')).toBe('9 Nov 2026, 23:30 UTC')
  })

  it('shows a dash for missing or unreadable values', () => {
    expect(formatDate(null)).toBe('–')
    expect(formatDateTime('')).toBe('–')
    expect(formatDateTime('not a date')).toBe('–')
  })

  it('describes how long ago something happened', () => {
    const now = Date.parse('2026-11-10T12:00:00Z')

    expect(formatRelative('2026-11-10T11:59:40Z', now)).toBe('just now')
    expect(formatRelative('2026-11-10T11:55:00Z', now)).toBe('5 minutes ago')
    expect(formatRelative('2026-11-10T06:00:00Z', now)).toBe('6 hours ago')
    expect(formatRelative('2026-11-09T11:00:00Z', now)).toBe('yesterday')
    expect(formatRelative('2026-11-03T12:00:00Z', now)).toBe('7 days ago')
    expect(formatRelative(null, now)).toBe('–')
  })
})

describe('chart helpers', () => {
  it('maps calendar days to consecutive numbers and back', () => {
    const day = toDayNumber('2026-11-10')

    expect(toDayNumber('2026-11-11') - day).toBe(1)
    expect(toDayNumber('2026-12-10') - day).toBe(30)
    expect(fromDayNumber(day)).toBe('2026-11-10')
  })

  it('writes small axis values in full', () => {
    expect(formatAxisValue(500, [0, 250, 500, 750])).toBe('500')
    expect(formatAxisValue(2000, [0, 1000, 2000])).toBe('2,000')
  })

  it('uses compact labels when the axis reaches 10,000', () => {
    const ticks = [0, 10000, 20000, 30000]

    expect(ticks.map((value) => formatAxisValue(value, ticks))).toEqual(['0', '10K', '20K', '30K'])
    expect(formatAxisValue(10500, [10000, 10500, 11000])).toBe('10.5K')
    expect(formatAxisValue(2500000, [0, 2500000, 5000000])).toBe('2.5M')
  })

  it('never lets neighbouring labels round to the same text', () => {
    const narrow = [91690, 91700, 91710]
    const million = [1200000, 1200500, 1201000]

    expect(narrow.map((value) => formatAxisValue(value, narrow))).toEqual([
      '91,690',
      '91,700',
      '91,710',
    ])
    expect(new Set(million.map((value) => formatAxisValue(value, million))).size).toBe(3)
  })
})

describe('error messages', () => {
  it('passes on messages the API wrote for people', () => {
    const duplicate = apiError('duplicate_repository', {
      status: 409,
      message: 'This repository is already being tracked.',
    })

    expect(describeError(duplicate)).toEqual({
      message: 'This repository is already being tracked.',
      temporary: false,
    })
  })

  it('explains a missing or blank address', () => {
    const { message, temporary } = describeError(apiError('validation_error'))

    expect(message).toContain('https://github.com/owner/repository')
    expect(temporary).toBe(false)
  })

  it('says when to retry after a GitHub rate limit', () => {
    const limited = (retryAfter) =>
      describeError(
        apiError('github_rate_limited', { status: 503, details: { retry_after: retryAfter } }),
      )

    expect(limited(1500).message).toContain('in about 25 minutes')
    expect(limited(45).message).toContain('in about 1 minute.')
    expect(limited(undefined).message).toContain('Try again later')
    expect(limited(1500).temporary).toBe(true)
  })

  it.each(['github_timeout', 'github_unavailable', 'throttled', 'network_error'])(
    'marks %s as temporary',
    (code) => {
      const { message, temporary } = describeError(apiError(code, { status: 503 }))

      expect(temporary).toBe(true)
      expect(message).toMatch(/try again/i)
    },
  )

  it('treats unknown server errors as temporary and unknown client errors as not', () => {
    expect(describeError(apiError('internal_error', { status: 500 })).temporary).toBe(true)
    expect(describeError(apiError('something_else', { status: 418 })).temporary).toBe(false)
  })

  it('copes with errors that did not come from the API client', () => {
    expect(describeError(new TypeError('boom'))).toEqual({
      message: 'Something went wrong. Try again.',
      temporary: true,
    })
  })
})

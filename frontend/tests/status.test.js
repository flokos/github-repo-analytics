import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import DailyReports from '../src/components/DailyReports.vue'
import DashboardSummary from '../src/components/DashboardSummary.vue'
import PipelineStatus from '../src/components/PipelineStatus.vue'
import { completedReport, pipeline, reportWith, summary } from './fixtures.js'

describe('DashboardSummary', () => {
  it('shows the headline numbers, compact with the exact figure on hover', () => {
    const wrapper = mount(DashboardSummary, { props: { summary } })
    const stars = wrapper.get('[data-test="tile-stars"] .tile__value')

    expect(wrapper.get('[data-test="tile-repositories"] .tile__value').text()).toBe('2')
    expect(stars.text()).toBe('91.7K')
    expect(stars.attributes('title')).toBe('91,699')
    expect(wrapper.get('[data-test="tile-forks"] .tile__value').text()).toBe('35.2K')
    expect(wrapper.get('[data-test="tile-open-issues"] .tile__value').text()).toBe('534')
  })

  it('notes that open issues include pull requests', () => {
    const wrapper = mount(DashboardSummary, { props: { summary } })

    expect(wrapper.get('[data-test="tile-open-issues"]').text()).toContain(
      'Includes open pull requests',
    )
  })

  it('says how many repositories the totals cover', () => {
    const partial = mount(DashboardSummary, { props: { summary } })
    const complete = mount(DashboardSummary, {
      props: { summary: { ...summary, repositories_with_data: 2 } },
    })

    expect(partial.text()).toContain('1 of 2 tracked repositories')
    expect(complete.text()).toContain('every tracked repository')
  })

  it('has a loading state', () => {
    expect(mount(DashboardSummary, { props: { summary: null } }).text()).toContain('Loading')
  })
})

describe('PipelineStatus', () => {
  function mountStatus(overrides = {}) {
    return mount(PipelineStatus, {
      props: { pipeline: { ...pipeline, ...overrides }, airflowUrl: 'http://localhost:8080' },
    })
  }

  it('shows the collection and the report as two separate things', () => {
    const wrapper = mountStatus()
    const collection = wrapper.get('[data-test="collection-status"]')
    const report = wrapper.get('[data-test="report-status"]')

    expect(collection.text()).toContain('Airflow DAG')
    expect(collection.text()).toContain('10 Nov 2026, 06:00 UTC')
    expect(collection.text()).toContain('1 of 2 tracked repositories have a snapshot for 10 Nov 2026')
    expect(collection.get('a').attributes('href')).toBe('http://localhost:8080')

    expect(report.text()).toContain('Django Celery task')
    expect(report.get('[data-status]').attributes('data-status')).toBe('completed')
    expect(report.text()).toContain('Completed')
    expect(report.text()).toContain('for 10 Nov 2026')
    expect(report.text()).toContain('independent of the Airflow run')
  })

  it.each([
    ['queued', 'Queued'],
    ['running', 'Running'],
    ['completed', 'Completed'],
    ['failed', 'Failed'],
  ])('names the %s report status in words, not only by colour', (status, label) => {
    const wrapper = mountStatus({ latest_report: reportWith({ status }) })
    const badge = wrapper.get('[data-test="report-status"] .badge')

    expect(badge.text()).toBe(label)
    expect(badge.find('svg').exists()).toBe(true)
  })

  it('shows why a report failed and how often it was attempted', () => {
    const wrapper = mountStatus({
      latest_report: reportWith({
        status: 'failed',
        attempts: 4,
        error_message: 'Gave up after 4 attempts: database is unavailable',
      }),
    })
    const report = wrapper.get('[data-test="report-status"]')

    expect(report.text()).toContain('Gave up after 4 attempts: database is unavailable')
    expect(report.text()).toContain('Attempts')
    expect(report.text()).toContain('4')
  })

  it('handles a system where nothing has run yet', () => {
    const wrapper = mountStatus({
      collection: {
        last_collected_at: null,
        latest_snapshot_date: null,
        repositories_collected: 0,
        active_repositories: 0,
      },
      latest_report: null,
    })

    expect(wrapper.get('[data-test="collection-status"]').text()).toContain(
      'Nothing has been collected yet',
    )
    expect(wrapper.get('[data-test="report-status"]').text()).toContain('No report yet')
  })

  it('has a loading state', () => {
    const wrapper = mount(PipelineStatus, { props: { pipeline: null } })

    expect(wrapper.text()).toContain('Loading pipeline status')
  })
})

describe('DailyReports', () => {
  it('lists totals with their signed changes', () => {
    const wrapper = mount(DailyReports, { props: { reports: [completedReport] } })
    const row = wrapper.get('[data-test="report-2026-11-10"]')

    expect(row.text()).toContain('10 Nov 2026')
    expect(row.text()).toContain('Completed')
    expect(row.text()).toContain('131,699')
    expect(row.text()).toContain('+42')
    expect(row.text()).toContain('−3')
    expect(row.text()).toContain('+5')
  })

  it('colours changes by whether they are good, and leaves open issues neutral', () => {
    const wrapper = mount(DailyReports, { props: { reports: [completedReport] } })
    const deltas = wrapper.get('[data-test="report-2026-11-10"]').findAll('.delta')

    expect(deltas[0].classes()).toContain('delta--up') // stars +42
    expect(deltas[1].classes()).toContain('delta--down') // forks −3
    expect(deltas[2].classes()).toEqual(['delta']) // open issues +5: neither good nor bad
  })

  it('shows a report that has not produced results yet', () => {
    const queued = reportWith({
      id: 8,
      snapshot_date: '2026-11-11',
      status: 'queued',
      finished_at: null,
      repository_count: 0,
      repository_changes: [],
    })
    const row = mount(DailyReports, { props: { reports: [queued] } }).get(
      '[data-test="report-2026-11-11"]',
    )

    expect(row.text()).toContain('Queued')
    expect(row.text()).toContain('Not generated yet')
  })

  it('shows the reason next to a failed report', () => {
    const failed = reportWith({
      status: 'failed',
      repository_count: 0,
      repository_changes: [],
      error_message: 'ValueError: unexpected data',
    })
    const wrapper = mount(DailyReports, { props: { reports: [failed] } })

    expect(wrapper.text()).toContain('Failed')
    expect(wrapper.text()).toContain('ValueError: unexpected data')
  })

  it('breaks the newest report down by repository', () => {
    const wrapper = mount(DailyReports, { props: { reports: [completedReport] } })
    const details = wrapper.get('[data-test="report-details"]')

    expect(details.get('summary').text()).toBe('Changes per repository on 10 Nov 2026')
    expect(details.text()).toContain('django/django')
    expect(details.text()).toContain('9 Nov 2026')
    // A repository collected for the first time has nothing to be compared with.
    expect(details.text()).toContain('apache/airflow')
    expect(details.text()).toContain('First snapshot')
  })

  it('has an empty state', () => {
    const wrapper = mount(DailyReports, { props: { reports: [] } })

    expect(wrapper.get('[data-test="reports-empty"]').text()).toContain('No reports yet')
  })
})

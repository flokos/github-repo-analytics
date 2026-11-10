import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError } from '../src/api/client.js'
import MetricsChart from '../src/components/MetricsChart.vue'
import RepositoryHistory from '../src/components/RepositoryHistory.vue'
import { toDayNumber } from '../src/utils/chart.js'
import { airflow, django, snapshots } from './fixtures.js'

vi.mock('vue-chartjs', () => import('./chartStub.js'))
vi.mock('../src/api/client.js', async (importOriginal) => ({
  ...(await importOriginal()),
  api: { getSnapshots: vi.fn() },
}))

async function mountHistory(repository = django) {
  const wrapper = mount(RepositoryHistory, { props: { repository } })
  await flushPromises()
  return wrapper
}

describe('MetricsChart', () => {
  const points = snapshots.map((snapshot) => ({ date: snapshot.snapshot_date, value: snapshot.stars }))

  function mountChart(props = {}) {
    const wrapper = mount(MetricsChart, {
      props: { title: 'Stars', subject: 'django/django', points, ...props },
    })
    return { wrapper, line: wrapper.getComponent({ name: 'Line' }) }
  }

  it('plots one series, positioned by calendar day', () => {
    const { line } = mountChart()
    const { datasets } = line.props('data')

    expect(datasets).toHaveLength(1)
    expect(datasets[0].data).toEqual([
      { x: toDayNumber('2026-11-08'), y: 91600 },
      { x: toDayNumber('2026-11-09'), y: 91657 },
      { x: toDayNumber('2026-11-10'), y: 91699 },
    ])
  })

  it('uses a single y axis and no legend', () => {
    const { line } = mountChart()
    const options = line.props('options')

    expect(Object.keys(options.scales)).toEqual(['x', 'y'])
    expect(options.plugins.legend.display).toBe(false)
  })

  it('labels the end of the line with the latest value', () => {
    const { line } = mountChart()

    expect(line.props('options').plugins.endLabel.text).toBe('91,699')
  })

  it('reads values from the nearest date instead of requiring a hit on the line', () => {
    const { line } = mountChart()
    const { interaction, plugins } = line.props('options')

    expect(interaction).toMatchObject({ mode: 'index', intersect: false })
    expect(plugins.tooltip.callbacks.title([{ parsed: { x: toDayNumber('2026-11-09') } }])).toBe(
      '9 Nov 2026',
    )
    expect(plugins.tooltip.callbacks.label({ parsed: { y: 91657 } })).toBe('Stars: 91,657')
  })

  it('marks every point while they have room, and only the latest when they do not', () => {
    const dataset = mountChart().line.props('data').datasets[0]
    const radii = (count, plotWidth) =>
      Array.from({ length: count }, (_, dataIndex) =>
        dataset.pointRadius({
          dataIndex,
          dataset: { data: Array.from({ length: count }) },
          chart: { chartArea: { width: plotWidth } },
        }),
      )

    expect(radii(3, 600)).toEqual([5, 5, 5])
    expect(radii(1, 600)).toEqual([5])
    // 60 points across 300px are 5px apart: too dense for a marker each.
    const dense = radii(60, 300)
    expect(dense.filter((radius) => radius > 0)).toHaveLength(1)
    expect(dense.at(-1)).toBe(5)
    // The same 60 points have room on a wide plot.
    expect(radii(60, 900).every((radius) => radius === 5)).toBe(true)
  })

  it('centres a single snapshot instead of stretching it across the axis', () => {
    const { line } = mountChart({ points: [points[2]] })
    const day = toDayNumber('2026-11-10')

    expect(line.props('options').scales.x).toMatchObject({ min: day - 1, max: day + 1 })
  })

  it('describes the chart in words for people who cannot see it', () => {
    const { line } = mountChart()
    const single = mountChart({ points: [points[2]] }).line

    expect(line.props('ariaLabel')).toBe(
      'Stars of django/django from 8 Nov 2026 to 10 Nov 2026: 91,600 at the start, ' +
        '91,699 at the end, between 91,600 and 91,699.',
    )
    expect(single.props('ariaLabel')).toBe('Stars of django/django: 91,699 on 10 Nov 2026.')
  })
})

describe('RepositoryHistory', () => {
  beforeEach(() => {
    api.getSnapshots.mockReset()
    api.getSnapshots.mockResolvedValue(snapshots)
  })

  it('loads the last 30 days and draws one chart per metric', async () => {
    const wrapper = await mountHistory()
    const charts = wrapper.findAllComponents(MetricsChart)

    expect(api.getSnapshots).toHaveBeenCalledExactlyOnceWith(1, 30)
    expect(wrapper.get('h2').text()).toBe('History of django/django')
    expect(charts.map((chart) => chart.props('title'))).toEqual(['Stars', 'Forks', 'Open issues'])
    expect(charts[1].props('points')).toEqual([
      { date: '2026-11-08', value: 35200 },
      { date: '2026-11-09', value: 35216 },
      { date: '2026-11-10', value: 35213 },
    ])
    expect(charts[2].props('note')).toBe('Includes open pull requests')
  })

  it('reloads everything it shows when the range changes', async () => {
    const wrapper = await mountHistory()

    await wrapper.get('input[value="90"]').setValue(true)
    await flushPromises()

    expect(api.getSnapshots).toHaveBeenLastCalledWith(1, 90)
  })

  it('reloads for another repository', async () => {
    const wrapper = await mountHistory()

    await wrapper.setProps({ repository: { ...airflow, last_synced_at: '2026-11-10T06:00:07Z' } })
    await flushPromises()

    expect(api.getSnapshots).toHaveBeenLastCalledWith(2, 30)
    expect(wrapper.get('h2').text()).toBe('History of apache/airflow')
  })

  it('picks up a new collection but ignores refreshes that change nothing', async () => {
    const wrapper = await mountHistory()

    // The dashboard polls and hands over a new but identical object: nothing to reload.
    await wrapper.setProps({ repository: { ...django } })
    await flushPromises()
    expect(api.getSnapshots).toHaveBeenCalledTimes(1)

    await wrapper.setProps({ repository: { ...django, last_synced_at: '2026-11-11T06:00:02Z' } })
    await flushPromises()
    expect(api.getSnapshots).toHaveBeenCalledTimes(2)
  })

  it('offers the same data as a table, newest first', async () => {
    const wrapper = await mountHistory()
    const toggle = wrapper.get('[aria-controls="history-table"]')

    expect(wrapper.find('table').exists()).toBe(false)
    expect(toggle.attributes('aria-expanded')).toBe('false')

    await toggle.trigger('click')

    const rows = wrapper.findAll('tbody tr')
    expect(toggle.attributes('aria-expanded')).toBe('true')
    expect(rows).toHaveLength(3)
    expect(rows[0].text()).toContain('10 Nov 2026')
    expect(rows[0].text()).toContain('91,699')
    expect(rows[0].text()).toContain('35,213')
    expect(rows[0].text()).toContain('534')
    expect(rows[2].text()).toContain('8 Nov 2026')
  })

  it('explains a single data point', async () => {
    api.getSnapshots.mockResolvedValue([snapshots[2]])

    const wrapper = await mountHistory()

    expect(wrapper.get('[data-test="history-hint"]').text()).toContain('One snapshot is stored per day')
  })

  it('explains that a new repository has not been collected yet', async () => {
    api.getSnapshots.mockResolvedValue([])

    const wrapper = await mountHistory(airflow)

    expect(wrapper.get('[data-test="history-empty"]').text()).toContain(
      'No metrics have been collected for this repository yet',
    )
    expect(wrapper.findComponent(MetricsChart).exists()).toBe(false)
  })

  it('suggests a longer range when the chosen one is empty', async () => {
    api.getSnapshots.mockResolvedValue([])

    const wrapper = await mountHistory(django)

    expect(wrapper.get('[data-test="history-empty"]').text()).toContain(
      'No snapshots in the last 30 days',
    )
  })

  it('shows a loading state first', () => {
    api.getSnapshots.mockReturnValue(new Promise(() => {}))

    const wrapper = mount(RepositoryHistory, { props: { repository: django } })

    expect(wrapper.find('[data-test="history-loading"]').exists()).toBe(true)
  })

  it('reports a failed load and retries on request', async () => {
    api.getSnapshots.mockRejectedValueOnce(
      new ApiError({ status: 0, code: 'network_error', message: 'unreachable' }),
    )
    const wrapper = await mountHistory()

    expect(wrapper.get('[role="alert"]').text()).toContain('The server cannot be reached')

    await wrapper.get('[role="alert"] button').trigger('click')
    await flushPromises()

    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.findAllComponents(MetricsChart)).toHaveLength(3)
  })
})

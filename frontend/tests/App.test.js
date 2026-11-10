import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError } from '../src/api/client.js'
import App from '../src/App.vue'
import { airflow, django, pipeline, removed, completedReport, snapshots, summary } from './fixtures.js'

vi.mock('vue-chartjs', () => import('./chartStub.js'))
vi.mock('../src/api/client.js', async (importOriginal) => ({
  ...(await importOriginal()),
  api: {
    listRepositories: vi.fn(),
    addRepository: vi.fn(),
    removeRepository: vi.fn(),
    getSnapshots: vi.fn(),
    getSummary: vi.fn(),
    listReports: vi.fn(),
    getPipelineStatus: vi.fn(),
  },
}))

/** A tiny in-memory backend behind the mocked API client. */
let stored

function serveFrom(repositories) {
  stored = repositories
  api.listRepositories.mockImplementation(async () => stored)
  api.getSummary.mockResolvedValue(summary)
  api.getPipelineStatus.mockResolvedValue(pipeline)
  api.listReports.mockResolvedValue([completedReport])
  api.getSnapshots.mockResolvedValue(snapshots)
}

let wrapper

async function mountApp() {
  wrapper = mount(App)
  await flushPromises()
  return wrapper
}

function trackedNames() {
  return wrapper.findAll('.repository__name').map((link) => link.text())
}

beforeEach(() => {
  vi.resetAllMocks()
  serveFrom([django, airflow, removed])
})

afterEach(() => {
  wrapper?.unmount() // also stops the refresh timer
  vi.useRealTimers()
})

describe('dashboard', () => {
  it('loads and shows repositories, totals, pipeline status and reports', async () => {
    await mountApp()

    expect(api.listRepositories).toHaveBeenCalledWith('all')
    expect(trackedNames()).toEqual(['django/django', 'apache/airflow'])
    expect(wrapper.get('[data-test="tile-stars"]').text()).toContain('91.7K')
    expect(wrapper.get('[data-test="collection-status"]').text()).toContain('10 Nov 2026, 06:00 UTC')
    expect(wrapper.get('[data-test="report-status"]').text()).toContain('Completed')
    expect(wrapper.get('[data-test="report-2026-11-10"]').text()).toContain('131,699')
    expect(wrapper.get('[data-test="removed-repositories"]').text()).toContain('vuejs/vue')
  })

  it('shows the history of the first repository by default', async () => {
    await mountApp()

    expect(api.getSnapshots).toHaveBeenCalledWith(django.id, 30)
    expect(wrapper.text()).toContain('History of django/django')
  })

  it('guides the user when nothing is tracked yet', async () => {
    serveFrom([])

    await mountApp()

    expect(wrapper.get('[data-test="repositories-empty"]').text()).toContain('No repositories')
    expect(wrapper.text()).toContain('Add a repository to see how its stars')
    expect(api.getSnapshots).not.toHaveBeenCalled()
  })
})

describe('adding a repository', () => {
  const created = {
    ...airflow,
    id: 9,
    owner: 'vuejs',
    name: 'core',
    full_name: 'vuejs/core',
    html_url: 'https://github.com/vuejs/core',
  }

  async function add(url) {
    await wrapper.get('#repository-url').setValue(url)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
  }

  it('adds it to the list, confirms, and selects it', async () => {
    api.addRepository.mockImplementation(async () => {
      stored = [...stored, created]
      return { ...created, reactivated: false }
    })
    await mountApp()

    await add('https://github.com/vuejs/core')

    expect(api.addRepository).toHaveBeenCalledExactlyOnceWith('https://github.com/vuejs/core')
    expect(trackedNames()).toContain('vuejs/core')
    expect(wrapper.get('[data-test="form-feedback"]').text()).toContain('vuejs/core is now tracked')
    expect(wrapper.get('[data-test="repository-9"]').classes()).toContain('repository--selected')
  })

  it.each([
    ['duplicate_repository', 409, 'This repository is already being tracked.'],
    ['invalid_repository_url', 400, 'Only github.com repository addresses are supported.'],
    [
      'repository_unavailable',
      422,
      'GitHub reports this repository as unavailable. It may not exist, or it may be private.',
    ],
  ])('shows the API feedback for %s and leaves the list alone', async (code, status, message) => {
    api.addRepository.mockRejectedValue(new ApiError({ status, code, message }))
    await mountApp()

    await add('https://github.com/whatever/it-is')

    expect(wrapper.get('[data-test="form-feedback"]').text()).toBe(message)
    expect(trackedNames()).toEqual(['django/django', 'apache/airflow'])
  })

  it('explains a GitHub rate limit as a temporary problem', async () => {
    api.addRepository.mockRejectedValue(
      new ApiError({
        status: 503,
        code: 'github_rate_limited',
        message: 'rate limited',
        details: { retry_after: 600 },
      }),
    )
    await mountApp()

    await add('https://github.com/vuejs/core')

    const feedback = wrapper.get('[data-test="form-feedback"]')
    expect(feedback.text()).toContain('Try again in about 10 minutes')
    expect(feedback.classes()).toContain('notice--warning')
  })
})

describe('removing and restoring', () => {
  it('moves a removed repository to the removed list and says its history is kept', async () => {
    api.removeRepository.mockImplementation(async (id) => {
      stored = stored.map((repository) =>
        repository.id === id
          ? { ...repository, is_active: false, deactivated_at: '2026-11-10T12:00:00Z' }
          : repository,
      )
      return null
    })
    await mountApp()

    await wrapper.get('[aria-label="Remove django/django from tracking"]').trigger('click')
    await flushPromises()

    expect(api.removeRepository).toHaveBeenCalledExactlyOnceWith(django.id)
    expect(trackedNames()).toEqual(['apache/airflow'])
    expect(wrapper.get('[data-test="removed-repositories"]').text()).toContain('django/django')
    expect(wrapper.get('[data-test="notice"]').text()).toContain('django/django was removed')
    expect(wrapper.get('[data-test="notice"]').text()).toContain('history is kept')
    // The history panel moves on to a repository that is still tracked.
    expect(wrapper.text()).toContain('History of apache/airflow')
  })

  it('reports a failed removal and keeps the repository', async () => {
    api.removeRepository.mockRejectedValue(
      new ApiError({ status: 0, code: 'network_error', message: 'unreachable' }),
    )
    await mountApp()

    await wrapper.get('[aria-label="Remove django/django from tracking"]').trigger('click')
    await flushPromises()

    const notice = wrapper.get('[data-test="notice"]')
    expect(notice.text()).toContain('django/django could not be removed')
    expect(notice.text()).toContain('The server cannot be reached')
    expect(trackedNames()).toEqual(['django/django', 'apache/airflow'])
  })

  it('restores a removed repository through the validated add path', async () => {
    api.addRepository.mockImplementation(async () => {
      stored = stored.map((repository) =>
        repository.id === removed.id
          ? { ...repository, is_active: true, deactivated_at: null }
          : repository,
      )
      return { ...removed, is_active: true, deactivated_at: null, reactivated: true }
    })
    await mountApp()

    await wrapper.get('[aria-label="Restore vuejs/vue"]').trigger('click')
    await flushPromises()

    expect(api.addRepository).toHaveBeenCalledExactlyOnceWith('https://github.com/vuejs/vue')
    expect(trackedNames()).toContain('vuejs/vue')
    expect(wrapper.find('[data-test="removed-repositories"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="notice"]').text()).toContain('vuejs/vue is tracked again')
  })

  it('explains why a repository could not be restored', async () => {
    api.addRepository.mockRejectedValue(
      new ApiError({
        status: 422,
        code: 'repository_unavailable',
        message: 'GitHub reports this repository as unavailable. It may not exist, or it may be private.',
      }),
    )
    await mountApp()

    await wrapper.get('[aria-label="Restore vuejs/vue"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-test="notice"]').text()).toContain('vuejs/vue could not be restored')
    expect(wrapper.get('[data-test="notice"]').text()).toContain('may be private')
  })
})

describe('when the API is unavailable', () => {
  it('says so and recovers on retry', async () => {
    api.listRepositories.mockRejectedValueOnce(
      new ApiError({ status: 0, code: 'network_error', message: 'unreachable' }),
    )
    await mountApp()

    const banner = wrapper.get('[data-test="load-error"]')
    expect(banner.text()).toContain('The server cannot be reached')

    await banner.get('button').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="load-error"]').exists()).toBe(false)
    expect(trackedNames()).toEqual(['django/django', 'apache/airflow'])
  })

  it('keeps showing the last data when a background refresh fails', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    await mountApp()
    api.getSummary.mockRejectedValue(
      new ApiError({ status: 502, code: 'unexpected_response', message: 'Bad gateway' }),
    )

    await vi.advanceTimersByTimeAsync(15_000)
    await flushPromises()

    expect(wrapper.get('[data-test="load-error"]').text()).toContain('last data that loaded')
    expect(trackedNames()).toEqual(['django/django', 'apache/airflow'])
    expect(wrapper.get('[data-test="tile-stars"]').text()).toContain('91.7K')
  })
})

describe('staying up to date', () => {
  it('refreshes periodically so a report can be watched moving through its states', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    api.getPipelineStatus.mockResolvedValue({
      ...pipeline,
      latest_report: { ...completedReport, status: 'queued' },
    })
    await mountApp()
    expect(wrapper.get('[data-test="report-status"]').text()).toContain('Queued')

    api.getPipelineStatus.mockResolvedValue(pipeline)
    await vi.advanceTimersByTimeAsync(15_000)
    await flushPromises()

    expect(wrapper.get('[data-test="report-status"]').text()).toContain('Completed')
  })
})

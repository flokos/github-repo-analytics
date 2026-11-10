import { ref } from 'vue'

import { api } from '../api/client.js'

const REPORT_LIMIT = 14

/**
 * All server state the dashboard shows, plus the actions that change it.
 * Plain refs are enough here: one page, one owner of the data, no cross-route sharing.
 */
export function useDashboard() {
  const repositories = ref([])
  const removedRepositories = ref([])
  const summary = ref(null)
  const pipeline = ref(null)
  const reports = ref([])
  const loading = ref(true) // true only until the first load settles
  const loadError = ref(null)

  /**
   * Reloads everything. On failure the last data that loaded stays on screen and `loadError`
   * is set, so a background refresh never blanks the page.
   */
  async function refresh() {
    try {
      const [all, summaryData, pipelineData, reportData] = await Promise.all([
        api.listRepositories('all'),
        api.getSummary(),
        api.getPipelineStatus(),
        api.listReports(REPORT_LIMIT),
      ])
      repositories.value = all.filter((repository) => repository.is_active)
      removedRepositories.value = all.filter((repository) => !repository.is_active)
      summary.value = summaryData
      pipeline.value = pipelineData
      reports.value = reportData
      loadError.value = null
    } catch (error) {
      loadError.value = error
    } finally {
      loading.value = false
    }
  }

  /** Adds a repository, or brings a removed one back. Rejects with an `ApiError`. */
  async function addRepository(url) {
    const repository = await api.addRepository(url)
    await refresh()
    return repository
  }

  async function removeRepository(id) {
    await api.removeRepository(id)
    await refresh()
  }

  return {
    repositories,
    removedRepositories,
    summary,
    pipeline,
    reports,
    loading,
    loadError,
    refresh,
    addRepository,
    removeRepository,
  }
}

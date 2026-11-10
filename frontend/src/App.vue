<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import DailyReports from './components/DailyReports.vue'
import DashboardSummary from './components/DashboardSummary.vue'
import PipelineStatus from './components/PipelineStatus.vue'
import RepositoryForm from './components/RepositoryForm.vue'
import RepositoryHistory from './components/RepositoryHistory.vue'
import RepositoryList from './components/RepositoryList.vue'
import { useDashboard } from './composables/useDashboard.js'
import { usePolling } from './composables/usePolling.js'
import { describeError } from './utils/errors.js'

const AIRFLOW_URL = import.meta.env.VITE_AIRFLOW_URL ?? 'http://localhost:8080'
// Frequent enough to watch a report go from queued to completed during a demonstration.
const REFRESH_INTERVAL_MS = 15_000

const {
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
} = useDashboard()

const selectedId = ref(null)
const busyIds = ref([])
/** @type {import('vue').Ref<{ kind: 'success' | 'error' | 'warning', text: string } | null>} */
const notice = ref(null)

const selectedRepository = computed(
  () => repositories.value.find((repository) => repository.id === selectedId.value) ?? null,
)
const loadErrorMessage = computed(() =>
  loadError.value ? describeError(loadError.value).message : '',
)
const hasLoadedOnce = computed(() => summary.value !== null)

// Always show the history of a repository that is still tracked: the first one by default.
watch(
  repositories,
  (list) => {
    if (!list.some((repository) => repository.id === selectedId.value)) {
      selectedId.value = list[0]?.id ?? null
    }
  },
  { immediate: true },
)

onMounted(refresh)
usePolling(refresh, REFRESH_INTERVAL_MS)

async function submitRepository(url) {
  const repository = await addRepository(url)
  notice.value = null
  selectedId.value = repository.id
  return repository
}

async function whileBusy(id, action) {
  busyIds.value = [...busyIds.value, id]
  try {
    await action()
  } finally {
    busyIds.value = busyIds.value.filter((busyId) => busyId !== id)
  }
}

function failure(prefix, error) {
  const { message, temporary } = describeError(error)
  return { kind: temporary ? 'warning' : 'error', text: `${prefix} ${message}` }
}

function onRemove(repository) {
  return whileBusy(repository.id, async () => {
    try {
      await removeRepository(repository.id)
      notice.value = {
        kind: 'success',
        text: `${repository.full_name} was removed from tracking. Its history is kept and it can be restored from the list of removed repositories.`,
      }
    } catch (error) {
      notice.value = failure(`${repository.full_name} could not be removed.`, error)
    }
  })
}

function onRestore(repository) {
  return whileBusy(repository.id, async () => {
    try {
      // Restoring goes through the same validated path as adding: the backend checks with
      // GitHub that the repository is still public before tracking it again.
      const restored = await addRepository(repository.html_url)
      selectedId.value = restored.id
      notice.value = {
        kind: 'success',
        text: `${restored.full_name} is tracked again. Its earlier history was kept.`,
      }
    } catch (error) {
      notice.value = failure(`${repository.full_name} could not be restored.`, error)
    }
  })
}
</script>

<template>
  <div class="page">
    <header class="masthead">
      <div>
        <h1>GitHub Repository Analytics</h1>
        <p class="muted">Daily stars, forks and open issues of the repositories you track.</p>
      </div>
      <a class="button button--secondary" :href="AIRFLOW_URL" target="_blank" rel="noopener noreferrer">
        Open Airflow
      </a>
    </header>

    <div v-if="loadErrorMessage" class="notice notice--warning banner" role="alert" data-test="load-error">
      <span>
        {{ loadErrorMessage }}
        <template v-if="hasLoadedOnce">The page shows the last data that loaded.</template>
      </span>
      <button class="button button--secondary" type="button" @click="refresh">Try again</button>
    </div>

    <main>
      <PipelineStatus :pipeline="pipeline" :airflow-url="AIRFLOW_URL" />
      <DashboardSummary :summary="summary" />

      <div class="workspace">
        <div class="workspace__side">
          <RepositoryForm :submit="submitRepository" />

          <div v-if="notice" class="notice banner" :class="`notice--${notice.kind}`" role="status" data-test="notice">
            <span>{{ notice.text }}</span>
            <button class="button button--quiet" type="button" @click="notice = null">Dismiss</button>
          </div>

          <RepositoryList
            :repositories="repositories"
            :removed="removedRepositories"
            :loading="loading"
            :selected-id="selectedId"
            :busy-ids="busyIds"
            @select="selectedId = $event.id"
            @remove="onRemove"
            @restore="onRestore"
          />
        </div>

        <div class="workspace__main">
          <RepositoryHistory v-if="selectedRepository" :repository="selectedRepository" />
          <section v-else aria-labelledby="history-heading">
            <h2 id="history-heading" class="section-title">History</h2>
            <p class="card empty">
              Add a repository to see how its stars, forks and open issues change from day to day.
            </p>
          </section>
        </div>
      </div>

      <DailyReports :reports="reports" />
    </main>

    <footer class="page__footer muted small">
      All dates and times are in UTC. GitHub counts open pull requests as open issues.
    </footer>
  </div>
</template>

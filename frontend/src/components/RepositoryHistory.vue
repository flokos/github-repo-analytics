<script setup>
import { computed, ref, watch } from 'vue'

import { api } from '../api/client.js'
import { describeError } from '../utils/errors.js'
import { formatCount, formatDate, formatDateTime } from '../utils/format.js'
import MetricsChart from './MetricsChart.vue'

const props = defineProps({
  /** One item of GET /api/repositories/. */
  repository: { type: Object, required: true },
})

const RANGES = [
  { days: 30, label: '30 days' },
  { days: 90, label: '90 days' },
  { days: 365, label: '1 year' },
]
const METRICS = [
  { key: 'stars', title: 'Stars' },
  { key: 'forks', title: 'Forks' },
  { key: 'open_issues', title: 'Open issues', note: 'Includes open pull requests' },
]

const days = ref(RANGES[0].days)
const snapshots = ref([])
const loading = ref(true) // nothing to show yet for this repository
const refreshing = ref(false) // newer data is loading behind what is on screen
const errorMessage = ref('')
const showTable = ref(false)

// Guards against answers arriving out of order when the repository or range changes quickly.
let latestRequest = 0

async function load({ keepFrame }) {
  const request = ++latestRequest
  if (keepFrame) {
    refreshing.value = true
  } else {
    loading.value = true
    snapshots.value = []
  }
  try {
    const data = await api.getSnapshots(props.repository.id, days.value)
    if (request !== latestRequest) {
      return
    }
    snapshots.value = data
    errorMessage.value = ''
  } catch (error) {
    if (request !== latestRequest) {
      return
    }
    errorMessage.value = describeError(error).message
  } finally {
    if (request === latestRequest) {
      loading.value = false
      refreshing.value = false
    }
  }
}

// Reload for another repository, and also when the selected one has just been collected again
// (its last sync time changes). The second case keeps the charts on screen while it loads.
// Two separate sources, so that a refreshed-but-identical repository object triggers nothing.
watch(
  [() => props.repository.id, () => props.repository.last_synced_at],
  ([id], [previousId]) => load({ keepFrame: previousId === id }),
  { immediate: true },
)
watch(days, () => load({ keepFrame: true }))

const charts = computed(() =>
  METRICS.map((metric) => ({
    ...metric,
    points: snapshots.value.map((snapshot) => ({
      date: snapshot.snapshot_date,
      value: snapshot[metric.key],
    })),
  })),
)
const newestFirst = computed(() => [...snapshots.value].reverse())
const rangeLabel = computed(() => RANGES.find((range) => range.days === days.value).label)
</script>

<template>
  <section class="history" aria-labelledby="history-heading">
    <!-- One control row above everything it scopes: the charts and the table below. -->
    <header class="history__header">
      <h2 id="history-heading" class="section-title">History of {{ repository.full_name }}</h2>
      <fieldset class="segmented">
        <legend class="visually-hidden">Time range</legend>
        <label v-for="range in RANGES" :key="range.days">
          <input v-model="days" type="radio" name="history-range" :value="range.days">
          <span>{{ range.label }}</span>
        </label>
      </fieldset>
    </header>

    <p v-if="loading" class="muted" data-test="history-loading">Loading history…</p>

    <div v-else-if="errorMessage" class="notice notice--warning banner" role="alert">
      <span>{{ errorMessage }}</span>
      <button class="button button--secondary" type="button" @click="load({ keepFrame: true })">
        Try again
      </button>
    </div>

    <p v-else-if="snapshots.length === 0" class="card empty" data-test="history-empty">
      <template v-if="repository.last_synced_at">
        No snapshots in the last {{ rangeLabel }}. Choose a longer range to see older ones.
      </template>
      <template v-else>
        No metrics have been collected for this repository yet. They arrive with the next
        pipeline run: every day, or right away when a run is started in Airflow.
      </template>
    </p>

    <template v-else>
      <div class="charts" :class="{ 'is-refreshing': refreshing }">
        <MetricsChart
          v-for="chart in charts"
          :key="chart.key"
          :title="chart.title"
          :note="chart.note"
          :points="chart.points"
          :subject="repository.full_name"
        />
      </div>

      <p v-if="snapshots.length === 1" class="muted small" data-test="history-hint">
        One snapshot is stored per day, so each line grows by one point with every daily
        collection.
      </p>

      <!-- Every value in the charts is also available as text. -->
      <button
        class="button button--secondary"
        type="button"
        :aria-expanded="showTable"
        aria-controls="history-table"
        @click="showTable = !showTable"
      >
        {{ showTable ? 'Hide data table' : 'Show data table' }}
      </button>

      <div v-if="showTable" id="history-table" class="table-wrap card">
        <table>
          <caption class="visually-hidden">
            Daily snapshots of {{ repository.full_name }}, newest first
          </caption>
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col" class="num">Stars</th>
              <th scope="col" class="num">Forks</th>
              <th scope="col" class="num">Open issues</th>
              <th scope="col">Collected</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="snapshot in newestFirst" :key="snapshot.snapshot_date">
              <th scope="row">{{ formatDate(snapshot.snapshot_date) }}</th>
              <td class="num">{{ formatCount(snapshot.stars) }}</td>
              <td class="num">{{ formatCount(snapshot.forks) }}</td>
              <td class="num">{{ formatCount(snapshot.open_issues) }}</td>
              <td>{{ formatDateTime(snapshot.collected_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>

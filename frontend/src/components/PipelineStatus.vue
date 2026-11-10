<script setup>
import { computed } from 'vue'

import { formatDate, formatDateTime, formatRelative } from '../utils/format.js'
import StatusBadge from './StatusBadge.vue'

/**
 * Two separate things, shown side by side on purpose:
 *  - the collection, done by the Airflow DAG, read here from the snapshots it stored;
 *  - the daily report, generated afterwards by a Django Celery task with a status of its own.
 * A successful Airflow run only means the report was requested, not that it finished.
 */
const props = defineProps({
  /** Response of GET /api/pipeline/status/, or null while it loads. */
  pipeline: { type: Object, default: null },
  airflowUrl: { type: String, default: '' },
})

const collection = computed(() => props.pipeline?.collection ?? null)
const report = computed(() => props.pipeline?.latest_report ?? null)

const coverage = computed(() => {
  const data = collection.value
  if (!data?.latest_snapshot_date) {
    return ''
  }
  return `${data.repositories_collected} of ${data.active_repositories} tracked repositories have a snapshot for ${formatDate(data.latest_snapshot_date)}.`
})
</script>

<template>
  <section aria-labelledby="pipeline-heading">
    <h2 id="pipeline-heading" class="section-title">Pipeline</h2>
    <p v-if="!pipeline" class="muted">Loading pipeline status…</p>

    <div v-else class="pipeline">
      <article class="card" data-test="collection-status">
        <header class="card__header">
          <h3 class="card__title">Latest collection</h3>
          <span class="tag">Airflow DAG</span>
        </header>
        <template v-if="collection.last_collected_at">
          <p class="card__lead" :title="formatDateTime(collection.last_collected_at)">
            {{ formatRelative(collection.last_collected_at) }}
          </p>
          <p class="muted small">{{ formatDateTime(collection.last_collected_at) }}</p>
          <p class="small">{{ coverage }}</p>
        </template>
        <p v-else class="muted">
          Nothing has been collected yet. Metrics arrive with the first pipeline run.
        </p>
        <p class="muted small">
          Runs every day and can be started by hand.
          <a v-if="airflowUrl" :href="airflowUrl" target="_blank" rel="noopener noreferrer">Open Airflow</a>
        </p>
      </article>

      <article class="card" data-test="report-status">
        <header class="card__header">
          <h3 class="card__title">Latest daily report</h3>
          <span class="tag">Django Celery task</span>
        </header>
        <template v-if="report">
          <p class="card__lead">
            <StatusBadge :status="report.status" />
            <span class="card__lead-detail">for {{ formatDate(report.snapshot_date) }}</span>
          </p>
          <dl class="facts small">
            <div>
              <dt>Requested</dt>
              <dd>{{ formatDateTime(report.requested_at) }}</dd>
            </div>
            <div v-if="report.started_at">
              <dt>Started</dt>
              <dd>{{ formatDateTime(report.started_at) }}</dd>
            </div>
            <div v-if="report.finished_at">
              <dt>{{ report.status === 'failed' ? 'Failed' : 'Finished' }}</dt>
              <dd>{{ formatDateTime(report.finished_at) }}</dd>
            </div>
            <div v-if="report.attempts > 1">
              <dt>Attempts</dt>
              <dd>{{ report.attempts }}</dd>
            </div>
          </dl>
          <p v-if="report.error_message" class="notice notice--error small" role="status">
            {{ report.error_message }}
          </p>
        </template>
        <p v-else class="muted">
          No report yet. One is generated after each successful collection.
        </p>
        <p class="muted small">
          Generated in the background after a collection; its status is independent of the
          Airflow run.
        </p>
      </article>
    </div>
  </section>
</template>

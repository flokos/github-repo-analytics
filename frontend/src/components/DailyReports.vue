<script setup>
import { computed } from 'vue'

import { formatCount, formatDate, formatDateTime, formatDelta } from '../utils/format.js'
import StatusBadge from './StatusBadge.vue'

const props = defineProps({
  /** Response of GET /api/reports/, newest first. */
  reports: { type: Array, required: true },
})

/** The newest report whose results can be shown line by line. */
const detailed = computed(
  () => props.reports.find((report) => report.repository_changes.length > 0) ?? null,
)

function hasResults(report) {
  return report.repository_count > 0
}

/**
 * Colour says whether a change is good, and the sign says its direction, so the meaning never
 * depends on colour alone. More stars or forks is good; open issues is neither, so it stays
 * in neutral ink.
 */
function deltaClass(value, upIsGood) {
  if (!upIsGood || !value) {
    return ''
  }
  return value > 0 ? 'delta--up' : 'delta--down'
}
</script>

<template>
  <section class="reports" aria-labelledby="reports-heading">
    <h2 id="reports-heading" class="section-title">Daily reports</h2>
    <p class="muted small">
      One report per collected day. Changes compare each repository with its previous available
      snapshot; a repository collected for the first time is not counted as a change.
    </p>

    <p v-if="reports.length === 0" class="card empty" data-test="reports-empty">
      No reports yet. The first one is generated after the first successful collection.
    </p>

    <template v-else>
      <div class="table-wrap card">
        <table>
          <thead>
            <tr>
              <th scope="col">Snapshot date</th>
              <th scope="col">Status</th>
              <th scope="col" class="num">Repositories</th>
              <th scope="col" class="num">Stars</th>
              <th scope="col" class="num">Forks</th>
              <th scope="col" class="num">Open issues</th>
              <th scope="col">Generated</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="report in reports" :key="report.id" :data-test="`report-${report.snapshot_date}`">
              <th scope="row">{{ formatDate(report.snapshot_date) }}</th>
              <td><StatusBadge :status="report.status" /></td>
              <template v-if="hasResults(report)">
                <td class="num">{{ formatCount(report.repository_count) }}</td>
                <td class="num">
                  {{ formatCount(report.total_stars) }}
                  <span class="delta" :class="deltaClass(report.stars_delta, true)">{{ formatDelta(report.stars_delta) }}</span>
                </td>
                <td class="num">
                  {{ formatCount(report.total_forks) }}
                  <span class="delta" :class="deltaClass(report.forks_delta, true)">{{ formatDelta(report.forks_delta) }}</span>
                </td>
                <td class="num">
                  {{ formatCount(report.total_open_issues) }}
                  <span class="delta">{{ formatDelta(report.open_issues_delta) }}</span>
                </td>
              </template>
              <td v-else colspan="4" class="muted">
                {{ report.error_message || 'Not generated yet' }}
              </td>
              <td>{{ formatDateTime(report.finished_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <details v-if="detailed" data-test="report-details">
        <summary>Changes per repository on {{ formatDate(detailed.snapshot_date) }}</summary>
        <div class="table-wrap card">
          <table>
            <thead>
              <tr>
                <th scope="col">Repository</th>
                <th scope="col" class="num">Stars</th>
                <th scope="col" class="num">Forks</th>
                <th scope="col" class="num">Open issues</th>
                <th scope="col">Compared with</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="change in detailed.repository_changes" :key="change.repository_id">
                <th scope="row">{{ change.full_name }}</th>
                <td class="num">
                  {{ formatCount(change.stars) }}
                  <span class="delta" :class="deltaClass(change.stars_delta, true)">{{ formatDelta(change.stars_delta) }}</span>
                </td>
                <td class="num">
                  {{ formatCount(change.forks) }}
                  <span class="delta" :class="deltaClass(change.forks_delta, true)">{{ formatDelta(change.forks_delta) }}</span>
                </td>
                <td class="num">
                  {{ formatCount(change.open_issues) }}
                  <span class="delta">{{ formatDelta(change.open_issues_delta) }}</span>
                </td>
                <td>
                  {{ change.previous_snapshot_date ? formatDate(change.previous_snapshot_date) : 'First snapshot' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>
    </template>
  </section>
</template>

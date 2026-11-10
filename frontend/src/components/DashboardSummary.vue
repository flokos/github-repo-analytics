<script setup>
import { computed } from 'vue'

import { formatCompact, formatCount } from '../utils/format.js'

const props = defineProps({
  /** Response of GET /api/dashboard/summary/, or null while it loads. */
  summary: { type: Object, default: null },
})

const tiles = computed(() => {
  const summary = props.summary
  if (!summary) {
    return []
  }
  return [
    { key: 'repositories', label: 'Tracked repositories', value: summary.active_repositories },
    { key: 'stars', label: 'Stars', value: summary.total_stars },
    { key: 'forks', label: 'Forks', value: summary.total_forks },
    {
      key: 'open-issues',
      label: 'Open issues',
      value: summary.total_open_issues,
      note: 'Includes open pull requests',
    },
  ]
})

const coverage = computed(() => {
  const summary = props.summary
  if (!summary || summary.active_repositories === 0) {
    return ''
  }
  if (summary.repositories_with_data === summary.active_repositories) {
    return 'Totals of the latest snapshot of every tracked repository.'
  }
  return `Totals of the ${summary.repositories_with_data} of ${summary.active_repositories} tracked repositories that have been collected so far.`
})
</script>

<template>
  <section aria-labelledby="summary-heading">
    <h2 id="summary-heading" class="section-title">Current totals</h2>
    <p v-if="!summary" class="muted">Loading totals…</p>
    <template v-else>
      <dl class="tiles">
        <div v-for="tile in tiles" :key="tile.key" class="tile" :data-test="`tile-${tile.key}`">
          <dt class="tile__label">{{ tile.label }}</dt>
          <!-- Compact for reading at a glance; the exact figure is in the title and the cards. -->
          <dd class="tile__value" :title="formatCount(tile.value)">
            {{ formatCompact(tile.value) }}
          </dd>
          <dd v-if="tile.note" class="tile__note">{{ tile.note }}</dd>
        </div>
      </dl>
      <p v-if="coverage" class="muted small">{{ coverage }}</p>
    </template>
  </section>
</template>

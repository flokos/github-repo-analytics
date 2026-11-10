<script setup>
import { computed } from 'vue'

import { formatCount, formatDateTime, formatRelative } from '../utils/format.js'

const props = defineProps({
  /** One item of GET /api/repositories/. */
  repository: { type: Object, required: true },
  selected: { type: Boolean, default: false },
  /** True while a remove request for this repository is in flight. */
  busy: { type: Boolean, default: false },
})

defineEmits(['select', 'remove'])

const metrics = computed(() => {
  const snapshot = props.repository.latest_snapshot
  if (!snapshot) {
    return []
  }
  return [
    { key: 'stars', label: 'Stars', value: snapshot.stars },
    { key: 'forks', label: 'Forks', value: snapshot.forks },
    { key: 'open-issues', label: 'Open issues', value: snapshot.open_issues, title: 'Includes open pull requests' },
  ]
})
</script>

<template>
  <li class="card repository" :class="{ 'repository--selected': selected }" :data-test="`repository-${repository.id}`">
    <div class="repository__header">
      <a class="repository__name" :href="repository.html_url" target="_blank" rel="noopener noreferrer">
        {{ repository.full_name }}
      </a>
      <button
        class="button button--quiet"
        type="button"
        :disabled="busy"
        :aria-label="`Remove ${repository.full_name} from tracking`"
        @click="$emit('remove', repository)"
      >
        {{ busy ? 'Removing…' : 'Remove' }}
      </button>
    </div>

    <dl v-if="metrics.length" class="repository__metrics">
      <div v-for="metric in metrics" :key="metric.key" :title="metric.title">
        <dt>{{ metric.label }}</dt>
        <dd>{{ formatCount(metric.value) }}</dd>
      </div>
    </dl>
    <p v-else class="muted small" data-test="no-metrics">
      No metrics yet. They are collected with the next pipeline run.
    </p>

    <div class="repository__footer">
      <p class="muted small" data-test="last-synced">
        <template v-if="repository.last_synced_at">
          Last synced {{ formatRelative(repository.last_synced_at) }}
          <span class="nowrap">({{ formatDateTime(repository.last_synced_at) }})</span>
        </template>
        <template v-else>Never synced</template>
      </p>
      <button
        class="button button--secondary"
        type="button"
        :aria-pressed="selected"
        @click="$emit('select', repository)"
      >
        {{ selected ? 'Showing history' : 'Show history' }}
      </button>
    </div>
  </li>
</template>

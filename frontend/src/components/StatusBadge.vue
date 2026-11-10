<script setup>
import { computed } from 'vue'

/**
 * A status as icon + label. Colour alone never carries the meaning: every state has its own
 * icon shape and its name written out.
 */
const props = defineProps({
  status: { type: String, required: true },
})

const STATUSES = {
  queued: { label: 'Queued', tone: 'neutral', icon: 'clock' },
  running: { label: 'Running', tone: 'info', icon: 'progress' },
  completed: { label: 'Completed', tone: 'good', icon: 'check' },
  failed: { label: 'Failed', tone: 'critical', icon: 'cross' },
}

const view = computed(
  () => STATUSES[props.status] ?? { label: props.status, tone: 'neutral', icon: 'clock' },
)
</script>

<template>
  <span class="badge" :class="`badge--${view.tone}`" :data-status="status">
    <svg class="badge__icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <template v-if="view.icon === 'check'">
        <circle cx="8" cy="8" r="7" fill="currentColor" />
        <path d="M4.6 8.3l2.2 2.2 4.6-4.9" fill="none" stroke="var(--surface)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
      </template>
      <template v-else-if="view.icon === 'cross'">
        <circle cx="8" cy="8" r="7" fill="currentColor" />
        <path d="M5.4 5.4l5.2 5.2M10.6 5.4l-5.2 5.2" fill="none" stroke="var(--surface)" stroke-width="1.8" stroke-linecap="round" />
      </template>
      <template v-else-if="view.icon === 'progress'">
        <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-width="2" opacity="0.3" />
        <path d="M8 2a6 6 0 0 1 6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
      </template>
      <template v-else>
        <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-width="2" />
        <path d="M8 4.8V8l2.2 1.4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
      </template>
    </svg>
    <span>{{ view.label }}</span>
  </span>
</template>

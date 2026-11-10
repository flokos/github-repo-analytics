<script setup>
import { formatDateTime } from '../utils/format.js'
import RepositoryCard from './RepositoryCard.vue'

defineProps({
  repositories: { type: Array, required: true },
  /** Repositories that were removed; their history is kept and they can be restored. */
  removed: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  selectedId: { type: Number, default: null },
  /** Ids with a remove or restore request in flight. */
  busyIds: { type: Array, default: () => [] },
})

defineEmits(['select', 'remove', 'restore'])
</script>

<template>
  <section aria-labelledby="repositories-heading">
    <h2 id="repositories-heading" class="section-title">
      Tracked repositories
      <span v-if="repositories.length" class="count">{{ repositories.length }}</span>
    </h2>

    <p v-if="loading" class="muted" data-test="repositories-loading">Loading repositories…</p>

    <p v-else-if="repositories.length === 0" class="card empty" data-test="repositories-empty">
      No repositories are tracked yet. Add one above to start collecting its metrics.
    </p>

    <ul v-else class="repository-list">
      <RepositoryCard
        v-for="repository in repositories"
        :key="repository.id"
        :repository="repository"
        :selected="repository.id === selectedId"
        :busy="busyIds.includes(repository.id)"
        @select="$emit('select', $event)"
        @remove="$emit('remove', $event)"
      />
    </ul>

    <details v-if="removed.length" class="removed" data-test="removed-repositories">
      <summary>Removed repositories ({{ removed.length }})</summary>
      <p class="muted small">
        These are no longer collected. Their history is kept, and restoring one continues it.
      </p>
      <ul class="removed__list">
        <li v-for="repository in removed" :key="repository.id" :data-test="`removed-${repository.id}`">
          <div>
            <span class="removed__name">{{ repository.full_name }}</span>
            <span class="muted small">removed {{ formatDateTime(repository.deactivated_at) }}</span>
          </div>
          <button
            class="button button--secondary"
            type="button"
            :disabled="busyIds.includes(repository.id)"
            :aria-label="`Restore ${repository.full_name}`"
            @click="$emit('restore', repository)"
          >
            {{ busyIds.includes(repository.id) ? 'Restoring…' : 'Restore' }}
          </button>
        </li>
      </ul>
    </details>
  </section>
</template>

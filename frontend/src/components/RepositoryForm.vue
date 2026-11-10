<script setup>
import { ref } from 'vue'

import { describeError } from '../utils/errors.js'

const props = defineProps({
  /**
   * Called with the address the user entered; resolves with the stored repository or rejects
   * with an `ApiError`. The backend is the only judge of whether an address is acceptable.
   * @type {import('vue').PropType<(url: string) => Promise<object>>}
   */
  submit: { type: Function, required: true },
})

const url = ref('')
const submitting = ref(false)
/** @type {import('vue').Ref<{ kind: 'success' | 'error' | 'warning', text: string } | null>} */
const feedback = ref(null)

async function onSubmit() {
  const value = url.value.trim()
  if (!value) {
    feedback.value = { kind: 'error', text: 'Paste the address of a GitHub repository first.' }
    return
  }

  submitting.value = true
  feedback.value = null
  try {
    const repository = await props.submit(value)
    feedback.value = {
      kind: 'success',
      text: repository.reactivated
        ? `${repository.full_name} is tracked again. Its earlier history was kept.`
        : `${repository.full_name} is now tracked. Its first metrics arrive with the next collection.`,
    }
    url.value = ''
  } catch (error) {
    const { message, temporary } = describeError(error)
    // The address stays in the field so it can be corrected or simply sent again.
    feedback.value = { kind: temporary ? 'warning' : 'error', text: message }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <form class="card form" novalidate @submit.prevent="onSubmit">
    <label class="form__label" for="repository-url">Add a repository</label>
    <p id="repository-url-hint" class="muted small">
      Paste the address of a public GitHub repository.
    </p>
    <div class="form__row">
      <input
        id="repository-url"
        v-model="url"
        class="input"
        type="url"
        inputmode="url"
        autocomplete="off"
        spellcheck="false"
        placeholder="https://github.com/owner/repository"
        aria-describedby="repository-url-hint repository-url-feedback"
        :aria-invalid="feedback?.kind === 'error'"
        :disabled="submitting"
      >
      <button class="button button--primary" type="submit" :disabled="submitting">
        {{ submitting ? 'Checking with GitHub…' : 'Add repository' }}
      </button>
    </div>
    <!-- Always in the DOM so that screen readers announce changes. -->
    <p
      id="repository-url-feedback"
      class="notice"
      :class="feedback ? `notice--${feedback.kind}` : 'notice--empty'"
      :role="feedback?.kind === 'success' ? 'status' : 'alert'"
      data-test="form-feedback"
    >
      {{ feedback?.text }}
    </p>
  </form>
</template>

import { onBeforeUnmount, onMounted } from 'vue'

/**
 * Calls `callback` every `intervalMs` while the tab is visible, and once more whenever the tab
 * becomes visible again. A hidden tab does not poll.
 */
export function usePolling(callback, intervalMs = 15_000) {
  let timer = null

  function onVisibilityChange() {
    if (!document.hidden) {
      callback()
    }
  }

  onMounted(() => {
    timer = setInterval(() => {
      if (!document.hidden) {
        callback()
      }
    }, intervalMs)
    document.addEventListener('visibilitychange', onVisibilityChange)
  })

  onBeforeUnmount(() => {
    clearInterval(timer)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })
}

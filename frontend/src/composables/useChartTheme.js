import { onBeforeUnmount, onMounted, reactive } from 'vue'

// Charts draw on a canvas, which cannot read CSS custom properties by itself. These are the
// roles a chart needs; their values are read from styles.css so light and dark stay in one place.
const ROLES = {
  series: '--series-1',
  surface: '--surface',
  textPrimary: '--text-primary',
  textSecondary: '--text-secondary',
  muted: '--text-muted',
  gridline: '--gridline',
  baseline: '--baseline',
}

// Used where the stylesheet is not available (unit tests).
const FALLBACK = {
  series: '#2a78d6',
  surface: '#fcfcfb',
  textPrimary: '#0b0b0b',
  textSecondary: '#52514e',
  muted: '#898781',
  gridline: '#e1e0d9',
  baseline: '#c3c2b7',
}

function readTheme() {
  const styles = getComputedStyle(document.documentElement)
  const theme = {}
  for (const [role, property] of Object.entries(ROLES)) {
    theme[role] = styles.getPropertyValue(property).trim() || FALLBACK[role]
  }
  return theme
}

/** Chart colours for the current colour scheme; updates when the system switches light/dark. */
export function useChartTheme() {
  const theme = reactive({ ...FALLBACK })
  const media = window.matchMedia?.('(prefers-color-scheme: dark)')

  function update() {
    Object.assign(theme, readTheme())
  }

  onMounted(() => {
    update()
    media?.addEventListener('change', update)
  })
  onBeforeUnmount(() => {
    media?.removeEventListener('change', update)
  })

  return theme
}

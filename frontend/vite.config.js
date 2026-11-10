import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [vue()],
  server: {
    // Only used by the optional dev server (`docker compose --profile dev up frontend-dev`).
    host: '0.0.0.0',
    port: 5173,
  },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.js'],
    restoreMocks: true,
    unstubGlobals: true,
  },
})

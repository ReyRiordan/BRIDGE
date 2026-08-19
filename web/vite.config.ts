import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Tailwind v4 has no config file and no PostCSS step — the Vite plugin plus the
// `@import 'tailwindcss'` in src/index.css is the whole setup.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Local dev only: the SPA calls the control plane SAME-ORIGIN through this
  // proxy (`npm run dev:api` serves :8000), so no CORS is involved on the local
  // path and src/config.ts can leave API_BASE_URL empty. Inert in a build.
  server: {
    proxy: {
      '/voice': 'http://localhost:8000',
      '/scenario': 'http://localhost:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
  },
})

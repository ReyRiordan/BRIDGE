import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Tailwind v4 has no config file and no PostCSS step — the Vite plugin plus the
// `@import 'tailwindcss'` in src/index.css is the whole setup.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: 'jsdom',
    globals: true,
  },
})

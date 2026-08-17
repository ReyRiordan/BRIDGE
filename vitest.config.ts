import { defineConfig } from 'vitest/config'

// Root vitest covers the Amplify Gen 2 infra only — web/ is a self-contained
// npm project with its own vitest config and its own CI job.
export default defineConfig({
  test: {
    include: ['amplify/**/*.test.ts'],
  },
})

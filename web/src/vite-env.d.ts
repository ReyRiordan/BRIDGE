/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** '1' opts the SPA into the local dev stack — see src/config.ts. */
  readonly VITE_BRIDGE_LOCAL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

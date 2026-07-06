/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/client" />

// Variables de entorno del dashboard (VITE_*). Se declaran aquí para que TypeScript
// las conozca bajo `strict`. Solo las de dev/build del front — nunca secretos.
interface ImportMetaEnv {
  readonly VITE_TENANT_SLUG?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

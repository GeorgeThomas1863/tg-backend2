import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// FRONTEND_PORT / BACKEND_PORT live in the repo-root .env shared with the backend.
const rootDir = fileURLToPath(new URL('..', import.meta.url))

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, rootDir, '')
  const apiBase = env.VITE_API_BASE || `http://localhost:${env.BACKEND_PORT || 8000}`
  return {
    plugins: [react()],
    server: {
      port: env.FRONTEND_PORT ? Number(env.FRONTEND_PORT) : 5173,
      // Fail instead of silently bumping to the next port — the backend's
      // CORS origin is pinned to FRONTEND_PORT.
      strictPort: true,
    },
    define: {
      'import.meta.env.VITE_API_BASE': JSON.stringify(apiBase),
    },
  }
})

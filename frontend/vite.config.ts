import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss()],
  server: {
    allowedHosts: ['.trycloudflare.com'],
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${loadEnv(mode, '.', 'TIO_').TIO_BACKEND_PORT || '8000'}`,
        changeOrigin: true,
      },
    },
  },
}))

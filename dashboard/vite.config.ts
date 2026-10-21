import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// `npm run dev` proxies API calls to the Python server; `npm run build` output is served by it directly.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, host: '127.0.0.1', proxy: { '/api': 'http://127.0.0.1:8765' } },
})

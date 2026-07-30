import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Tauri expects a fixed port and does not tolerate the dev server
// silently falling back to another one.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: {
      // src-tauri is watched by the Rust toolchain, not Vite.
      ignored: ['**/src-tauri/**'],
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // Safari 13 is the floor implied by the macOS 10.15 bundle target.
    target: 'safari14',
    sourcemap: false,
  },
})

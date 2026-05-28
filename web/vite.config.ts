import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg', 'apple-touch-icon.png'],
      manifest: {
        name: 'LazyClaw',
        short_name: 'LazyClaw',
        description: 'E2E encrypted AI agent platform',
        theme_color: '#10b981',
        background_color: '#0d0d0d',
        display: 'standalone',
        orientation: 'any',
        scope: '/',
        start_url: '/',
        categories: ['productivity', 'utilities'],
        icons: [
          {
            src: 'pwa-192x192.png',
            sizes: '192x192',
            type: 'image/png',
            purpose: 'any',
          },
          {
            src: 'pwa-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'any',
          },
          {
            src: 'pwa-maskable-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png,ico,woff,woff2}'],
        // Never precache index.html — nginx serves it no-cache so the SPA
        // shell stays fresh and always points at the latest hashed bundles.
        // Also skip the ~7 MB Univer editor chunk: it's lazy-loaded only when
        // the Sheets page opens and is useless offline (needs the API to
        // load/save), so it runtime-caches on first visit instead of bloating
        // the install bundle.
        globIgnores: ['**/index.html', '**/univer-*.js'],
        // Phosphor's SVG icon sprite is ~3 MB. Bump the precache cap so it
        // ships in the install bundle instead of becoming a runtime fetch.
        maximumFileSizeToCacheInBytes: 5 * 1024 * 1024,
        // Never cache API or WebSocket traffic — they must hit the gateway live.
        navigateFallbackDenylist: [/^\/api\//, /^\/ws\b/],
        runtimeCaching: [
          {
            urlPattern: ({ url }) => url.pathname.startsWith('/api/'),
            handler: 'NetworkOnly',
          },
          {
            urlPattern: ({ url }) => url.pathname.startsWith('/ws'),
            handler: 'NetworkOnly',
          },
          {
            urlPattern: ({ request }) => request.destination === 'image',
            handler: 'StaleWhileRevalidate',
            options: {
              cacheName: 'images',
              expiration: { maxEntries: 60, maxAgeSeconds: 60 * 60 * 24 * 30 },
            },
          },
          {
            urlPattern: ({ request }) =>
              request.destination === 'style' ||
              request.destination === 'script' ||
              request.destination === 'worker',
            handler: 'StaleWhileRevalidate',
            options: {
              cacheName: 'assets',
              expiration: { maxEntries: 60, maxAgeSeconds: 60 * 60 * 24 * 30 },
            },
          },
        ],
        cleanupOutdatedCaches: true,
        clientsClaim: true,
      },
      devOptions: {
        enabled: false,
      },
    }),
  ],
  build: {
    // Univer alone is ~7 MB; silence the 500 kB chunk warning for its bundle.
    chunkSizeWarningLimit: 8000,
    rollupOptions: {
      output: {
        // Pin all Univer code into one predictably-named `univer-*.js` chunk
        // so the PWA config can exclude it from precache (see globIgnores).
        manualChunks(id: string) {
          if (id.includes("node_modules/@univerjs") || id.includes("node_modules/univer/")) {
            return "univer";
          }
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:18789',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:18789',
        ws: true,
      },
    },
  },
})

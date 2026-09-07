const CACHE_NAME = 'approvals-sales-pilot-v2'
const APP_SHELL = ['/', '/index.html', '/manifest.webmanifest', '/approval-mark.svg']
const CACHEABLE_FILES = new Set(['/index.html', '/manifest.webmanifest', '/approval-mark.svg'])

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)))
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))),
    ),
  )
  self.clients.claim()
})

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return
  const url = new URL(event.request.url)
  if (url.origin !== self.location.origin) return

  if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request).catch(() => caches.match('/index.html')))
    return
  }

  const pathname = url.pathname
  if (
    url.search
    || pathname.startsWith('/api/')
    || pathname === '/runtime-config.json'
    || pathname === '/auth/callback'
  ) return

  if (!pathname.startsWith('/assets/') && !CACHEABLE_FILES.has(pathname)) return

  event.respondWith(
    caches.match(event.request).then((cached) => cached ?? fetch(event.request).then(async (response) => {
      if (response.ok) {
        const copy = response.clone()
        const cache = await caches.open(CACHE_NAME)
        await cache.put(event.request, copy)
      }
      return response
    })),
  )
})

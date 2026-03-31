// Service Worker — enables "Add to Home Screen" PWA install
// Always fetches fresh data; no offline caching (listings must be live).
const CACHE = "house-hunt-v1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(clients.claim()));

self.addEventListener("fetch", e => {
  // Let API calls always go to network
  if (e.request.url.includes("/api/")) {
    e.respondWith(fetch(e.request));
    return;
  }
  // For app shell, try cache then network
  e.respondWith(
    caches.open(CACHE).then(cache =>
      cache.match(e.request).then(cached =>
        cached || fetch(e.request).then(resp => {
          cache.put(e.request, resp.clone());
          return resp;
        })
      )
    )
  );
});

// Fermentation Manager service worker.
// Deliberately caches nothing: live data must never come from a stale cache, and every
// navigation goes to the network (index.html is served with no-cache). It exists so the app
// can be installed, and it is where native push notifications will be handled.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => { /* network only: let the browser handle every request */ });

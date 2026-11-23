// Fermentation Manager service worker (2.3).
// Caches the INTERFACE only (index.html, scripts, styles, fonts, icons), so the app opens even when
// the lab PC cannot be reached and then shows "Lab PC not reachable". Live data (/api) is never
// cached: it always comes from the network, or the request fails and the app says so.
const CACHE = "fm2-shell-v2.3.1";
const SHELL = ["/", "/index.html", "/manifest.json", "/icon-192.png", "/icon-512.png", "/favicon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => undefined).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith("fm2-shell-") && k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api")) return;   // data: network only

  // Pages: network first (always the latest version), cached shell when the PC is unreachable.
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).then((r) => {
      const copy = r.clone();
      caches.open(CACHE).then((c) => c.put("/index.html", copy)).catch(() => undefined);
      return r;
    }).catch(() => caches.match("/index.html")));
    return;
  }
  // Built assets have hashed names: cache first, then network (and remember it).
  e.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((r) => {
    if (r.ok) { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => undefined); }
    return r;
  })));
});

// Keeps the console openable when the server or network is briefly unreachable.
// Only the page shell and icons are cached; API calls always go to the server.
const CACHE = "assistant-shell-v1";
const SHELL = ["/ui", "/icons/icon-192.png", "/icons/favicon-48.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))));
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname === "/ui") {
    // Network first, so every deploy shows up at once; the cached copy is the fallback.
    event.respondWith(fetch(event.request).then((response) => {
      if (response.ok) {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put("/ui", copy));
      }
      return response;
    }).catch(() => caches.match("/ui")));
  } else if (url.pathname.startsWith("/icons/")) {
    event.respondWith(caches.match(event.request).then((hit) => hit || fetch(event.request)));
  }
});

// ShopPOS Cloud - service worker
//
// Strategy:
//  - App shell (HTML pages, CSS, JS, icons, manifest): cache-first, with a
//    background refetch that updates the cache for next time (stale-while-
//    revalidate). Makes reopening the installed app feel instant even on a
//    slow connection, while still picking up changes within a visit or two.
//  - Read-only dashboard data (GET /api/dashboard/*, /api/shops): network-
//    first, falling back to the last cached response if offline, so a shop
//    owner checking in with no signal still sees the last-known numbers
//    rather than a blank screen.
//  - Anything that logs in, registers, pushes data, or signs out: always
//    goes straight to the network, never cached, never served from cache.
//    Getting a stale or replayed response for one of these would be worse
//    than a failed request.

const SHELL_CACHE = "shoppos-shell-v1";
const DATA_CACHE = "shoppos-data-v1";

const SHELL_URLS = [
  "/login",
  "/static/css/style.css",
  "/static/js/dashboard.js",
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_URLS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names
          .filter((n) => n !== SHELL_CACHE && n !== DATA_CACHE)
          .map((n) => caches.delete(n))
      )
    )
  );
  self.clients.claim();
});

function isNeverCached(url) {
  return (
    url.pathname === "/api/login" ||
    url.pathname === "/api/register" ||
    url.pathname === "/api/push" ||
    url.pathname === "/logout"
  );
}

function isReadOnlyApi(url) {
  return (
    url.pathname.startsWith("/api/dashboard/") ||
    url.pathname === "/api/shops"
  );
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(SHELL_CACHE);
  const cached = await cache.match(request);
  const network = fetch(request)
    .then((resp) => {
      if (resp && resp.ok) cache.put(request, resp.clone());
      return resp;
    })
    .catch(() => null);
  return cached || (await network) || new Response("Offline", { status: 503 });
}

async function networkFirst(request) {
  const cache = await caches.open(DATA_CACHE);
  try {
    const resp = await fetch(request);
    if (resp && resp.ok) cache.put(request, resp.clone());
    return resp;
  } catch (e) {
    const cached = await cache.match(request);
    if (cached) return cached;
    return new Response(JSON.stringify({ ok: false, error: "Offline, no cached data yet" }),
      { status: 503, headers: { "Content-Type": "application/json" } });
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return; // never intercept writes

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (isNeverCached(url)) return; // let it hit the network untouched

  if (isReadOnlyApi(url)) {
    event.respondWith(networkFirst(req));
    return;
  }

  if (req.mode === "navigate" || SHELL_URLS.includes(url.pathname) || url.pathname.startsWith("/static/")) {
    event.respondWith(staleWhileRevalidate(req));
  }
});

/* CuiSync service worker (makes the site installable as an app).
   - Pages: always loaded from the server first (results, search and admin are live data).
     With no internet, the last copy of the page is shown, or the offline page.
   - CSS, JS, icons and fonts: served from the cache right away and refreshed in the background.
   - Searches, OCR, GPT, login and admin requests are never cached.
   Change VERSION when the static files change a lot, so phones drop the old copies. */
const VERSION = 'cuisync-v2-unified-v3';
const STATIC_CACHE = `${VERSION}-static`;
const PAGE_CACHE = `${VERSION}-pages`;
const OFFLINE_URL = '/offline';

const PRECACHE = [
  OFFLINE_URL,
  '/manifest.webmanifest',
  '/static/css/base-style.css',
  '/static/css/index.css',
  '/static/css/panel.css',
  '/static/css/results.css',
  '/static/scripts/components.js',
  '/static/scripts/text-search.js',
  '/static/scripts/tag-search.js',
  '/static/scripts/ocr.js',
  '/static/pwa/pwa.js',
  '/static/assets/no-image.svg',
  '/static/pwa/icons/icon-192.png',
  '/static/pwa/icons/icon-512.png',
  '/static/pwa/icons/favicon-32.png',
];

// Never cached: live data, uploads, logins and the admin area
const NETWORK_ONLY = [
  /^\/api\//, /^\/admin/, /^\/login/, /^\/logout/, /^\/register/, /^\/profile/, /^\/history/,
  /^\/extract-dish-info/, /^\/search-/, /^\/recipes\.json/, /^\/sw\.js/,
  /^\/compare(?:\/|$)/, /^\/similar-dishes(?:\/|$)/,
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(STATIC_CACHE);
    // One missing file must not stop the install
    await Promise.allSettled(PRECACHE.map((url) => cache.add(new Request(url, { cache: 'reload' }))));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

async function staleWhileRevalidate(request) {
  const cache = await caches.open(STATIC_CACHE);
  const cached = await cache.match(request);
  const network = fetch(request)
    .then((response) => {
      if (response && (response.ok || response.type === 'opaque')) cache.put(request, response.clone());
      return response;
    })
    .catch(() => cached);
  return cached || network;
}

const IS_LOCAL = ['localhost', '127.0.0.1', '[::1]'].includes(self.location.hostname);

async function networkFirstStatic(request) {
  const cache = await caches.open(STATIC_CACHE);
  try {
    const response = await fetch(request, { cache: 'no-store' });
    if (response && response.ok) cache.put(request, response.clone());
    return response;
  } catch (error) {
    return (await cache.match(request)) || Response.error();
  }
}

async function networkFirstPage(request) {
  const cache = await caches.open(PAGE_CACHE);
  try {
    const response = await fetch(request);
    if (response && response.ok) cache.put(request, response.clone());
    return response;
  } catch (error) {
    return (await cache.match(request)) || (await caches.match(OFFLINE_URL)) || Response.error();
  }
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;            // form posts (searches, uploads) go straight through
  const url = new URL(request.url);

  // Google Fonts and the map libraries (d3, topojson) from CDNs
  if (url.origin !== self.location.origin) {
    if (/fonts\.(googleapis|gstatic)\.com$|cdn\.jsdelivr\.net$/.test(url.hostname)) {
      event.respondWith(staleWhileRevalidate(request));
    }
    return;
  }

  if (NETWORK_ONLY.some((re) => re.test(url.pathname))) return;

  if (request.mode === 'navigate') {
    event.respondWith(networkFirstPage(request));
    return;
  }

  if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest') {
    // On your own computer (python app.py) always load the newest CSS/JS, so code changes show
    // on the first refresh. Online (Render) the cached copy is used for speed.
    event.respondWith(IS_LOCAL ? networkFirstStatic(request) : staleWhileRevalidate(request));
  }
});

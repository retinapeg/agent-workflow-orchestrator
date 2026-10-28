// Only cache the application shell. Never cache API data or queue offline mutations.
// /share is the Web Share Target (a spec shared from another app); it is served the same
// shell and falls back to the cached shell offline, but its query is never cached.
const CACHE='arena-shell-v3';
self.addEventListener('install',event=>{self.skipWaiting();event.waitUntil(caches.open(CACHE).then(c=>c.addAll(['/','/icon.svg','/manifest.webmanifest'])))});
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',event=>{const u=new URL(event.request.url);if(event.request.method!=='GET'||u.origin!==self.location.origin||u.pathname.startsWith('/api/'))return;if(u.pathname==='/share'){event.respondWith(fetch(event.request).catch(()=>caches.match('/')));return}if(u.pathname==='/'||u.pathname.startsWith('/assets/'))event.respondWith(fetch(event.request).then(r=>{if(r.ok){const copy=r.clone();caches.open(CACHE).then(c=>c.put(event.request,copy))}return r}).catch(()=>caches.match(event.request)))});

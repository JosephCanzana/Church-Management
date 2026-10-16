/* sw.js: the service worker for Web Push (Brick 12).
   Served from /sw.js (see notifications/push_views.py) so its scope is the whole site.
   It only does two things: show a notification when a push arrives, and open the app at the link
   when the notification is clicked. There is no offline cache and no fetch handler.
   PRIVACY: a push carries a title, a short body and a relative link, nothing else. */

// Static files are served under /static/ (STATIC_URL). A worker cannot use template tags.
const ICON = '/static/images/icon-192.png';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

// Only ever follow links on this site; anything else falls back to the home page.
function safeUrl(value) {
  try {
    const url = new URL(value || '/', self.location.origin);
    return url.origin === self.location.origin ? url.href : self.location.origin + '/';
  } catch (err) {
    return self.location.origin + '/';
  }
}

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (err) { /* not JSON: show the default */ }
  const options = {
    body: data.body || '',
    icon: ICON,
    data: { url: safeUrl(data.url) },
  };
  if (data.tag) options.tag = data.tag;   // same tag replaces the older notification instead of stacking
  // Always show something: iPhone and Chrome require a visible notification for every push.
  event.waitUntil(self.registration.showNotification(data.title || 'Church Management', options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || self.location.origin + '/';
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of windows) {
      if (new URL(client.url).origin === self.location.origin) {
        await client.focus();
        if ('navigate' in client) { try { await client.navigate(target); } catch (err) { /* ignore */ } }
        return;
      }
    }
    await self.clients.openWindow(target);
  })());
});

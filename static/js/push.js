/* push.js: the Settings "device" card (Brick 12).
   Registers the service worker, and defines the Alpine component pushDevice() used by
   notifications/_push_card.html. Loaded from base.html BEFORE alpine.min.js, only when push is set up,
   because the alpine:init listener has to exist before Alpine starts.

   States shown to the person: loading, on, off, blocked, unsupported, needs_install.
   Every change asks through the global confirm dialog ($store.ui.confirm) and reports with a toast.
   Subscription details (endpoint, keys) are sent only to our own server and never shown or logged. */
(function () {
  'use strict';

  var SW_URL = '/sw.js';

  // The browser wants the public key as bytes, the page has it as base64url text.
  function keyToBytes(base64url) {
    var pad = '='.repeat((4 - (base64url.length % 4)) % 4);
    var raw = atob((base64url + pad).replace(/-/g, '+').replace(/_/g, '/'));
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  function isIos() {
    // iPadOS 13+ reports itself as a Mac, so also look for a touch screen.
    return /iPhone|iPad|iPod/.test(navigator.userAgent) ||
      (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  }

  function isInstalled() {
    return window.navigator.standalone === true ||
      (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches);
  }

  // SHA-256 of the endpoint, to mark "This device" in the list. Same value as push.hash_endpoint() on the server.
  async function sha256Hex(text) {
    if (!window.crypto || !window.crypto.subtle) return '';
    var bytes = new TextEncoder().encode(text);
    var digest = await window.crypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest)).map(function (b) { return b.toString(16).padStart(2, '0'); }).join('');
  }

  var registrationPromise = null;
  // Register once and wait for the worker to be ready. A timeout stops a broken worker from hanging the card.
  function getRegistration() {
    if (!registrationPromise) {
      registrationPromise = navigator.serviceWorker.register(SW_URL, { scope: '/' })
        .then(function () {
          return Promise.race([
            navigator.serviceWorker.ready,
            new Promise(function (_, reject) { setTimeout(function () { reject(new Error('timeout')); }, 8000); }),
          ]);
        });
    }
    return registrationPromise;
  }

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () { getRegistration().catch(function () {}); });
  }

  document.addEventListener('alpine:init', function () {
    Alpine.data('pushDevice', function () {
      return {
        state: 'loading',
        busy: false,
        thisHash: '',
        hasDevices: false,
        cfg: {},

        init() {
          var d = this.$root.dataset;
          this.cfg = {
            key: d.publicKey, csrf: d.csrf,
            subscribeUrl: d.subscribeUrl, unsubscribeUrl: d.unsubscribeUrl,
            statusUrl: d.statusUrl, testUrl: d.testUrl,
          };
          this.hasDevices = parseInt(d.deviceCount || '0', 10) > 0;
          this.detect().then(function (s) { this.state = s; }.bind(this));
        },

        // The server's device list is the truth; this works out what THIS browser can do right now.
        async detect() {
          if (isIos() && !isInstalled()) return 'needs_install';
          if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return 'unsupported';
          if (Notification.permission === 'denied') return 'blocked';
          try {
            var reg = await getRegistration();
            var sub = await reg.pushManager.getSubscription();
            if (!sub) return 'off';
            this.thisHash = await sha256Hex(sub.endpoint);
            var res = await this.post(this.cfg.statusUrl, { endpoint: sub.endpoint });
            if (res.ok && !res.data.registered) {
              // The server no longer knows this browser (removed from another device): keep both sides in step.
              try { await sub.unsubscribe(); } catch (err) { /* ignore */ }
              this.thisHash = '';
              return 'off';
            }
            return 'on';
          } catch (err) {
            return 'unsupported';
          }
        },

        async post(url, body) {
          try {
            var response = await fetch(url, {
              method: 'POST',
              credentials: 'same-origin',
              headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.cfg.csrf, 'X-Requested-With': 'fetch' },
              body: JSON.stringify(body || {}),
            });
            var data = {};
            try { data = await response.json(); } catch (err) { /* not JSON */ }
            return { ok: response.ok && data.ok !== false, data: data, error: data.error || '' };
          } catch (err) {
            return { ok: false, data: {}, error: 'Could not reach the server. Check your connection and try again.' };
          }
        },

        toast(message, type) { Alpine.store('ui').toast(message, type || 'info'); },

        async enable() {
          var ok = await Alpine.store('ui').confirm({
            title: 'Turn on notifications?',
            message: 'This device will show alerts such as event reminders, even when the app is closed.',
            confirmText: 'Turn on',
          });
          if (!ok) return;
          this.busy = true;
          try {
            var permission = await Notification.requestPermission();
            if (permission !== 'granted') {
              this.state = permission === 'denied' ? 'blocked' : 'off';
              this.toast('Notifications were not allowed on this device.', 'warning');
              return;
            }
            var reg = await getRegistration();
            var sub = await reg.pushManager.getSubscription();
            if (!sub) {
              var options = { userVisibleOnly: true, applicationServerKey: keyToBytes(this.cfg.key) };
              try {
                sub = await reg.pushManager.subscribe(options);
              } catch (err) {
                // An old subscription made with different server keys blocks a new one: drop it and retry once.
                var stale = await reg.pushManager.getSubscription();
                if (stale) await stale.unsubscribe();
                sub = await reg.pushManager.subscribe(options);
              }
            }
            var res = await this.post(this.cfg.subscribeUrl, sub.toJSON());
            if (!res.ok) {
              try { await sub.unsubscribe(); } catch (err) { /* ignore */ }
              this.toast(res.error || 'Could not turn on notifications.', 'error');
              return;
            }
            window.location.reload();   // the server stored a message; it shows as a toast after the reload
          } catch (err) {
            this.toast('Could not turn on notifications on this device.', 'error');
          } finally {
            this.busy = false;
          }
        },

        async disable() {
          var ok = await Alpine.store('ui').confirm({
            title: 'Turn off notifications?',
            message: 'This device will stop receiving alerts. The bell inside the app keeps working.',
            confirmText: 'Turn off',
            danger: true,
          });
          if (!ok) return;
          this.busy = true;
          try {
            var reg = await getRegistration();
            var sub = await reg.pushManager.getSubscription();
            if (sub) {
              var res = await this.post(this.cfg.unsubscribeUrl, { endpoint: sub.endpoint });
              if (!res.ok) { this.toast(res.error || 'Could not turn off notifications.', 'error'); return; }
              try { await sub.unsubscribe(); } catch (err) { /* the server side is already done */ }
            }
            window.location.reload();
          } catch (err) {
            this.toast('Could not turn off notifications on this device.', 'error');
          } finally {
            this.busy = false;
          }
        },

        // Sending a test changes nothing, so it does not ask first; the result is a toast.
        async sendTest() {
          this.busy = true;
          try {
            var res = await this.post(this.cfg.testUrl, {});
            if (!res.ok) { this.toast(res.error || 'Could not send the test.', 'error'); return; }
            this.toast(res.data.message, res.data.reached > 0 ? 'success' : 'warning');
          } finally {
            this.busy = false;
          }
        },
      };
    });
  });
})();

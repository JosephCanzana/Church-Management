document.addEventListener('alpine:init', () => {
  Alpine.store('ui', {
    toasts: [],
    dialog: { open: false, title: '', message: '', confirmText: 'Confirm', danger: false, resolve: null },
    // Show-once information such as generated passwords. Not stored anywhere else.
    secrets: { open: false, title: '', message: '', headers: [], rows: [], resolve: null },

    toast(message, type = 'info', ms = 5000) {
      const id = Date.now() + Math.random();
      this.toasts.push({ id, message, type });
      setTimeout(() => this.dismiss(id), ms);
    },
    dismiss(id) { this.toasts = this.toasts.filter(t => t.id !== id); },

    confirm(opts = {}) {
      return new Promise(resolve => {
        this.dialog = {
          open: true,
          title: opts.title || 'Are you sure?',
          message: opts.message || '',
          confirmText: opts.confirmText || 'Confirm',
          danger: !!opts.danger,
          resolve,
        };
      });
    },
    answer(ok) { this.dialog.open = false; this.dialog.resolve?.(ok); },

    // Blocking "save this now" dialog: { title, message, headers: [..], rows: [[..], ..] }.
    // It closes only through closeSecrets(), and the values are wiped when it does.
    showSecrets(opts = {}) {
      return new Promise(resolve => {
        this.secrets = {
          open: true,
          title: opts.title || 'Save this now',
          message: opts.message || 'This is shown only once.',
          headers: opts.headers || [],
          rows: opts.rows || [],
          resolve,
        };
      });
    },
    closeSecrets() {
      const done = this.secrets.resolve;
      this.secrets = { open: false, title: '', message: '', headers: [], rows: [], resolve: null };
      done?.(true);
    },
    async copySecrets() {
      const lines = [this.secrets.headers, ...this.secrets.rows].filter(r => r.length);
      try {
        await navigator.clipboard.writeText(lines.map(r => r.join('\t')).join('\n'));
        this.toast('Copied to the clipboard.', 'success');
      } catch (err) {
        this.toast('Could not copy. Select the text and copy it by hand.', 'warning');
      }
    },
  });

  // Django messages -> toasts
  const map = { success: 'success', error: 'error', warning: 'warning', info: 'info', debug: 'info' };
  document.querySelectorAll('#dj-messages [data-level]').forEach(el => {
    Alpine.store('ui').toast(el.textContent.trim(), map[el.dataset.level] || 'info');
  });

  // A page can hand over show-once data with {{ data|json_script:"secrets-data" }}.
  // It is read once and the element is removed from the page.
  const secretsEl = document.getElementById('secrets-data');
  if (secretsEl) {
    try { Alpine.store('ui').showSecrets(JSON.parse(secretsEl.textContent)); } catch (err) { /* ignore bad data */ }
    secretsEl.remove();
  }
});

// Declarative confirm: <form data-confirm="..."> or a submit button with data-confirm
document.addEventListener('submit', async (e) => {
  const form = e.target;
  const msg = form.dataset.confirm || e.submitter?.dataset.confirm;
  if (!msg || form.dataset.confirmed) return;
  e.preventDefault();
  // "{n}" in the title or message becomes the number of ticked people (bulk actions).
  const count = form.querySelectorAll('input[name="ids"]:checked').length;
  const fill = (text) => (text ? text.replaceAll('{n}', count) : text);
  const ok = await Alpine.store('ui').confirm({
    title: fill(form.dataset.confirmTitle || e.submitter?.dataset.confirmTitle),
    message: fill(msg),
    confirmText: form.dataset.confirmText || e.submitter?.dataset.confirmText,
    danger: (form.dataset.confirmDanger ?? e.submitter?.dataset.confirmDanger) !== undefined,
  });
  if (ok) {
    // requestSubmit() fires the submit event again, synchronously. The flag lets that
    // second event through, and is removed right after so a later submit asks again.
    form.dataset.confirmed = '1';
    form.requestSubmit(e.submitter || undefined);
    delete form.dataset.confirmed;
  }
}, true);


// ── Double-submit guard ──────────────────────────────────
// The first real submit of a POST form locks it, so a second click, a double click or
// Enter pressed twice cannot send it twice. This is a convenience only: the server is
// built to be safe without it (see AGENTS.md). Opt out with data-no-lock on the form.
(() => {
  const LOCK_MS = 20000;          // safety net for responses that do not leave the page

  function unlock(form) {
    delete form.dataset.submitting;
    form.querySelectorAll('[data-was-locked]').forEach(b => {
      b.disabled = false;
      b.removeAttribute('aria-busy');
      b.removeAttribute('data-was-locked');
    });
  }

  // Bubble phase on purpose: the confirm handler above runs first (capture phase) and
  // cancels the event while it waits for an answer. A cancelled event is not a real submit.
  document.addEventListener('submit', (e) => {
    const form = e.target;
    if (e.defaultPrevented) return;
    if (!(form instanceof HTMLFormElement) || form.method.toLowerCase() !== 'post') return;
    if (form.hasAttribute('data-no-lock')) return;
    if (form.dataset.submitting) { e.preventDefault(); return; }

    form.dataset.submitting = '1';
    // One tick later: a disabled button is left out of the form data, and the clicked
    // button's name and value may matter to the server.
    setTimeout(() => {
      form.querySelectorAll('button:not([type="button"]):not([type="reset"]), input[type="submit"]').forEach(b => {
        if (b.disabled) return;
        b.disabled = true;
        b.setAttribute('aria-busy', 'true');
        b.setAttribute('data-was-locked', '');
      });
    }, 0);
    setTimeout(() => unlock(form), LOCK_MS);
  });

  // Coming back with the browser's Back button can restore a page with locked forms.
  window.addEventListener('pageshow', (e) => {
    if (e.persisted) document.querySelectorAll('form[data-submitting]').forEach(unlock);
  });
})();


// ── Alpine stores: sidebar and colour mode ────────────────
document.addEventListener('alpine:init', () => {
 
    // The expanded sidebar panel (desktop overlay and mobile drawer).
    Alpine.store('sidebar', {
        expanded: false,
 
        // Open the panel, lock page scroll, and move focus to its close button.
        open() {
            this.expanded = true
            document.documentElement.style.overflow = 'hidden'
            setTimeout(() => document.getElementById('sidebar-close')?.focus(), 60)
        },
 
        // Close the panel; returnFocus sends keyboard users back to the visible menu button
        // (the rail's on sm and up, the header's on mobile).
        close(returnFocus = false) {
            if (!this.expanded) return
            this.expanded = false
            document.documentElement.style.overflow = ''
            if (returnFocus) setTimeout(() => [...document.querySelectorAll('[data-sidebar-toggle]')].find(b => b.offsetParent)?.focus(), 60)
        },
 
        toggle() { this.expanded ? this.close(true) : this.open() },
    })
 
    // Light/dark mode. base.html defines window.toggleMode() and sets the starting class.
    Alpine.store('mode', {
        dark: document.documentElement.classList.contains('dark'),
        toggle() { this.dark = window.toggleMode() === 'dark' },
    })
 
})

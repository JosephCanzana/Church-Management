document.addEventListener('alpine:init', () => {
  Alpine.store('ui', {
    toasts: [],
    dialog: { open: false, title: '', message: '', confirmText: 'Confirm', danger: false, resolve: null },

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
  });

  // Django messages -> toasts
  const map = { success: 'success', error: 'error', warning: 'warning', info: 'info', debug: 'info' };
  document.querySelectorAll('#dj-messages [data-level]').forEach(el => {
    Alpine.store('ui').toast(el.textContent.trim(), map[el.dataset.level] || 'info');
  });
});

// Declarative confirm: <form data-confirm="..."> or a submit button with data-confirm
document.addEventListener('submit', async (e) => {
  const form = e.target;
  const msg = form.dataset.confirm || e.submitter?.dataset.confirm;
  if (!msg || form.dataset.confirmed) return;
  e.preventDefault();
  const ok = await Alpine.store('ui').confirm({
    title: form.dataset.confirmTitle || e.submitter?.dataset.confirmTitle,
    message: msg,
    confirmText: form.dataset.confirmText || e.submitter?.dataset.confirmText,
    danger: (form.dataset.confirmDanger ?? e.submitter?.dataset.confirmDanger) !== undefined,
  });
  if (ok) { form.dataset.confirmed = '1'; form.requestSubmit(e.submitter); }
}, true);


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

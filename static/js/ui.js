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
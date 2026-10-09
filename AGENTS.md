# AGENTS.md

## Project overview
Church-Management is a Django + PostgreSQL system for Jesus Is Lord Church
(JIL) and its local branches ("extensions"). Scope: accounts and hierarchy,
attendance, tithes and offering, faith goals and Bible streaks, accountability
buddies, prayer requests, events, resources, notifications and audit logging.

**Status:** all 14 apps and 43 models exist, with migrations and seed data.
`core.test_schema` verifies them. The base templates, design tokens, light/dark
toggle, the public landing page (built, with a default for every block until `pages` content is managed) and the redesigned login page exist. Services, views and forms are
mostly not built yet. The login is built (`accounts/backends.py`, `services.py`,
`forms.py`, `views.py`, `urls.py`; routes `/accounts/login/`, `/accounts/logout/`, `/accounts/activate/`);
 `/activate/` is the built activation page (new password + confirm). Forgot-password and reset
are not built yet; the "Forgot password?" link on the login page is a placeholder
(`href="#"`). The landing page Log in buttons use `{% url 'accounts:login' %}`. Next: forgot/reset password,
then features on top of the models. `role_required` exists (`accounts/decorators.py`). Super-admin extension management is built (`/superadmin/extensions/`); user management is built (`/superadmin/users/`). A global toast and confirm
dialog (`static/js/ui.js`, rendered by `includes/messages.html`) report results and
guard destructive actions.

Reference docs (the source of truth for behaviour):
- `docs/schema.sql`: original SQL design, with comments on every rule
- `docs/flows.md`: the feature flows (login, attendance, tithes, buddy, ...)
- `docs/verify.sql`: read-only checks to run in pgAdmin

## Tech stack
Python 3.12, Django 5.1+ (tested on 5.2 and 6.0), PostgreSQL 16, Tailwind CSS
(standalone CLI), Alpine.js, Docker Compose.

## Repository layout
- `church_management/`: project package (settings, urls, asgi/wsgi). Config only.
- `apps/`: Python package containing all Django apps (`accounts/`, `core/`,
  `attendance/`, etc.). Import app code through `apps.<app>`, for example
  `from apps.core.models import ...`; use `apps.<app>.apps.<AppConfig>` for
  explicit AppConfig paths and `apps.<app>.<module>` for dotted settings paths.
  Keep each Django app label short (`accounts`, `core`, etc.): labels are used
  by `AUTH_USER_MODEL`, migration dependencies, relations, and URL namespaces.
- Standard files per app: `models.py`,
  `services.py` (business logic), `views.py`, `urls.py`, `forms.py`,
  `permissions.py`, `management/commands/` (jobs).
- `templates/` (project-level: `base.html`, `base_public.html`, `base_app.html`,
  `includes/` with `public_navbar.html`, `footer.html`, `logo.html`, `icon.html`,
  `sidebar.html`, `sidebar_rail.html`, `sidebar_utilities.html`, `nav_link.html`, `brand_mark.html`, `topbar.html`, `bottom_nav.html`, `messages.html`, `modal.html`, `breadcrumb.html`).
  Page templates that belong to one app go in that app's `templates/<app>/` folder
  (e.g. `apps/pages/templates/pages/landing.html`, `apps/accounts/templates/accounts/login.html`).
- `static/`: `css/input.css` (Tailwind source and design tokens), `css/themes.css`
  (dark overrides), `css/output.css` (generated, not committed), `fonts/`,
  `icons/sprite.svg`, `images/` (logos), `js/alpine.min.js`, `js/ui.js` (toast and
  confirm store).
- `theme/` (Tailwind CLI binary, gitignored), `docs/`.
- `Dockerfile`, `docker-compose.yml`, `Makefile`, `requirements.txt`, `.env.example`.

## Commands (always through Docker)
```bash
docker compose up --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser   # Enter at "Account id"
docker compose exec web python manage.py seed_superadmin   # dev only: super-admin from SEED_SUPERADMIN_* in .env
docker compose exec web python manage.py check
docker compose exec web python manage.py makemigrations --check --dry-run
docker compose exec web python manage.py test
docker compose exec web python manage.py test apps.core.test_text -v 2
make tailwind-watch    # separate terminal
make tailwind-build
```
App at http://127.0.0.1:8001/ (use `127.0.0.1`, not `localhost`).
`.env` is read with python-decouple; keys are in `.env.example`.

Use `python manage.py test` to run the complete test suite. Test module paths
include the Python package prefix (for example,
`apps.accounts.tests.test_accounts`). The accounts tests are grouped under
`apps/accounts/tests/`; run them by passing the full module paths. Do not rewrite
short Django app labels in model references, migrations, or URL namespaces when
moving or reorganizing app code.

## Settings that must stay true
- `AUTH_USER_MODEL = 'accounts.User'`, set before the first migrate. Never change
  it on a migrated database.
- `INSTALLED_APPS` includes `django.contrib.postgres` (needed for `ArrayField`
  on Django 6.0) and all 14 project apps. Use `theming.apps.ThemingConfig` for
  `theming` (it registers the signal).
- `TIME_ZONE = 'Asia/Manila'`, `USE_TZ = True`.
- Plain `date` columns (check-ins, streaks, daily verse, attendance) use the
  Manila calendar date.

## Apps, tables, and where logic belongs

| App | Tables | Logic that lives here |
|---|---|---|
| `core` | `site_setting`, `retention_policy` | Abstract bases (`TimestampedModel`, `ArchivableModel`), `archived_matches_status()`, generic archive / restore / force-delete, nightly `purge_archived`, permission hierarchy helpers, retention screen |
| `audit` | `audit_log` | `log_action()` in `audit/services.py` (strips sensitive keys, sends `action_logged` from `audit/signals.py`), log viewer, `cleanup_audit_log` |
| `accounts` | `extension`, `app_user`, `special_role`, `user_special_role`, `extension_special_role_limit`, `default_password`, `email_token`, `user_extension_history` | Login/logout/activation, forgot + reset password, email verification, profile, create/manage accounts, extensions, special roles and limits, default passwords, `archive_inactive_users`, `auto_transfer_extension`, `cleanup_email_tokens`, `mark_attended()` |
| `theming` | `theme`, `theme_palette`, `user_settings` | Theme CRUD (both palettes in one transaction), contrast warning, CSS generation + cache, context processor; `UserSettings` row created by `signals.py` |
| `pages` | `church_content`, `landing_image`, `donation_account`, `jil_video` | Landing page (`get_landing_context()` in `pages/services.py`, a default for every block) and terms editing, donation page, `fetch_jil_videos` |
| `bible` | `daily_verse`, `bible_chapter_read` | Reader, API.bible / scrollmapper client, daily verse, `record_chapter_read()` |
| `faith` | `goal`, `goal_checkin`, `user_daily_activity`, `user_streak` | Goals, check-ins, consistency, heatmap, streak rules, `complete_ended_goals` |
| `events` | `event` | Event CRUD, change/cancel notices, `send_event_reminders` |
| `resources` | `monthly_theme`, `monthly_resource`, `weekly_powerpoint`, `general_resource` | Monthly theme + files, weekly PPT (admin/coordinator only), general resources |
| `attendance` | `attendance`, `attendance_member`, `attendance_first_timer` | Sheets, first timers, upload (freeze counts, update `last_attended_at`), reopen (admin) |
| `tithes` | `tithes_offering`, `tithe_entry` | Entries, live totals, denomination count, finalize, reopen; members see only their own entries |
| `buddy` | `user_buddy`, `buddy_request`, `buddy_note` | Request/accept (transactional one-partner check), notes/nudges, end partnership, view partner goals |
| `prayer` | `prayer_request`, `prayer_reply` | Send, inbox (coordinator scope, anonymous masking), reply, done, delete |
| `notifications` | `notification`, `notification_preference`, `notification_rule` | `notify()`, bell + list, preferences, admin-alert listener, `cleanup_notifications` |

Before adding a model, put it in the app whose data it shares a lifecycle
with. Only create a new app if the answer is "none".

### Dependency rules
1. Direction: `core` <- `audit` <- `accounts` <- everything else. `theming`
   depends on `accounts`; `tithes` depends on `attendance`; `attendance` depends
   on `events`.
2. `audit` and `notifications` never import feature apps. They refer to other
   records with `entity_type` / `entity_id`, not foreign keys.
3. Cross-app reactions use signals, not imports upward:
   `audit.log_action()` sends `action_logged` and `notifications` listens to
   apply `notification_rule`; the daily open streak hooks Django's
   `user_logged_in` signal from `faith`.
4. Each app owns its jobs as management commands. Business logic goes in
   `services.py`, wrapped in `transaction.atomic()`, calling `log_action()` and
   `notify()` in the same transaction. Views stay thin.

## Model conventions
- Every model sets `db_table` to the schema table name. The purge trigger
  matches `retention_policy.entity_type` to the table name, so a new archivable
  table needs: `db_table`, a `retention_policy` row, a trigger + partial index
  (add to a new migration, copy the pattern in `core/migrations/0002`).
- `Meta.constraints` uses `condition=` (not `check=`, removed in Django 6.0).
- Enums are `TextChoices`; cross-field rules are database constraints.
- Archivable models inherit `ArchivableModel` and use
  `archived_matches_status()` when they have a `status` field.
- Do **not** write `purge_at` from Django when archiving; the trigger sets it.
  To extend one record, update only that column:
  `Model.objects.filter(pk=...).update(purge_at=...)`. Use
  `save(update_fields=[...])` on archivable rows.
- `on_delete` is enforced by Django, not by database foreign keys. Do not delete
  rows with raw SQL. Rules: personal content CASCADE; church records that
  mention a user SET NULL (names are snapshotted); `created_by` style columns
  SET NULL; extensions with people or records RESTRICT.
- Money is `Decimal(12,2)`, never float. Totals on uploaded attendance/tithes
  are frozen values, not recalculated.
- Four join tables use a surrogate `id` plus a unique constraint instead of a
  composite primary key (Django limitation).

## Behaviour rules agents must not break
- **Account id:** `<counter from account_number_seq><year, Manila>`, generated
  in `User.save()`. Never reused, never edited. `bulk_create()` skips it, so
  create users one by one.
- **Login:** account id + password, or a *verified* email. Wrong account and
  wrong password show the same message. Log failures. Archived accounts are
  denied; not-activated or `must_change_password` users go to activation.
- **Login implementation:** `AccountIdOrEmailBackend` (listed in
  `AUTHENTICATION_BACKENDS`) accepts an account id or a verified email and
  rejects archived users; `accounts.services.attempt_login()` calls Django's
  `authenticate()` then `login()` (so `user_logged_in` fires), writes failures to
  `audit_log` (`account.login_failed`, status failed, reason code in `after`; never the
  password or the typed identifier)
  and reports whether activation is needed. Every failure shows
  `forms.GENERIC_LOGIN_ERROR`. `next` is only followed if
  `url_has_allowed_host_and_scheme` passes. Post-login landing URLs are in
  `decorators.ROLE_HOME_NAMES` (the `dashboards` routes). Brute-force
  throttling is not built yet.
- **Status:** `not_activated -> active -> archived`, plus `suspended` (a reversible login block set by the
  super-admin; never purged; `User.is_active` is false, so open sessions end at once). "Inactive for a year" is an
  automatic archive with `archive_reason='inactive'`. Restore and unsuspend go back to `active` when
  `must_change_password` is false, otherwise to `not_activated`. Deactivate = back to `not_activated` with
  `must_change_password=True`.
- **Default passwords:** hash only, and personal: each person who manages others keeps their own, one per role
  beneath them (`DefaultPassword.owner` + `applies_to_role`). Used when that person creates or resets someone and
  types nothing; a typed custom password always overrides. No global or per-extension default.
- **Special-role limits** are enforced in a transaction with the extension row
  locked.
- **Uploaded attendance/tithes** are locked; only an admin reopens them
  (logged and alerts admins). Force delete is super admin only, archived rows
  only, logged before deleting; delete files from disk first.
- **Privacy:** never write passwords, tokens, goal text, notes, prayer text or
  tithe amounts to `audit_log` or `notification.data`. Notifications say that
  something happened; the linked page does the permission check. Anonymous
  prayer requests hide the sender in UI/API but keep `user_id`.
- **Enumeration:** forgot-password always shows the same message.
- **Activation:** `accounts/services/activation.py` (`user_needs_activation`, `activate_account`), form `ActivationForm`,
  view `activate_view`, template `accounts/activate.html`. A person must activate when `status=not_activated` OR
  `must_change_password=True` (a reset leaves the status `active`). `activate_account` locks the row, sets the password,
  clears `must_change_password`, turns `not_activated` into `active`, re-signs the session
  (`update_session_auth_hash`) and logs `account.activated` (flag logged as `needs_activation`). It is idempotent, and
  suspended or archived accounts are left alone. `ActivationRequiredMiddleware` (`accounts/middleware.py`, after
  `MessageMiddleware`) redirects everything except `accounts:activate` and `accounts:logout` while activation is pending.

## Nightly jobs (each is a management command in its own app)
`purge_archived` (core, children first, files before rows, skips extensions
still in use), `archive_inactive_users`, `auto_transfer_extension`,
`cleanup_email_tokens` (accounts), `complete_ended_goals` (faith),
`cleanup_notifications`, `cleanup_audit_log`; every 15 minutes
`send_event_reminders`; hourly `fetch_jil_videos`. Jobs log with
`source='system_job'` and no actor.

## Frontend conventions
- Colors come from CSS custom properties (design tokens) generated from the
  active `theme_palette`, never raw Tailwind palette classes or `dark:`. Use
  utilities such as `bg-bg`, `bg-surface`, `text-txt-primary`,
  `text-txt-secondary`, `border-border`, `bg-accent`. Light/dark is the
  `data-mode` / `.dark` state, not a Tailwind variant.
- Icons: self-hosted Heroicons sprite,
  `<use href="{% static 'icons/sprite.svg' %}#name">`. No icon fonts or CDNs.
  `aria-hidden="true"` on the icon, `aria-label` on the parent button/link.
- Layouts: `base_public.html` for logged-out pages, `base_app.html` for the
  authenticated app. Nav entries are data in `accounts/navigation.py` (`NAV_ITEMS`), not markup: never hardcode a
  link in a sidebar or bottom-bar template.
- Template comments: `{# ... #}` is single-line only; a multi-line one renders as page text.
  Use `{% comment %} ... {% endcomment %}` for anything longer than one line or that
  mentions template tags. The one exception is the file header: it is an HTML comment
  (`<!-- ... -->`) placed before `{% extends %}` and it must contain no template tags,
  because anything tag-like inside an HTML comment still executes. Never put a comment
  inside a tag.
- `base_public.html` composes `includes/public_navbar.html` and `includes/footer.html`
  inside `{% block body %}`: a `min-h-dvh` flex column holding the navbar, a `flex-1`
  `<main>` that centers the `content` block (`items-center justify-center px-4 py-10`),
  then the footer. Pages never include the footer themselves. An `{% include %}` outside a block is ignored in a child
  template, so keep includes inside blocks. Public pages fill only `content` (and
  `main_align`).
- Public navbar: on `md` and up it is a three-column grid (`md:grid-cols-[1fr_auto_1fr]`) so
  the links stay centered whatever the brand text width; below `md` it is a flex row. The
  mobile menu is `absolute inset-x-0 top-full` under the sticky header, so it overlays the
  page instead of pushing it down. Keep the nav and menu `max-w-*` the same.
- The navbar Login button uses `{% url 'accounts:login' %}`. The About / Give links are in-page
  anchors (`#about`, `#give`) that exist only on the landing page; give them real URLs before
  adding other public pages. `{% url %}` on a missing route raises `NoReverseMatch`.
- Icons inside Alpine toggles (`x-show`) go in a wrapper `<span>`, since `includes/icon.html`
  only accepts `name` and `class`.
- Fonts are self-hosted from `static/fonts/`. `font-main` is Space Grotesk (body and UI, declared as `'Grotesk'`);
  `font-mono` is JetBrains Mono (account ids, amounts). Change a font by editing
  `--font-main` / `--font-mono` and the matching `@font-face` in `input.css`.
- Reusable component classes live in `input.css` under `@layer components`:
  `.btn`, `.btn-primary`, `.btn-secondary`, `.btn-ghost`, `.label`, `.input`,
  `.input-error`, `.field-error`, `.field-help`, `.card`. Use them before writing
  new one-off utility strings, and build new ones from tokens only.
- `--color-on-primary` and `--color-on-accent` are the text colors for filled
  primary and accent surfaces; use them instead of `text-white`.
- Light/dark: `base.html` sets `data-mode` and `.dark` on `<html>` from
  `localStorage` (falling back to the system setting) before first paint, and
  exposes `window.toggleMode()`. When `user_settings` is wired up, the server
  value `theme_mode` takes over. Dark values live in `static/css/themes.css`.
- Logos: `includes/logo.html` draws the SVG from `static/images/` as a CSS mask,
  so its color follows the theme tokens. Options: `variant` (`mark` | `full`),
  `plate`, `size`, `decorative`.
- `input.css` must contain `[x-cloak] { display: none !important; }`. Put
  `x-cloak` on anything Alpine shows or hides on load, to avoid a flash.
- Toasts and confirm dialogs are global. `static/js/ui.js` registers the Alpine store
  `ui` (`toasts`, `dialog`) and `includes/messages.html` renders both from `base.html`,
  so every layout has them. Do not build per-page modals or use `alert()` / `confirm()`.
  For a dialog that holds content (filters, a small form), use the reusable modal below, never hand-rolled markup.
  - Toast: the result of an action (saved, sent, failed). It does not block and
    dismisses itself. Types: `success`, `error`, `warning`, `info`. Django `messages`
    become toasts on page load (the template writes them into the hidden `#dj-messages`
    element and `ui.js` reads it), so views keep calling `messages.success(...)`. From
    Alpine or JS: `$store.ui.toast('Copied', 'success')`. Form validation errors stay
    inline next to the fields; use `error` toasts only for real failures.
  - Confirm dialog: before a destructive or hard-to-undo action (delete, archive, force
    delete, reopen attendance or tithes, end a buddy partnership). Declarative form:
    `data-confirm="..."` on the `<form>` or its submit button, with optional
    `data-confirm-title`, `data-confirm-text` and `data-confirm-danger`. From Alpine or
    JS: `await $store.ui.confirm({ title, message, confirmText, danger })`, which
    resolves to true or false. Confirm first, then show a toast for the result. Do not
    chain a confirm modal into an OK modal; a blocking acknowledge modal is only for
    show-once information such as a generated default password.
  - Reusable modal: `{% load ui_tags %}` then `{% modal "filters" title="Filter extensions" %}...{% endmodal %}`
    (`core/templatetags/ui_tags.py`, shell in `includes/modal.html`; width `sm`..`2xl`). The id must be a quoted literal.
    Open with `$store.ui.openModal('filters')`, close with `$store.ui.closeModal()`; Escape, the X and the backdrop also
    close it, focus moves in and back. The confirm dialog always sits above it. A tag is used because an included
    template cannot receive a block of content.
  - Destructive actions are POST forms. The dialog is a UX guard only; the server still
    enforces permissions and the frozen-record rules.
  - Style with tokens only: the danger button uses a danger token (add one to
    `input.css` if it is missing) and `--color-on-primary` / `--color-on-accent` for
    text. No `bg-red-*` or `text-white`.
- App shell (`base_app.html`): the header shows the logo (`brand_mark.html`, the one place its size is set) and
  the page title. From `sm` up an icon rail (its top button opens the panel) plus an expandable panel (Alpine store `sidebar`,
  `expanded`, `open()`, `close()`, `toggle()`); below `sm` a bottom bar or a drawer from `mobile_nav_style`
  (`user_settings.mobile_nav_style`, default `bottom`). The context processor
  `accounts.context_processors.navigation` supplies `nav` and `mobile_nav_style` and must be in `TEMPLATES`.
  To change a menu edit `NAV_ITEMS` in `accounts/navigation.py` (order = list order, `roles=`, `bottom=True`,
  `group=`, `exact=True` for home pages); `icon` must exist in `static/icons/sprite.svg`. Unresolvable url
  names render as disabled placeholders, never `NoReverseMatch`. `NAV_ITEMS` holds only strings (no imports
  from feature apps). Hiding a link is not access control. Nav markup uses utility classes only (state
  classes are chosen in `nav_link.html`; nothing is added to `input.css`). The closed panel and the rail/content
  behind an open panel are `inert`. Rail tooltips are `position: fixed` via Alpine so the scrolling rail never
  clips them. The page heading is the `page_title` block in `base_app.html` (it renders the h1; pages use h2
  and below). The fixed bottom bar is the one allowed fixed bar: `main` gets matching bottom padding in bottom
  mode. Light/dark and log out live in `sidebar_utilities.html` (Alpine store `mode`). The `?nav=` override in
  the context processor is DEBUG-only and temporary.
- Page templates extend `base_public.html` or `base_app.html` and fill the
  `content` block (and `title`, `page_title` where they exist). Never extend
  `base.html` directly from a page.
- Public page sizing: `<main>` already centers and pads the page, so a public page sets only
  its own width (`w-full max-w-*`) and adds no outer padding. If a page is not vertically
  centered, check the real `base_public.html` and the navbar height before adding offsets.
  The one exception is the landing page, which is full width (see Landing page).
- Footer (`includes/footer.html`): sits in normal flow (never `fixed`, which covers content on
  short screens), `border-t border-border bg-bg`, `text-txt-secondary` (not `text-txt-muted`,
  which fails contrast). The year comes from `{% now "Y" %}`. A `{% block %}` inside an
  included file overrides nothing; pass values with `{% include ... with church_name="..." %}`.
  The landing page also has its own long footer block (see Landing page); the shared footer still
  follows it and acts as the copyright line.
- App shell layers: the page (`bg-bg`) is the lowest layer. The icon rail and the header are `bg-shell` and float above it:
  a hairline (`border-shell-border`, level across the rail's menu row and the header) plus a soft shadow that falls onto the
  page. The sidebar panel, the mobile bottom bar and its More sheet use the same color and sit higher, with a bigger shadow and
  light glass (`supports-[backdrop-filter]:bg-shell/85 supports-[backdrop-filter]:backdrop-blur-xl` over a solid fallback).
  `--color-shell` and `--color-shell-border` are derived in `input.css` (`color-mix()` from `--color-surface` and
  `--color-primary`), so they follow light/dark mode (higher layers are lighter in dark mode) and custom themes. Shadows are a
  black `shadow-[...]` with low alpha. Cards, tables and forms stay flat with borders; no glass behind dense data.
- Visual direction: calm, minimal, human. Flat surfaces, borders instead of shadows, one
  accent color, real church context (greeting, first-time note, verse) instead of generic
  marketing copy. Glass is optional and subtle: keep a solid fallback and add
  `supports-[backdrop-filter]:bg-surface/70 supports-[backdrop-filter]:backdrop-blur-md`
  over at most one soft glow (`bg-accent/20 blur-3xl`). Opacity modifiers on tokens work
  in light and dark. Never use glass behind tables or dense data.
- Accessibility on public pages: body text 16px or larger, touch targets at least 44px,
  contrast AA in both modes, and no meaning carried by color alone.
- Login page pattern (`accounts/login.html`): two sides from `md` up (greeting, first-time
  note and verse on the left, form on the right), stacked below `md`; the verse is hidden
  below `md`. The greeting uses Alpine (`x-data` with the device clock) and falls back to
  "Welcome" without JavaScript. Show password is a checkbox (`x-data="{ show: false }"`,
  `x-bind:type`, `x-cloak` on the checkbox label) on the same row as "Forgot password?".
  Failed logins and a badly shaped account id share one generic error box (`role="alert"`)
  listing `non_field_errors` and `identifier.errors`. The account id input is `font-mono`
  with `autocomplete="username"` and `autocapitalize="none"`; the password is never echoed
  back. Inputs are enlarged with `px-4 py-3.5 text-base` on top of `.input`.
- Landing page: see the Landing page section. Do not invent church facts (service times,
  addresses, verses attributed to JIL).

## Landing page
- **Where:** `apps/pages/views.py` (`landing_view`, thin; the root URL must import that exact name),
  `apps/pages/services.py` (`get_landing_context()`, all the logic and every default),
  `apps/pages/templates/pages/landing.html`, tests in `apps/pages/tests/test_landing.py`. Served at `/`.
- **Every block has a default**, so the page is complete before anything is managed. Managed content wins:

  | Block | Managed source | Default |
  |---|---|---|
  | Hero intro | `ChurchContent` key `landing_description` | `DEFAULT_HERO` (title is always the church name) |
  | Mission / vision / core values | `ChurchContent` keys `mission`, `vision`, `core_values` | `DEFAULT_ABOUT` |
  | Photos (hero carousel and "Come as you are" gallery) | `LandingImage` (`is_active`, `sort_order`) | `DEFAULT_IMAGES` |
  | Current month theme | `resources.MonthlyTheme` for this Manila month, `is_published`, not archived | `DEFAULT_THEME` (labels its own month, so it is never mislabeled) |
  | Latest video | newest `JilVideo` | `DEFAULT_VIDEO_ID` |
  | Support the ministry | active `DonationAccount` rows | none: a "not published yet" note, nothing invented |
  | Long footer | none yet | `FOOTER` (move to `site_setting` when it is managed) |
  | "What's inside", Buddy Buddy, welcome copy | none | `MEMBER_FEATURES`, `SERVING_FEATURES`, `BUDDY_POINTS`, `CHURCH_INTRO`, `CHURCH_POINTS` |

- **Default texts come from the church's own landing draft** (mission, vision, values, theme, addresses,
  phones, links, photos). Confirm they are official. Change defaults only in `services.py`, never in the
  template. The welcome copy (`CHURCH_INTRO`, `CHURCH_POINTS`) must stay free of claims about size,
  history, service times or locations.
- **Content lookup:** `ChurchContent` is unique per `(key, language)`. The page tries the visitor's language,
  then English, then Filipino, and skips rows with an empty body. Loaders import models lazily inside
  functions. If a model field is renamed, edit only the loader in `services.py`.
- **Colors use tokens only, and `input.css` / `themes.css` are not edited for this page** (other themes
  exist and would not define new tokens). The navy "deep" sections are `bg-txt-primary text-surface`:
  `txt-primary` is dark in light mode and light in dark mode, so they flip by themselves in every theme.
  Brand gold is the one exception: `--lp-gold`, `--lp-gold-soft` and `--lp-on-gold` are defined once on the
  page wrapper and used for fills, lines and button backgrounds only, never for text (it is unreadable on
  the flipped light sections). Use `bg-[var(--lp-gold)]` and `text-[color:var(--lp-on-gold)]`.
- **Full width:** the wrapper uses `-mx-4 -my-10` to cancel the `px-4 py-10` that `base_public.html` puts on
  `main`. If that padding changes, change both numbers (or add a `main_padding` block to the base).
- **In-page anchors:** `#about`, `#church`, `#theme`, `#buddy`, `#whats-inside`, `#watch`, `#prayer`, `#give`,
  `#contact`. The public navbar links to `#about` and `#give`; keep both.
- **Prayer request is a Log in button, not a form.** A prayer request needs an account (the coordinator
  receives it), so the landing page never collects one.
- **External requests:** the video poster loads from `i.ytimg.com` and the default photos from
  `imagedelivery.net` when the page loads; the player (`youtube-nocookie.com`) loads only after a click.
- **Footer:** the long footer is part of this page. `base_public.html`'s footer still renders below it as the
  copyright line. The Privacy Policy link is a `#` placeholder until that page exists.
- **Before changing the page:** run `docker compose exec web python manage.py test apps.pages.tests.test_landing`
  and `make tailwind-build` (the template uses new utility classes).

## Known footguns
- `migrate` from the host cannot reach `POSTGRES_HOST=db`; run it in the `web`
  container. `makemigrations` works from the host.
- Running `migrate` before `AUTH_USER_MODEL` is set causes
  `InconsistentMigrationHistory`; fix by `docker compose down -v` (dev only).
- Core migration `0002_seed_and_triggers` depends on the first migration of
  several apps; if an app's tables move to a later migration, update its
  `dependencies`.
- pgAdmin shows FKs as `NO ACTION`; that is expected (see above).
- `TemplateDoesNotExist` for `base_public.html` means `TEMPLATES['DIRS']` is
  missing `BASE_DIR / 'templates'`. Static files need
  `STATICFILES_DIRS = [BASE_DIR / 'static']`. After adding folders, restart the
  `web` container (or rebuild) so they are mounted.
- `{% extends %}` needs the full file name: `"base_public.html"`, not
  `"base_public"`.
- Django 5+ logout is POST-only; use a form with `{% csrf_token %}`, not a link.
- Font file names with spaces or commas need URL-encoding in `@font-face`;
  prefer renaming them to plain names.
- Rebuild Tailwind (`make tailwind-build`) after adding classes in templates,
  or run `make tailwind-watch`.
- pgAdmin access needs the dev-only port from
  `docker-compose.override.yml.example`; never publish the db port in production.
- `static/js/ui.js` must be loaded with `defer` before `alpine.min.js` in `base.html`.
  The `alpine:init` listener has to exist before Alpine starts, otherwise `$store.ui`
  is undefined and toasts and confirm dialogs silently do nothing. Keep
  `{% include 'includes/messages.html' %}` in `base.html` after `{% block body %}`.
- A form with `data-confirm` submits normally if `ui.js` fails to load, so the server
  must never rely on the dialog for safety.
- Do not use multi-line `{# ... #}` template comments; they render as text on the page. In JS
  and CSS use `/* ... */` or `//`.
- Each template starts with a header comment (HTML `<!-- ... -->` before `{% extends %}`, no
  template tags inside) listing the blocks it fills, the context variables it expects and,
  for includes, a usage example.
- An `{% include %}` placed outside a block in a template that extends another is silently
  dropped.
- A `{% block %}` inside a file that is only `{% include %}`d never overrides anything.
- A `position: fixed` footer or bar covers page content on short screens; keep the footer in
  normal flow.
- In `input.css` the Space Grotesk `@font-face` says `format('woff2')` but points at a `.ttf`
  file; use `format('truetype')` to match the file.

## Working rules for agents
- Use Docker commands; run `check` and `core.test_schema` after any model change.
- Never edit an applied migration; add a new one.
- Do not commit `.env`, `venv/`, `theme/tailwindcss`, media uploads or db dumps.
- Keep `.env.example` in sync with settings.
- **Document the code so it is easy to read.** Every new module, class and
  function gets a short docstring saying what it is for. Add a comment for the
  why when the reason is not obvious (a constraint, a trigger, a workaround).
  Each template starts with a header comment (see Template comments) listing the blocks it fills,
  the context variables it expects and, for includes, a usage example. When
  you change structure, commands or behaviour, update `README.md` and this file
  in the same change.
- Prefer the smallest relevant command or page load to verify a change.

## Roles and routing
- Four roles: super-admin, admin, coordinator, member. Role is stored in
  `User.role`; do not infer it from `is_staff` or `is_superuser`.
- `is_staff` / `is_superuser` are reserved for the super-admin, since they
  control access to Django's built-in admin.
- Django's built-in admin is mounted at `/django-admin/`. `/admin/` is the
  custom Admin role area, not Django admin. Never "fix" this by moving
  Django admin back to `/admin/`.
- Route groups: `/superadmin/`, `/admin/`, `/coordinator/`, and member
  pages at the root. Each group has its own urls module.
- Every view in a role group must be protected with `role_required(...)`.
  Hiding a link in a template is not access control.
- Coordinator-scoped data must be fetched through the `for_user(user)`
  queryset helper, not with ad hoc `.filter(extension=...)` calls in views.
- Use `{% url 'admin:index' %}` to link to Django admin, never a hardcoded
  path.

## Super-admin management
- **Where:** `accounts/views/superadmin.py` + `accounts/urls/superadmin.py` (namespace `superadmin`, mounted at
  `/superadmin/`; the super-admin home stays in `dashboards`). Templates: `accounts/templates/accounts/superadmin/`.
  Business rules: `accounts/services/__init__.py` (extension section). Who may do what: `accounts/permissions.py`.
  Shared archive / restore / force-delete helpers and `ServiceError`: `core/services.py`.
- **Two guards, both required:** `@role_required(Role.SUPER_ADMIN)` on the view guards the page; every service starts
  with a check from `accounts/permissions.py` and guards the action. To open a screen to another role, change the
  permission function and add routes, not the services.
- **Role home URLs live in ONE place:** `accounts/decorators.py` (`ROLE_HOME_NAMES`, `home_url_for`, `role_required`).
  `accounts/views/__init__.py` imports them; never copy them back.
- **Extension invariants (the database cannot enforce them, the services do):** the coordinator has `role=coordinator`
  and belongs to that extension; one coordinator per extension; replacing a coordinator demotes the old one to member;
  an extension cannot be archived while non-archived people belong to it; only archived extensions can be deleted.
- **Extension text is stored lowercase, shown in title case:** `core/text.py` (`clean_text` on input, `title_case` on output;
  templates use `{% load ui_tags %}` and `|title_case`, messages call `title_case(...)`). Old rows may be mixed case and are NOT
  migrated, so every comparison ignores case (`name__iexact`, `clean_text`) and `update_extension` treats a case-only difference
  as no change. Acronyms and roman numerals shown in capitals are listed in `core/text.py`.
- **Extension required fields and address rules live in the service too:** `clean_extension_data` (`accounts/services/__init__.py`)
  requires everything except `building_number` and, for the Philippines, checks province > municipality > barangay and a
  4-digit postal code against `static/data/ph_address.json` through `accounts/address.py`. The form uses the same function.
  The JSON ({province: {municipality: [barangay]}}, from PSGC, NCR districts merged into Metro Manila) is also what the
  browser loads for the cascading dropdowns, so the two can never disagree. Other countries are free text.
- **Extension bulk actions:** `bulk_archive_extensions` / `bulk_delete_extensions` (max 100 rows, `BulkResult(done, skipped)`)
  run each extension in its own transaction and skip the ones that do not qualify; the view turns the result into a success
  and a warning toast. They are naturally idempotent (an archived or deleted row is skipped on a repeat), so they do not use
  a one-time submission token.
- **Lock order in services:** extension row first, then people in ascending id order. Never `select_related` a nullable
  FK together with `select_for_update` (Postgres refuses it).
- **Archive writes:** always `save(update_fields=[...])` with `archived_at` in the list (via `core.services`), or the purge
  trigger does not fire and a stale `purge_at` can be written back.
- **Audit keys:** `log_action` drops any `before`/`after` key containing "password", "token" or "secret", so a key such as
  `must_change_password` silently disappears. Log it under another name (for example `needs_activation`).
- **Double clicks:** `static/js/ui.js` locks a POST form on its first real submit (opt out with `data-no-lock`). The server
  must stay safe without it: actions check the current state under a row lock and report "already done" instead of failing.
- **UI pieces:** `.btn-danger` (destructive buttons), `includes/pagination.html` (Page + `querystring`), and
  `$store.ui.showSecrets({title, message, headers, rows})` for show-once data such as generated passwords (or hand it
  over with `json_script:"secrets-data"`). The secrets dialog only closes with its own button. Also
  `includes/breadcrumb.html` (parent_label, parent_url, current) and the reusable modal above.
- **App text size:** `base_app.html` marks its root with `data-app-shell`, and `input.css` raises the root font size to
  106.25% for pages that have it. Public and login pages keep the default.

## Super-admin user management
- **Where:** `accounts/services/users.py` (rules), `accounts/views/users.py` (screens), forms in `accounts/forms.py`,
  templates `accounts/templates/accounts/superadmin/user_*.html`, routes in `accounts/urls/superadmin.py`
  (`/superadmin/users/...`). Permission helpers (`assignable_roles`, `can_manage_users`) are in `accounts/permissions.py`.
- **One-time form tokens (`submission_token` table, 43rd table):** a form that must not run twice (create a person,
  reset a password, any bulk action) carries `new_submission_token()` in a hidden field; the service calls
  `consume_submission_token()` INSIDE its `transaction.atomic()`. The token is the primary key, so two simultaneous
  identical requests cannot both pass; a failed action rolls its token back so a retry works. Single actions that are
  naturally idempotent (archive, suspend, ...) check the current state under a row lock instead and need no token.
- **Bulk actions:** `run_bulk()`, max 100 people, each person in its own transaction in ascending id order. Anyone the
  action does not apply to (yourself, wrong status, gone) is skipped with a reason; it never fails the rest. One audit
  row per person, plus one `account.bulk_archive` summary row when two or more are archived. Bulk reset shows all new
  passwords in one show-once dialog.
- **Guards:** you cannot archive, suspend, deactivate, reset, delete or change the role of yourself; the last active
  super-admin cannot be archived, suspended, deactivated or demoted (checked under a lock on all super-admin rows).
- **Coordinators:** archiving a coordinator frees the seat and makes them a member; a person leaving or entering a
  seat updates `Extension.coordinator` in the same transaction. Putting a coordinator into a seat that is taken is
  refused UNLESS the form sent `replace_coordinator` (the confirmation dialog in `user_form.html`): then the old
  coordinator stays in that extension as a member (`_free_taken_seat`, audit rows `account.role_change` and
  `extension.coordinator_change`). The user form's extension is a text box with a datalist (`UserForm.extension_data`)
  plus a hidden id field, so the service still receives an Extension object.
- **People inside one extension (detail page):** only transfer IN. There is no remove, archive or transfer-out on this
  page (people leave an extension by being transferred into another one; archiving a person is done from the people
  screens). The "Transfer people in" button opens a right-hand panel (search + tick list of everyone from every extension,
  capped at `ADD_OPTION_LIMIT`), and Transfer asks for confirmation (`$store.ui.confirm`) first. `move_person` (someone
  from ANOTHER extension needs `confirmed=True`; a moving coordinator frees the old seat and arrives as a member;
  special roles are cleared) and `transfer_people_in` (max 100, one transaction per person, one-time token, skipped
  rows explained). Only members and coordinators belong to an extension; admins and super-admins never do.
  Route: `/superadmin/extensions/<pk>/people/add/`.
- **Passwords:** shown once, never stored, logged or put in the session. Order used: typed (min 8 characters), else the
  else the acting person's own default for the role, else a generated 12-character password, else a generated 12-character password. A reset forces a change at next
  login and ends the person's sessions (the hash changes). "Skip activation" on create is for test accounts that should not
  go through the activation page.
- **Pages that can show a password** (`user_create`, `user_reset_password`, `user_bulk`) are `never_cache` and render the
  dialog in the POST response (`json_script:"secrets-data"`), not a redirect.
- Names are stored lowercase (core.text.clean_text), shown with name_case/person_name, and checked for duplicates (same first, middle and last name, unless both have different birth dates). The super-admin role cannot be handed out from a screen.
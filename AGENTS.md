# AGENTS.md

## Project overview
Church-Management is a Django + PostgreSQL system for Jesus Is Lord Church
(JIL) and its local branches ("extensions"). Scope: accounts and hierarchy,
attendance, tithes and offering, faith goals and Bible streaks, accountability
buddies, prayer requests, events, resources, notifications and audit logging.

**Status:** all 14 apps and 42 models exist, with migrations and seed data.
`core.test_schema` verifies them. The base templates, design tokens, light/dark
toggle, the public landing page (static copy for now) and the redesigned login page exist. Services, views and forms are
mostly not built yet. The login is built (`accounts/backends.py`, `services.py`,
`forms.py`, `views.py`, `urls.py`; routes `/login/`, `/logout/`, `/activate/`);
`/activate/` is a placeholder until the activation flow exists. Forgot-password and reset
are not built yet; the "Forgot password?" link on the login page is a placeholder
(`href="#"`). The Log in buttons on the landing page hardcode `/login/`; switch them to
`{% url 'accounts:login' %}`. Next: the real activation flow, forgot/reset password,
`role_required`, then features on top of the models.

Reference docs (the source of truth for behaviour):
- `docs/schema.sql`: original SQL design, with comments on every rule
- `docs/flows.md`: the feature flows (login, attendance, tithes, buddy, ...)
- `docs/verify.sql`: read-only checks to run in pgAdmin

## Tech stack
Python 3.12, Django 5.1+ (tested on 5.2 and 6.0), PostgreSQL 16, Tailwind CSS
(standalone CLI), Alpine.js, Docker Compose.

## Repository layout
- `church_management/`: project package (settings, urls, asgi/wsgi). Config only.
- One folder per app (below). Standard files per app: `models.py`,
  `services.py` (business logic), `views.py`, `urls.py`, `forms.py`,
  `permissions.py`, `management/commands/` (jobs).
- `templates/` (project-level: `base.html`, `base_public.html`, `base_app.html`,
  `includes/` with `public_navbar.html`, `footer.html`, `logo.html`, `icon.html`).
  Page templates that belong to one app go in that app's `templates/<app>/` folder
  (e.g. `pages/templates/pages/landing.html`, `accounts/templates/accounts/login.html`).
- `static/`: `css/input.css` (Tailwind source and design tokens), `css/themes.css`
  (dark overrides), `css/output.css` (generated, not committed), `fonts/`,
  `icons/sprite.svg`, `images/` (logos), `js/alpine.min.js`.
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
docker compose exec web python manage.py test core.test_schema -v 2
make tailwind-watch    # separate terminal
make tailwind-build
```
App at http://127.0.0.1:8001/ (use `127.0.0.1`, not `localhost`).
`.env` is read with python-decouple; keys are in `.env.example`.

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
| `pages` | `church_content`, `landing_image`, `donation_account`, `jil_video` | Landing page and terms editing, donation page, `fetch_jil_videos` |
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
  `views.ROLE_HOME_URLS` (all `/` until the role dashboards exist). Brute-force
  throttling is not built yet.
- **Status:** `not_activated -> active -> archived`. "Inactive for a year" is an
  automatic archive with `archive_reason='inactive'`.
- **Default passwords:** hash only. Most specific wins: extension default, else
  global; a typed custom password always overrides.
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
  authenticated app. Add nav entries to `templates/includes/`, not inline.
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
- The Login button and the About / Give links in the public navbar are temporary
  (`href="#"`). Replace them with `{% url %}` once the views exist; `{% url %}` on a missing
  route raises `NoReverseMatch`.
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
- Page templates extend `base_public.html` or `base_app.html` and fill the
  `content` block (and `title`, `page_title` where they exist). Never extend
  `base.html` directly from a page.
- Public page sizing: `<main>` already centers and pads the page, so a public page sets only
  its own width (`w-full max-w-*`) and adds no outer padding. If a page is not vertically
  centered, check the real `base_public.html` and the navbar height before adding offsets.
- Footer (`includes/footer.html`): sits in normal flow (never `fixed`, which covers content on
  short screens), `border-t border-border bg-bg`, `text-txt-secondary` (not `text-txt-muted`,
  which fails contrast). The year comes from `{% now "Y" %}`. A `{% block %}` inside an
  included file overrides nothing; pass values with `{% include ... with church_name="..." %}`.
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
- Landing page copy is static in the template until the `pages` models feed it. Do not
  invent church facts (service times, addresses, verses attributed to JIL).

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
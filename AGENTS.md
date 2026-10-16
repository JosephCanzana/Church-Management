# AGENTS.md

## Project overview
Church-Management is a Django + PostgreSQL system for Jesus Is Lord Church
(JIL) and its local branches ("extensions"). Scope: accounts and hierarchy,
attendance, tithes and offering, faith goals and Bible streaks, accountability
buddies, prayer requests, events, resources, notifications and audit logging.

**Status:** all 14 apps and 46 models exist, with migrations and seed data.
`check` and `makemigrations --check --dry-run` verify them (there is no `core.test_schema` in this repo).

- **Built:**
  - Base templates, design tokens, light/dark toggle, the public landing page (a default for every block until
    `pages` content is managed) and the redesigned login page.
  - Accounts: login (`/accounts/login/`, `/accounts/logout/`), activation (`/accounts/activate/`), profile
    (`/accounts/profile/`), `role_required` (`accounts/decorators.py`).
  - Super-admin extension management (`/superadmin/extensions/`).
  - People management for three roles, one set of screens with role-based rules: super-admin (`/superadmin/users/`),
    admin (`/admin/users/`) and coordinator (`/coordinator/users/`); optional contact details (suffix, phone, address);
    special member roles with per-extension limits (`/admin/special-roles/`, `/superadmin/special-roles/`); default
    passwords for the super-admin and admins (see People screens for admin and coordinator).
  - A global toast and confirm dialog (`static/js/ui.js`, rendered by `includes/messages.html`).
  - Bible: reader with three versions (KJV and Ang Dating Biblia from the database, ESV from api.esv.org), version
    picker with locking, chapter reads (Next counts a chapter as read), a per-account bookmark, ESV call limits, locks and a size-capped cache, and the super-admin
    switches for every version (`/superadmin/site-settings/`, see Bible), and the verse of the day.
  - Dashboards: one page per role (super-admin, admin, coordinator, member) with the verse of the day, continue
    reading, quick links and, for leaders, counts and charts (see Dashboards).
  - Attendance (bricks 1 to 14): gender and age networks, model changes, permissions and scoping, draft services,
    summary, upload, reopen, the sheet screens for every role, A4 print page, first timer conversion, repeat-visitor
    auto-transfer, reset-to-activation, 58 mm welcome slip and personal history (see Attendance).
- **Not built yet:**
  - Brute-force login throttling.
  - The `faith` listener that turns a chapter read into a streak, MBBTAG. The dashboards show "Coming soon"
    cards for the Bible streak, goals and events until those apps have views.
  - The tithes screens.
  - Beyond the list above, services, views and forms are mostly not built yet: features are being added on top of
    the models, one app at a time.

Reference docs (the source of truth for behaviour):
- `docs/schema.sql`: original SQL design, with comments on every rule
- `docs/flows.md`: the feature flows (login, attendance, tithes, buddy, ...)
- `docs/verify.sql`: read-only checks to run in pgAdmin

## Tech stack
Python 3.12, Django 5.1+ (tested on 5.2 and 6.0), PostgreSQL 16, Tailwind CSS
(standalone CLI), Alpine.js, Docker Compose. Outgoing HTTP (the ESV API) uses the
standard library, so there is no extra Python package for it.

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
  (e.g. `apps/pages/templates/pages/landing.html`, `apps/accounts/templates/accounts/login.html`,
  `apps/bible/templates/bible/chapter.html`).
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
docker compose exec web python manage.py test apps.bible
docker compose exec web python manage.py import_bible KJV --url https://raw.githubusercontent.com/seven1m/open-bibles/master/eng-kjv.osis.xml
docker compose exec web python manage.py import_bible ADB --url https://raw.githubusercontent.com/seven1m/open-bibles/master/tgl-tagalog.osis.xml
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
- Plain `date` columns (check-ins, streaks, daily verse, attendance, chapter reads) use the
  Manila calendar date.
- `ESV_API_KEY` (settings, from `.env`; empty means "ESV not set up"). The `web` service in
  `docker-compose.yml` must receive it (`env_file: .env` or an `environment:` entry) and
  `.env.example` must list it. Keep it secret: Crossway forbids sharing or publishing it.

## Apps, tables, and where logic belongs

| App | Tables | Logic that lives here |
|---|---|---|
| `core` | `site_setting`, `retention_policy`, `submission_token` | Abstract bases (`TimestampedModel`, `ArchivableModel`), `archived_matches_status()`, generic archive / restore / force-delete, upload validation and storage helpers (`uploads.py`), nightly `purge_archived`, permission hierarchy helpers, retention screen. `site_setting` also holds the Bible switches and ESV state (keys `bible_api_enabled`, `bible_esv_enabled`, `bible_esv_state`, `bible_esv_cache_lock`) |
| `audit` | `audit_log` | `log_action()` in `audit/services.py` (strips sensitive keys, sends `action_logged` from `audit/signals.py`), log viewer, `cleanup_audit_log` |
| `accounts` | `extension`, `app_user`, `special_role`, `user_special_role`, `extension_special_role_limit`, `default_password`, `email_token`, `user_extension_history` | Login/logout/activation, forgot + reset password, email verification, profile, create/manage accounts, extensions, special roles and limits, default passwords, `archive_inactive_users`, `auto_transfer_extension`, `cleanup_email_tokens`, `mark_attended()` |
| `theming` | `theme`, `theme_palette`, `user_settings` | Theme CRUD (both palettes in one transaction), contrast warning (including `highlight` / `on_highlight`), CSS generation + cache, context processor; `UserSettings` row created by `signals.py` |
| `pages` | `church_content`, `landing_image`, `donation_account`, `jil_video` | Landing page (`get_landing_context()` in `pages/services.py`, a default for every block) and terms editing, donation page, `fetch_jil_videos` |
| `bible` | `bible_verse`, `bible_esv_cache`, `daily_verse`, `bible_chapter_read`, `bible_bookmark` | Reader (KJV, Ang Dating Biblia, ESV), `import_bible`, ESV client with limits, locks and cache, version picker, daily verse (`daily_verse.py`, `seed_daily_verses`), `record_chapter_read()`, `finish_chapter()`, bookmark |
| `faith` | `goal`, `goal_checkin`, `user_daily_activity`, `user_streak` | Goals, check-ins, consistency, heatmap, streak rules, `complete_ended_goals` |
| `events` | `event` | Event CRUD, change/cancel notices, `send_event_reminders` |
| `resources` | `monthly_theme`, `monthly_resource`, `weekly_powerpoint`, `general_resource` | Resource permissions (`permissions.py`) and transactional create/edit/publish/archive/restore/delete services (`services.py`); monthly themes + files, weekly PPT, general resources |
| `attendance` | `attendance`, `attendance_member`, `attendance_first_timer` | Sheets, draft services (ticks, first timers, search), summary, upload (freeze counts, update `last_attended_at`), reopen (admin) |
| `tithes` | `tithes_offering`, `tithe_entry` | Entries, live totals, denomination count, finalize, reopen; members see only their own entries |
| `buddy` | `user_buddy`, `buddy_request`, `buddy_note` | Request/accept (transactional one-partner check), notes/nudges, end partnership, view partner goals |
| `prayer` | `prayer_request`, `prayer_reply` | Send, inbox (coordinator scope, anonymous masking), reply, done, delete |
| `notifications` | `notification`, `notification_preference`, `notification_rule` | `notify()`, bell + list, preferences, the admin-alert receiver (`notifications/listeners.py`, applies `notification_rule` to `action_logged`), `cleanup_notifications` |

Before adding a model, put it in the app whose data it shares a lifecycle
with. Only create a new app if the answer is "none".

### Resources
- Management is admin and super-admin only. Every mutating function in
  `apps/resources/services.py` calls `require_resource_manager`, runs inside
  `transaction.atomic()`, and logs the change through `audit.log_action()`.
- A monthly theme is dated on the first of a month and unique for that month.
  Only active, published themes and their files are visible to coordinators
  and members; managers can inspect drafts and archived records. Coordinators
  can also view active weekly presentations and general resources; members
  cannot.
- Monthly files are `MonthlyResource` rows. Weekly presentations and general
  resources each have one required file. Audit entries contain record ids and
  changed field names, never file names or contents.
- The management screens are under `/resources/manage/` in the `resources`
  namespace. Admin and super-admin share the screens; only the super-admin can
  permanently delete archived records. Mutations are POST-only, use the global
  confirmation dialog, and report results through Django messages/toasts.
- Templates live in `apps/resources/templates/resources/manage/`; list screens
  filter by title/status and paginate. The library route remains separate and
  is at `/resources/`: coordinators see published monthly themes, active weekly
  presentations and active general resources; members see published monthly
  themes only. Every document download uses a permission-checked view and the
  original filename; resource storage has no public URL.
- Run `apps.resources.tests.test_permissions`,
  `apps.resources.tests.test_services`, `apps.resources.tests.test_views`, and
  `apps.resources.tests.test_downloads` when changing these rules or screens.

### Dependency rules
1. Direction: `core` <- `audit` <- `accounts` <- everything else. `theming`
   depends on `accounts`; `tithes` depends on `attendance`; `attendance` depends
   on `events`; `bible` uses `core` (`site_setting`) and `accounts` (`role_required`, roles).
2. `audit` and `notifications` never import feature apps. They refer to other
   records with `entity_type` / `entity_id`, not foreign keys.
3. Cross-app reactions use signals, not imports upward:
   `audit.log_action()` sends `action_logged` and `notifications` listens to
   apply `notification_rule`; the daily open streak hooks Django's
   `user_logged_in` signal from `faith`; `bible` sends `bible.signals.chapter_read`
   and `faith` listens to it for the Bible streak (`bible` never imports `faith`).
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
- Uploads use `core.uploads`: photos accept JPG, PNG and WebP (10 MB by default);
  resources accept PDF, PPTX, DOCX and XLSX (25 MB by default). The limits are
  configured by `PHOTO_UPLOAD_MAX_MB` and `RESOURCE_UPLOAD_MAX_MB`.
- Public photos live under `MEDIA_ROOT` and are served by Caddy at `/media/`.
  Resource documents use `PRIVATE_MEDIA_ROOT`, outside that public tree, and
  their storage deliberately has no `.url`; future downloads must pass through
  permission-checked Django views. Keep the private volume off the Caddy mount.
- Upload models retain an original basename for display/download while storage
  paths use sanitized names. `FileCleanupModel` removes replaced/deleted files
  after commit; generic force-delete and `purge_archived` remove cascade files
  before deleting rows and tolerate missing files.
- A table added in Django first (`bible_verse`, `bible_esv_cache`) must also be added to `docs/schema.sql`,
  A column added to an existing table (for example the contact
  fields on `app_user`) is added there too.

## Roles and routing
- Four roles: super-admin, admin, coordinator, member. Role is stored in
  `User.role`; do not infer it from `is_staff` or `is_superuser`.
- `is_staff` / `is_superuser` are reserved for the super-admin, since they
  control access to Django's built-in admin.
- Django's built-in admin is mounted at `/django-admin/`. `/admin/` is the
  custom Admin role area, not Django admin. Never "fix" this by moving
  Django admin back to `/admin/`.
- Route groups: `/superadmin/`, `/admin/`, `/coordinator/`, and member
  pages at the root (the Bible is at `/bible/`, open to all four roles). Each group has its own urls module and URL
  namespace: `superadmin`, `admin_area` and `coordinator_area`. Never name one `admin`: that is Django's own admin.
- Every view in a role group must be protected with `role_required(...)`.
  Hiding a link in a template is not access control.
- Coordinator-scoped data must be fetched through the `for_user(user)`
  queryset helper, not with ad hoc `.filter(extension=...)` calls in views. For people this is
  `User.objects.for_user(actor)` (see People screens for admin and coordinator); for attendance it is
  `Attendance.objects.for_user(actor)` (see Attendance); tithes get their own when their screens are built.
- Use `{% url 'admin:index' %}` to link to Django admin, never a hardcoded
  path.

## Behaviour rules agents must not break
- **Account id:** `<counter from account_number_seq><year, Manila>`, generated
  in `User.save()`. Never reused, never edited. `bulk_create()` skips it, so
  create users one by one.
- **Special-role limits** are enforced in a transaction with the extension row
  locked. An extension with no limit row for a role cannot give that role to anyone until an admin sets one.
- **Uploaded attendance/tithes** are locked; only an admin reopens them
  (logged and alerts admins). Force delete is super admin only, archived rows
  only, logged before deleting; delete files from disk first.
- **Privacy:** never write passwords, tokens, goal text, notes, prayer text or
  tithe amounts to `audit_log` or `notification.data`. Notifications say that
  something happened; the linked page does the permission check. Anonymous
  prayer requests hide the sender in UI/API but keep `user_id`.
- **Enumeration:** forgot-password always shows the same message.
- **Licensed Bible text (ESV):** never store it anywhere except `bible_esv_cache` (at most 450 verses in total).
  Not in `bible_verse` (a database constraint refuses it), not in `daily_verse.verse_text`, not in logs, not in
  `audit_log`. Every page that shows ESV text carries the copyright notice, "(ESV)" and a link to www.esv.org.
- **Tests never call outside services.** Mock `apps.bible.esv.esv_client.fetch_chapter`; tests that depend on ESV
  being locked or open set `ESV_API_KEY` with `override_settings`, so a real key in `.env` never changes them.

## Accounts: login, status, activation, profile
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
  super-admin or an admin; never purged; `User.is_active` is false, so open sessions end at once). "Inactive for a year" is an
  automatic archive with `archive_reason='inactive'`. Restore and unsuspend go back to `active` when
  `must_change_password` is false, otherwise to `not_activated`. Deactivate = back to `not_activated` with
  `must_change_password=True`.
- **Last attended:** `app_user.last_attended_at` (what the inactivity job reads) is written only through
  `services/presence.mark_attended(user_ids, on_date)`: one UPDATE that moves people with no date, or an earlier one, to
  `on_date` and never moves anyone back. The attendance upload calls it (`accounts` never imports `attendance`, so it takes
  plain ids and a date). It does not touch `updated_at`: attending is activity, not an edit. A reopen does not roll it back.
  `services/presence.remember_age_network(user_id, age_network)` stores `app_user.age_network` for a person WITHOUT a birth
  date (the birth date always wins, so anyone with one is never touched); attendance calls it when the taker picks or changes a
  category. Same rules: no `updated_at`, nothing in `audit_log`.
- **Default passwords:** kept by the super-admin (the global ones) and by each admin, one per role beneath them
  (`DefaultPassword.owner` + `applies_to_role`); a coordinator keeps none. Stored as a hash plus an encrypted copy so the
  owner can see it again. Which one a person gets depends on who creates or resets them; the rules are under People
  screens for admin and coordinator.
- **Activation:** `accounts/services/activation.py` (`user_needs_activation`, `activate_account`), form `ActivationForm`,
  view `activate_view`, template `accounts/activate.html`. A person must activate when `status=not_activated` OR
  `must_change_password=True` (a reset leaves the status `active`). `activate_account` locks the row, sets the password,
  clears `must_change_password`, turns `not_activated` into `active`, re-signs the session
  (`update_session_auth_hash`) and logs `account.activated` (flag logged as `needs_activation`). It is idempotent, and
  suspended or archived accounts are left alone. `ActivationRequiredMiddleware` (`accounts/middleware.py`, after
  `MessageMiddleware`) redirects everything except `accounts:activate` and `accounts:logout` while activation is pending.
  Tests that log a user in must create them activated (`status="active"`, `must_change_password=False`), or the
  middleware redirects every request.
- **Profile:** `/accounts/profile/` (every role; `login_required`, `never_cache`). Services in `accounts/services/profile.py`,
  views in `accounts/views/profile.py`, forms `ProfileDetailsForm` / `ProfileEmailForm` / `ChangePasswordForm`. Users edit only
  their birth date and photo. `User.email` is verified only; an unverified address is `pending_email`. A verified address is
  unique; a pending one is not: when someone verifies it first the others lose it and `accounts.signals.email_released` fires
  (`notifications/listeners.py` creates the bell item). Links live `VERIFY_LINK_MINUTES` (60), resend gap
  `VERIFY_RESEND_SECONDS` (60), max `VERIFY_MAX_PER_HOUR` (10); tokens are stored as SHA-256 hashes. The emailed link
  (`/accounts/verify-email/<token>/`) needs login; GET only shows a page, POST verifies. Mail uses `MAILERS` (Gmail App
  Password via `.env`; console backend when unset). Never name settings `EMAIL_HOST_USER` etc. with MAILERS in use.
  Photos are re-encoded to a 512 px square JPEG; old files are deleted after commit.

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
  requires everything except `street` and `building_number` and, for the Philippines, checks province > municipality > barangay and a
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
  `includes/breadcrumb.html` (parent_label, parent_url, current) and the reusable modal (see Frontend conventions).
- **App text size:** `base_app.html` marks its root with `data-app-shell`, and `input.css` raises the root font size to
  106.25% for pages that have it. Public and login pages keep the default.

### User management
- **Where:** `accounts/services/users.py` (rules), `accounts/views/users.py` (screens, shared by three roles), forms in
  `accounts/forms.py`, templates `accounts/templates/accounts/people/user_*.html`, routes in `accounts/urls/superadmin.py`
  (`/superadmin/users/...`), `admin_area.py` (`/admin/users/...`) and `coordinator_area.py` (`/coordinator/users/...`).
  Permission helpers (`can_act_on`, `available_actions`, `assignable_roles`, ...) are in `accounts/permissions.py`.
- **One-time form tokens (`submission_token` table):** a form that must not run twice (create a person,
  reset a password, any bulk action) carries `new_submission_token()` in a hidden field; the service calls
  `consume_submission_token()` INSIDE its `transaction.atomic()`. The token is the primary key, so two simultaneous
  identical requests cannot both pass; a failed action rolls its token back so a retry works. Single actions that are
  naturally idempotent (archive, suspend, ...) check the current state under a row lock instead and need no token.
- **Bulk actions:** `run_bulk()`, max 100 people, each person in its own transaction in ascending id order. Anyone the
  action does not apply to (yourself, wrong status, gone) is skipped with a reason; it never fails the rest. One audit
  row per person, plus one `account.bulk_archive` summary row when two or more are archived. Bulk reset shows all new
  passwords in one show-once dialog. Which bulk actions a role has comes from `allowed_bulk_actions(actor)`; people are
  looked up through `for_user`, so anyone outside the actor's reach is skipped as "no longer exists".
- **Guards:** you cannot archive, suspend, deactivate, reset, delete or change the role of yourself; the last active
  super-admin cannot be archived, suspended, deactivated or demoted (checked under a lock on all super-admin rows). Who may
  act on whom is checked on the locked person row with `require_can_act` (see People screens for admin and coordinator).
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
- **Passwords:** shown once, never stored, logged or put in the session. Order used: typed (min 8 characters; only the
  super-admin and admin may type one), else the default that applies to the acting person (see People screens for admin and
  coordinator), else a generated 12-character password. A reset forces a change at next login and ends the person's
  sessions (the hash changes). "Skip activation" on create is for test accounts that should not go through the activation
  page; only the super-admin and admin may use it, since it would keep a shared default password for good.
- **Pages that can show a password** (`user_create`, `user_reset_password`, `user_bulk`) are `never_cache` and render the
  dialog in the POST response (`json_script:"secrets-data"`), not a redirect.
- Names are stored lowercase (`core.text.clean_text`), shown with `name_case` / `person_name`, and checked for duplicates
  (same first, middle and last name and suffix, unless both have different birth dates). The super-admin role cannot be
  handed out from a screen.

### People screens for admin and coordinator
- **One set of screens, three roles.** `accounts/views/users.py` and `accounts/templates/accounts/people/user_*.html` serve
  `/superadmin/users/`, `/admin/users/` and `/coordinator/users/`. The views redirect through
  `request.resolver_match.namespace`; shared templates use `{% load people_tags %}` and `{% role_url 'user_detail' pk %}`.
  Never hardcode `superadmin:...` in a shared template. The address dropdown partial `_cascade_field.html` stays in
  `accounts/superadmin/` (the extension form includes it too) and takes `optional=True` for the person form.
- **Who may do what** (`accounts/permissions.py`, `_ACTOR_RULES`; `can_act_on`, `require_can_act`):
  - Super-admin: everything.
  - Admin: acts on coordinators and members anywhere (create, edit, move, reset password, archive, restore, suspend,
    unsuspend, deactivate). Never force delete; never super-admins or other admins.
  - Coordinator: acts on members of their OWN extension only (create, edit details, reset password, archive, restore).
    No role or extension change, no suspend or deactivate, no delete, no typed passwords.
  - `assignable_roles`: super-admin admin/coordinator/member; admin coordinator/member; coordinator member.
    `require_can_create` also pins a coordinator's new people to their own extension. `require_placement_manager`
    (move people, change role or extension) is super-admin and admin only. Every service calls these on the LOCKED
    person row; the view decorator (`role_required(SUPER_ADMIN, ADMIN, COORDINATOR)`) only guards the page.
- **Scoping:** `User.objects.for_user(actor)` (a manager method in `models.py`) is the only place that narrows people by
  who is asking: super-admin everyone, admin coordinators and members, coordinator the members of their extension. Views
  and bulk actions start from it, so someone out of reach looks exactly like someone who no longer exists. A coordinator's
  extension is forced in the form (`UserForm.fixed_extension`) AND checked in the service, whatever the browser posts.
- **Buttons follow the rules:** `available_actions(actor, person)` (the same matrix plus the person's status) feeds
  `{% person_actions person as can %}`. The detail page, the list's right-click / three-dot menu (`data-can` on each row)
  and the bulk bar all use it, so Restore shows only on archived people and Archive never on archived ones. A bulk button
  shows only while a ticked row has a status it applies to (`BULK_APPLIES` in `views/users.py`). Hiding a button is never
  access control; the services check again.
- **Contact details:** `app_user` has optional `suffix`, `phone_number` and an address with the same parts as `extension`
  (`country`, `province`, `municipality`, `barangay`, `postal_code`, `street`, `building_number`), all blank by default.
  `address.ADDRESS_FIELDS` lists the parts. Rules: `check_address` (the Philippine rules, same as extensions) plus "a partly
  filled address needs a country" (`services.users.check_contact`, and in `UserForm`). Phone: `accounts/contact.py`
  (stored as digits; `+639...` becomes `09...`). Suffix: `core.text.clean_suffix` / `suffix_case` (stored `jr`, shown
  `Jr.`). The duplicate-name check (`find_duplicate_people` in `services/users.py`, also used by attendance) includes the suffix and only runs when a name, the suffix or the birth date changes.
  Phone and address values are NEVER written to `audit_log`: `update_user` logs only `contact_fields_changed` (field names).
  `app_user.gender` (`accounts.models.Gender`: male, female) is handled the same way (`PRIVATE_FIELDS` in
  `services/users.py`): REQUIRED on the people create and edit screens (`UserForm`, and `create_user` / `update_user` refuse
  a blank one with "Choose a gender."), set by leaders only (not on the member's own profile in v1), saved without
  re-running the duplicate-name check, never written to `audit_log`. The column stays `blank=True`: people saved before the
  rule keep working and are forced to get a gender the next time they are edited.
  `app_user.age_network` (`accounts.models.AgeNetwork`, blank = unknown) is the fallback age group for people with no birth
  date. It is not on the people forms; attendance fills it (see `remember_age_network`), and `convert_first_timer` copies the
  first timer's network when there is no birth date. Never written to `audit_log`.
- **Main church tag:** `extension.is_main_church` (default false) with a partial unique index (`uq_extension_main_church`), so
  at most one extension holds it. `create_extension` / `update_extension` take `data["is_main_church"]`; giving the tag to an
  extension takes it from the old holder in the same transaction (rows locked in ascending id order, old holder saved first)
  and writes one `extension.main_church_change` audit row. Unticking removes it (no main church is allowed). A key that is
  absent changes nothing. The main church cannot be archived until another one (or none) holds the tag. `ExtensionForm`
  keeps the field out of the model's constraint check on purpose (the service moves the tag).
- **Special roles** (`services/special_roles.py`, `views/special_roles.py`): `tithes_offering` and `attendance`. The
  super-admin and admins (any extension) and a coordinator (their own) give or take them on a member's detail page. Only a
  member who can sign in (not archived, not suspended) can hold one. `assign_special_role` locks the extension row, then
  the person, and refuses when no `extension_special_role_limit` row exists or the limit is full. Limits are set per
  extension on `/admin/special-roles/` and `/superadmin/special-roles/` (super-admin and admin only,
  `set_special_role_limits`, all or nothing; a limit cannot go below the current holders and cannot be cleared while
  someone holds the role). Archiving or moving a person clears their roles (`_clear_special_roles`). Audit actions:
  `special_role.assign`, `special_role.remove`, `special_role.limit`.
- **Default passwords:** personal to the super-admin and each admin, one per role beneath them (super-admin: admin,
  coordinator, member; admin: coordinator, member); a coordinator keeps none. The password a person gets depends on WHO
  creates or resets them (`services.users.default_password_row`): the super-admin their own (the global default); an admin
  their own, else the global one (`global_default_row`: the most recently changed row of an active super-admin); a
  coordinator the global MEMBER one only. No default means a generated password. Only the super-admin and admin may type a
  password or skip activation (`can_type_password`); a coordinator's new members always get the global member password
  and must change it at first sign-in. Screen: `views/default_passwords.py` with
  `accounts/people/default_passwords.html` (super-admin and admin; an admin with none of their own for a role sees that the
  global one is used). A coordinator has no screen: the "Default password" button on their people list POSTs to
  `coordinator_area:default_password_show` (`reveal_default_password`), which shows the global member password read-only in
  the show-once dialog and writes the audit row `default_password.view` (who looked, never the password). Each default is
  stored as a hash plus an encrypted copy (`DefaultPassword.password_encrypted`, key `DEFAULT_PASSWORD_KEY`).

## Attendance
Bricks 1 to 14 are implemented. In addition to the sheet workflow, first-timer conversion and "My attendance":
- **Repeat visitors (brick 11):** after three distinct uploaded sheets at an extension, an active member whose home extension differs is transferred there. A draft or a single visit never changes their extension. The transfer is performed by `accounts.services.users.auto_transfer_visitor`, updates `user_extension_history` with reason `auto_transfer`, clears extension-specific roles, and logs extension ids only.
- **Account reset (brick 12):** a reset sets `status=not_activated` and `must_change_password=True`; the account id remains the sign-in identifier and activation is required on the next login. The password hash change ends existing sessions.
- **Welcome slip (brick 13):** first-timer conversion offers a browser-printable 58 mm receipt. It is rendered only in the uncached one-time response; a generated password can be printed, while default/typed credentials require the leader to hand over the password separately. No printer driver or server-side PDF dependency is used.
- **My attendance (brick 14):** own uploaded-sheet history only, newest first, with no user id accepted from the request. The first-visit row appears once its sheet is uploaded.

The source plan `docs/attendance.md` is not present in this checkout; the defaults above are the implemented acceptance assumptions.
- **Models** (`attendance/models.py`): `Attendance` (one sheet per extension, date and label; `taken_by`, `uploaded_by`,
  frozen `summary` JSON that is NULL while a draft; `is_locked` is true when uploaded), `AttendanceMember` (a tick, with a
  SNAPSHOT of name, gender, age network and home extension taken at tick time; gender and age network may be blank until the
  taker fills them in), `AttendanceFirstTimer` (first, middle, last name, suffix, required gender and age network, optional
  phone and birth date; names stored lowercase and cleaned in `save()`, shown with `full_name`).
- **Age networks** (`attendance/age.py`): `AgeNetwork` (adult 36+, young adult 25 to 35, youth 13 to 24, children 0 to 12;
  defined in `accounts/models.py`, re-exported here), `age_on(birth_date, on_date)` and `age_network_for(birth_date, on_date)`
  (None without a birth date or when it is after the date). `effective_age_network(person, on_date)` is what the sheet
  uses: the birth date when there is one, else the network stored on the person (`User.age_network`), else None.
  A 29 February birthday counts on 1 March in a common year. Always computed as of the SHEET date.
- **Dependencies:** `Gender` and `AgeNetwork` live in `accounts/models.py` and attendance imports them (`attendance.age`
  re-exports `AgeNetwork`).
  `accounts` never imports `attendance`.
- **Who may do what** (`attendance/permissions.py`, the only place that decides): `Attendance.objects.for_user(actor)`
  (super-admin and admin all sheets; coordinator their extension's; an attendance special member the DRAFTS of the extension
  where they hold the seat; anyone else, or anyone who cannot sign in, nothing) and `can_view`, `can_create`,
  `can_edit_draft`, `can_review`, `can_upload`, `can_reopen`, `can_convert`, `can_print` (+ `has_attendance_role`, `require`). "Own extension"
  means the SHEET's extension. Views start from `for_user`; services call `require(can_..., actor, sheet)` again.
  `can_convert` is the coordinator of the sheet's extension or an admin or super-admin, on a draft or uploaded sheet (never a
  special member, never an archived sheet).
  Upload is the coordinator of that extension on a draft; an admin or super-admin may upload only while the extension has no
  coordinator (open item in the plan, default taken). Reopen is admin and super-admin only, on an uploaded sheet.
- **Draft services** (`attendance/services.py`): `create_sheet`, `set_taken_by`, `search_people`, `find_possible_matches`,
  `mark_present`, `unmark_present`, `set_member_category`, `add_first_timer`, `update_first_timer`, `remove_first_timer`.
  Every action on a sheet's rows runs in `transaction.atomic()`, locks the sheet row (`select_for_update`), re-checks permission
  on the LOCKED row and refuses with a `ServiceError` unless the sheet is a draft (the upload takes the same lock, so a late tick
  from a slow device is refused). Details:
  - `create_sheet` locks the extension row and refuses a repeat of extension, date and label (ignoring capitals, archived
    sheets included); `created_by` and `taken_by` are the actor; logs `attendance.create` (date and label only).
    `request` is an optional keyword for the audit row.
  - `set_taken_by`: coordinator of the extension or an admin, on a draft (`can_set_taken_by`); the person must be that
    extension's coordinator or an attendance special member who can sign in. Not audited.
  - `search_people` is a DELIBERATE exception to `for_user` (a visitor belongs to another extension): it searches everyone who
    can sign in, needs the right to edit the draft, asks for 3+ characters (fewer returns `[]`), returns at most 20 people and
    only `id`, `name`, `home_extension` ("-" for an admin or super-admin) and `age_network`. Never birth date, phone or address.
  - `mark_present` returns `(row, created)`. The row snapshots name, home extension, gender and age network (the age network as
    of the SHEET date). The person's own gender, birth date and stored age network win; what the taker supplies only fills
    blanks. A gender is stored on the row only. An age network is stored on the row AND on the person (when they have no birth
    date), and `set_member_category` also updates the person's stored age network when the taker changes it, so nobody is asked
    every Sunday. A row with a missing category is saved blank and flagged on the screen; the upload refuses blanks.
    A second tick of the same person changes nothing.
  - `add_first_timer` returns an `AddFirstTimerResult` (`first_timer` or `matches`, `needs_confirmation`). When people with the
    same name exist and `confirm_new` is false, nothing is saved and the matches come back for the "same person?" prompt. A birth
    date, when given, DECIDES the age network. The same first timer twice on one sheet is refused unless birth dates differ.
    An earlier unconverted first timer of the same extension with the same name counts like an existing person: without
    `confirm_new` nothing is saved and the result carries `previous_visits` (see First timer to member).
  - Two guards protect conversions: `mark_present` refuses a person who is already a CONVERTED first timer on that sheet (they
    would count twice), and `remove_first_timer` refuses a converted row (it is the member's "First visit").
  - Ticks, first timers and `taken_by` changes are working data and are NOT audited.
- **Duplicate-name rule** (`accounts.services.users.find_duplicate_people`, one rule for user create and edit and for the
  attendance "same person?" prompt; `find_duplicate_person` returns its first result). Same first name, last name and suffix;
  archived people count; a match is dropped only when both sides have a birth date and they differ. `middle_name=None` means
  any middle name (used by attendance).
- **Summary** (`attendance/summary.py`): `build_summary(sheet)` counts the STORED rows only (never current birth dates) with two
  grouped queries and returns a `SheetSummary` (`summary` JSON for `Attendance.summary`, `members_present`,
  `first_timers_count`, `total_present`, `incomplete`). It serves the live preview on a draft and the value frozen at upload, so
  they cannot disagree. `summary_for(sheet)` returns the frozen numbers for an uploaded sheet and counts live otherwise (draft or
  reopened); screens and the print page call it. `incomplete_rows(sheet)` lists member rows missing a gender or age network
  (the upload refuses while any exist). `summary_table(summary)` gives the display rows (Men and Women by the four age networks,
  for first timers and for members, with totals) for the screen and the print page. A member row without a category counts in
  `members_present` but in no cell, so a frozen summary always adds up.
- **Upload** (`upload_sheet(actor, sheet_id, *, token, request=None)` in `attendance/services.py`, returns an `UploadResult`
  with `sheet` and `duplicate`): runs in ONE transaction, in this order: (1) consume the form's one-time token (a replay
  returns `duplicate=True` and does nothing, so a double tap is quiet); (2) lock the sheet row and require a draft
  (`_check_draft`, so an uploaded sheet is refused even with a fresh token) and `can_upload`; (3) refuse while
  `incomplete_rows` is not empty (the message says how many) or nobody is marked (a sheet of only first timers is fine);
  (4) `build_summary`; (5) write `members_present`, `first_timers_count`, `total_present`, `summary`, `uploaded_by`,
  `uploaded_at` and the uploaded status; (6) `accounts.services.presence.mark_attended` for the ticked people; (7) log
  `attendance.upload` with the three counts only. Any refusal rolls the token back too, so the same form can be sent again
  once the problem is fixed. `last_attended_at` is set to the sheet date capped at TODAY in Manila, so a sheet dated in the
  future by mistake cannot give anyone a future attendance date. The view puts `new_submission_token()` in the form.
- **Reopen** (`reopen_sheet(actor, sheet_id, *, token, request=None)` in `attendance/services.py`, returns a `ReopenResult` with
  `sheet` and `duplicate`): admin or super-admin only (`can_reopen`), uploaded sheets only. ONE transaction, in this order:
  (1) consume the form's one-time token (a replay returns `duplicate=True`); (2) lock the sheet row and require the uploaded
  status (a draft or archived sheet is a `ServiceError`); (3) `require(can_reopen, ...)`; (4) set the status back to draft and
  clear `members_present`, `first_timers_count`, `total_present`, `summary`, `uploaded_by` and `uploaded_at`; (5) log
  `attendance.reopen` with the old counts in `before` (counts only, no free-text reason). A refusal rolls the token back too.
  `last_attended_at` is NOT rolled back; the next upload only moves it forward. A reopened sheet leaves the dashboards (they
  count uploaded sheets only) until it is uploaded again. This is the ONLY way to edit an uploaded sheet: reopen, change it with
  the draft services, then the coordinator uploads again. The view puts `new_submission_token()` in the form.
- **First timer to member** (brick 10, `attendance/services.py`): `convert_first_timer(actor, first_timer_id, *, token,
  request=None)` returns a `ConvertResult` (`user`, `password` as a `PasswordInfo`, `first_timer`, `duplicate`). Allowed through
  `can_convert` on the first timer's OWN sheet. ONE transaction, in this order: (1) consume the form's one-time token (a replay
  returns `duplicate=True`); (2) lock the sheet row, then the first-timer row; refuse an archived sheet and a row that is
  already converted; (3) call `accounts.services.users.create_user` (role member, the sheet's extension, the row's names, gender,
  birth date and phone, the actor's default-password rules). It derives its own token from the form's (`cv-<token>`), so one
  submit is one account, and it runs the duplicate-name check first, so someone who already has an account is refused;
  (4) set `converted_user`; (5) log `attendance.first_timer_convert` with ids only (`attendance_id`, `user_id`). NO account is
  created automatically, and NO member row is added to the sheet: the person already counts once, as a first timer, so a member
  row would count them twice in the frozen numbers. `previous_first_timer_visits(extension, first_name, last_name, *, suffix,
  before_date, exclude_sheet_id)` lists earlier UNCONVERTED visits in the same extension (`first_timer_id`, `sheet_id`, `date`;
  archived sheets and converted rows left out); `add_first_timer` returns them as `previous_visits` for the "Visited before on
  [date]" prompt. `member_attendance_history(user)` reads UPLOADED sheets only, newest first: ticks are "Present", first-timer
  rows converted into this account are "First visit"; nothing is copied between tables, and a reopened sheet leaves the history
  until it is uploaded again.
- **Convert screen** (`sheet_first_timer_convert` in `views.py`, route `sheets/<pk>/first-timers/convert/` in `area_urls.py`):
  a `sheet_action` POST that renders `attendance/convert_result.html` instead of redirecting, because the new password is shown
  ONCE through the secrets dialog (`secrets-data`), like the people screens. The first timer may be on this sheet or an earlier
  one (the "visited before" prompt); with `mark_here` the new member is also ticked present on this draft. Partials to include
  in the workspace and review templates: `_convert_button.html` (one first-timer line) and `_previous_visits.html` (the
  "same person?" prompt). The context carries `can_convert` and `convert_token`.
- **My attendance** (brick 14, `my_attendance` in `views.py`, route `mine/` in `attendance/urls.py` ONLY, name `attendance:mine`,
  template `attendance/mine.html`, a "My attendance" entry in `NAV_ITEMS` for every role): the signed-in person's own history
  from `member_attendance_history(request.user)`. It never reads a person id from the URL, query string or a form, skips
  `attendance_screen` on purpose (that guard is about sheets) and is NOT in `area_urls.py`, so it never appears under a role area.
- **Admin alerts** (`notifications/listeners.py`, `notify_admin_alert`, connected to `audit.signals.action_logged`): looks up the
  ENABLED `notification_rule` for the logged action; the recipients are the people whose role is in `notify_roles` and who can
  still sign in, except the person who did the action and anyone who turned off in-app alerts of type `admin_alert`. The bell
  item (`type="admin_alert"`, important) carries only `data={"action": ...}`, the entity type and id, and `audit_log_id`. It
  runs inside `log_action`'s transaction. `notifications` never imports a feature app. Every seeded rule alerts this way, not
  only `attendance.reopen`. The bell list does not render this type yet (open item).
- **Screens** (`attendance/views.py`, `forms.py`, `templates/attendance/`): thin views over the services, one set shared by four
  places. The routes are written ONCE in `attendance/area_urls.py` (no `app_name`) and mounted by a STRING include
  (`include("apps.attendance.area_urls")`, so `accounts` imports nothing from `attendance`) in `accounts/urls/superadmin.py`
  (`/superadmin/attendance/`), `admin_area.py` (`/admin/attendance/`) and `coordinator_area.py` (`/coordinator/attendance/`),
  and once more by `attendance/urls.py` at the member root (`/attendance/`, namespace `attendance`, for attendance special
  members). Routes: `sheets/` (list), `sheets/new/`, `sheets/<pk>/` (the draft workspace, or the review once uploaded),
  `review/`, `print/`, and POST-only `mark/`, `unmark/`, `category/`, `first-timers/add/`, `first-timers/remove/`, `upload/`,
  `reopen/`, and POST-only `first-timers/convert/`. Templates and redirects follow the namespace of the request (`request.resolver_match.namespace`, and
  `{% role_url %}` in templates), never a hardcoded one. Every view goes through `attendance_screen`: `role_required`, then
  `AREA_ROLES` (each namespace belongs to ONE role: a coordinator typing an admin URL is sent home), then
  `permissions.can_use_area`, and it is `never_cache`. Every sheet is opened with `_open_sheet`, which starts from
  `Attendance.objects.for_user(request.user)`; a sheet that is missing OR out of reach gives the same "no longer exists"
  message. POST actions use the `sheet_action` decorator: it loads the sheet, runs ONE service, and turns a `ServiceError` or
  `PermissionDenied` into an error toast and a redirect back. `AREA_ROLES` must stay in step with `church_management/urls.py`
  and the role url modules.
- **A4 print page** (brick 9; `sheet_print` in `views.py`, `templates/attendance/print.html`, route `sheets/<pk>/print/`):
  a read-only GET that changes nothing and logs nothing. Allowed through `can_print` (the same rule as `can_view`: a special
  member prints drafts only). The page is STANDALONE, it does not extend `base_app.html`: black text on white whatever the
  person's light or dark mode, inline CSS, no external fonts, no Tailwind, no `{% include 'includes/logo.html' %}` (that logo
  is a CSS mask whose class lives in `input.css`, so the page uses a plain `<img src="{% static 'images/black-logo.svg' %}">`).
  Layout copies the paper sheet: header (logo, church and extension name, municipality and province, "Attendance sheet", date,
  service label, "Taken by", and "Uploaded by" with the upload date or a plain "DRAFT, not uploaded" line), ONE numbered grid
  (members by name, then first timers by name) with Name, Belongs to, Men and Women by Adult, YA, Youth, C, and an FT column, a
  tick in the cell of the person's stored gender and age network (a first timer gets their gender and age tick AND the FT tick),
  then the summary table from `summary_table`, "Total attendees" (`summary.total_present`), the age-network legend and the
  printed date and time in Manila. "Belongs to" shows the home extension only for a visitor (home extension different from the
  sheet's, the same rule as the review) and is blank for everyone else, admins included. The view builds `rows` (`_print_rows`:
  `number`, `name`, `belongs`, `first_timer`, `needs_category`, `cells`, eight booleans, men then women, in age-network order);
  only names, home extension, gender and age network are read, so a birth date, phone number or address cannot reach the page.
  An uploaded sheet prints its FROZEN numbers (`summary_for`); a draft counts live and a member still missing a category is
  marked "(no category yet)" with no tick. CSS: `@page { size: A4; margin: 12mm }`, `thead { display: table-header-group }`
  so the header repeats on page 2, `tr { break-inside: avoid }`, the summary block never splits, and NO page numbers (CSS page
  counters are unreliable across browsers). The toolbar and the grey desk are screen only. "Save as PDF" is the browser's print
  destination; there is no server-side PDF library (it would add system packages to the Docker image) and no direct ESC/POS
  printing. The review page has a "Print or save as PDF" button (`can_print` in its context) that opens the page in a new tab.
- **Privacy:** no names, birth dates, phone numbers or passwords in `audit_log`; individual ticks are not audited. The print
  page and the people search show no birth dates, phone numbers or addresses.
- **Tests:** `attendance/tests/` (a package, so there is no `tests.py` beside it): `test_age`, `test_conversion`, `test_models`,
  `test_permissions`, `test_print`, `test_reopen`, `test_services`, `test_summary`, `test_upload`. The admin-alert receiver is tested in
  `notifications/tests.py`. The gender field is tested in
  `accounts/tests/test_gender.py`, the stored age network in `accounts/tests/test_age_network.py`, the main church tag in
  `accounts/tests/test_main_church.py`, and `mark_attended` in `accounts/tests/test_presence.py` (tests live with the code they check). In tests, never create two sheets with the same extension, date and label,
  and set `archived_at` together with `status="archived"` (a database constraint ties them).

## Bible
- **Where:** `apps/bible/`: `models.py`, `books.py` (the 66 books, OSIS ids), `osis.py` (file reader),
  `versions.py` (version catalog), `services.py`, `esv.py` + `esv_client.py` (ESV), `errors.py`, `signals.py`,
  `views.py`, `urls.py` (namespace `bible`, mounted at `/bible/`), `management/commands/import_bible.py`,
  templates in `apps/bible/templates/bible/` (`reader`, `book`, `chapter`, `_version_picker`), tests in
  `apps/bible/tests/` (`helpers.py` has `make_user()` and `add_chapter()`).
- **Versions:** the picker always lists all of them, in this order.

  | Code | Version | Source | Can lock? |
  |---|---|---|---|
  | `ESV` | English Standard Version | api.esv.org (licensed) | yes (switch and automatic locks) |
  | `KJV` | King James Version | `bible_verse` (public domain) | yes (switch only) |
  | `ADB` | Ang Dating Biblia (1905, Tagalog) | `bible_verse` (public domain) | yes (switch only) |

  `MBBTAG` stays in `Translation` so older rows remain valid, but it is not offered: it needs permission from the
  Philippine Bible Society and is not on API.bible. Adding a version means one entry in `versions.py` plus its source.
  `KJV` is the fallback for a missing or tampered cookie.
- **Models:** `BibleVerse` (local text; constraints refuse ESV and MBBTAG rows), `EsvChapterCache`, `DailyVerse`,
  `BibleChapterRead` (unique per person, book, chapter and Manila day, NOT per translation: reading Genesis 1 in two
  versions on one day is one chapter; `translation` records the first version used), `BibleBookmark` (one row per person, `OneToOneField` on the user: where they are currently reading). `book` is always an OSIS id
  (`Gen`, `1John`).
- **Loading the public domain texts:** `manage.py import_bible KJV|ADB (--file PATH | --url URL)` reads the
  seven1m/open-bibles OSIS files (`eng-kjv.osis.xml`, `tgl-tagalog.osis.xml`). Import is all-or-nothing: `osis.py`
  refuses verse ranges, empty verses and incomplete books (`check_canon`), skips non-canonical books (the KJV file has
  the Apocrypha) and the command replaces one translation in a single transaction, so a failed run keeps the old text.
  Psalm titles and poetry line breaks are not imported (plain verse text only).
- **Reader:** routes `/bible/` (books), `/bible/<book>/` (chapters), `/bible/<book>/<chapter>/` (text), POST
  `/bible/version/` (choose a version), POST `/bible/<book>/<chapter>/finish/` (count as read, move the bookmark), POST `/bible/<book>/<chapter>/bookmark/` (set the bookmark). All four roles can use
  them (still wrapped in `role_required`). The chosen version is kept in the `bible_version` cookie (per device, not per
  account, HttpOnly, one year) and checked against the catalog on every request.
- **Locked versions:** shown in the picker, disabled, with the reason as text, and refused on the server
  (`VersionLocked`) when chosen. A saved version that is locked shows a notice instead of text, never a silent swap.
  The service layer (`services.get_chapter`) may still serve an ESV chapter from the cache while ESV is locked.
- **Verse of the day:** `daily_verse.py` + the curated list in `daily_verse_refs.py` (63 references, no text). The
  verse for a Manila date is `DAILY_VERSE_REFS[date.toordinal() % len]`, saved once as a `daily_verse` row by
  `get_daily_verse()` (`get_or_create`, so a row that already exists, for example one set by hand, always wins).
  Rows are a *reference only*: `translation` is always KJV (whose verse numbers the list uses) and `verse_text` is
  never written, so ESV text is never stored. The text is read at view time with `get_daily_verse_text(row, version)`,
  which goes through `services.get_chapter()`, so switches, ESV locks and the ESV cache all apply. A version that
  cannot be read gives a notice, never another version. `manage.py seed_daily_verses [--days N]` saves the next N
  days ahead (optional, safe to repeat). Tests: `apps/bible/tests/test_daily_verse.py`.
- **Bookmark and Next:** there is no "mark as read" button. The Next button on `chapter.html` is a POST form to `bible:finish` (`after=next`): `services.finish_chapter()` calls `record_chapter_read()`, moves the person's single `BibleBookmark` to the following chapter (crossing book ends) and the view redirects there. The target is computed on the server, never read from the form. "I'm done for now" posts to the same url without `after` and stays on the page. On Revelation 22 the button reads "Finish", the bookmark stays and a toast is shown. Previous is a plain link and counts nothing. A GET never writes. A "Bookmark" button (`bible:bookmark`) sets the bookmark without counting a read. When the text is locked or not imported, Next is a plain link and changes nothing.
- **Chapter reads:** `record_chapter_read()` is idempotent and sends `bible.signals.chapter_read` only for a new row;
  `faith` listens to it for the Bible streak. It does not check version availability (the text may have come from the
  cache). Reads are not written to `audit_log` (high volume, not an administrative action).
- **ESV (api.esv.org), non-commercial use for churches and ministries:**
  - `esv_client.py` does HTTP and parsing only (plain text with `[n]` verse numbers; headings, footnotes and the
    "(ESV)" tag are switched off in the request). A single-chapter book is requested by name only (`Jude`, not `Jude 1`).
  - `esv.get_verses()` is the one entry point: cache first, then lock check, then call budget, then fetch and cache.
  - **Switches** (rows in `site_setting`, on when missing): `bible_api_enabled` (all external Bible APIs) and
    `bible_esv_enabled` (ESV). Counters and the current lock: `bible_esv_state`. All are shared across web workers.
  - **Our budget** is below Crossway's limits (60/minute, 1,000/hour, 5,000/day): 50, 900 and 4,500. When a window is
    full ESV locks until it ends.
  - **Automatic locks:** HTTP 429 waits `Retry-After` (else 15 minutes); a rejected key locks for an hour; any other
    failure for a minute. Messages shown to people never mention keys or limits. Locks clear by themselves.
  - **Cache:** `bible_esv_cache`, at most 450 verses in total (Crossway's limit is 500), least recently used chapters
    go first, rows older than 7 days are ignored, a database constraint caps one row at 500 verses. Cached chapters keep
    loading while ESV is locked or switched off.
  - **Displaying ESV:** the Crossway copyright notice, "(ESV)" in the heading and a link to https://www.esv.org/ on
    every page that shows ESV text (all in `versions.py` and `chapter.html`). Do not change the words.
- **Version switches (super-admin screen):** `/superadmin/site-settings/` (namespace `bible_superadmin`, nav entry
  "Site settings", super-admin only). One master switch for every online service plus one button per version.
  - **Where:** `switches.py` (reads the switches and says why a version is blocked), `site_settings.py` (the only code
    that changes them), `views_superadmin.py`, `urls_superadmin.py`, templates in `bible/superadmin/`
    (`site_settings.html`, `_switch_button.html`), tests in `tests/test_site_settings.py`. The screen lives in `bible`
    because `core` cannot import `accounts` (`role_required`, permissions).
  - **Keys** (rows in `site_setting`, on when missing): `bible_esv_enabled`, `bible_kjv_enabled`, `bible_adb_enabled`,
    and the master `bible_api_enabled` (ESV today). A new version gets `bible_<code lower>_enabled` from
    `switches.switch_key()`; nothing else to add.
  - **Blocked completely:** a switched-off version is refused by `services.get_chapter()` before anything else, so
    even an ESV chapter in `bible_esv_cache` is not shown. The picker shows it locked with the reason as text.
    `services.resolve_version()` skips a switched-off default (KJV) when there is no cookie.
  - **Rules:** at least one version must stay readable (a switch cannot be turned off if it would leave none; the
    master counts as off for ESV). Both guards apply: `role_required(Role.SUPER_ADMIN)` on the views and
    `require_site_settings_manager` (`accounts/permissions.py`) at the top of each function. Forms post the state they
    want (`enabled=1|0`), never "flip", and the service is idempotent: no change means no audit row. Turning off asks
    first (`data-confirm`).
  - **Audit:** `site_setting.bible_version_toggled` and `site_setting.bible_online_toggled`, with the switch key as the
    label and `{"enabled": ...}` before and after. Nothing else is logged.
  - **Automatic ESV locks are separate** and still clear by themselves. To clear one early (shell):
    `SiteSetting.objects.filter(key="bible_esv_state").delete()` (this also resets the call counters). Check the
    state with `from apps.bible import esv; print(esv.lock_reason() or "ESV is available")`.

## Site settings sections
- **One page, many sections.** `/superadmin/site-settings/` (route name `bible_superadmin:site_settings`, kept so the
  sidebar entry works) is the shell in `accounts/views/site_settings.py` + `accounts/templates/accounts/superadmin/site_settings.html`.
  A section is a `SiteSection` (key, title, icon, partial template, `context(request)`, order) added with
  `register_section(...)` from `accounts/site_sections.py`. To add a function: write its partial and its POST views, register
  the section from the owning app (import the module once, for example from that app's urls). The shell needs no change.
  Sections today: `bible` (`apps/bible/site_section.py`, order 10) and `special-roles` (order 20). `accounts` never imports
  feature apps; they register themselves. A section's POST views keep their own `role_required` and service guard: the
  registry only decides what is displayed. Section icons must exist in `sprite.svg`.
- **Special-role limits live in two places with one panel.** The super-admin uses the "Special roles" section; an admin uses
  the "Special roles" button on `/admin/users/` (`admin_area:special_role_limits`). Both include
  `accounts/people/_special_role_limits.html` with `views.special_roles.limits_context()`. A super-admin GET of
  `/superadmin/special-roles/` redirects to the section; POSTs still go to `special_role_limits`.
- **Limit rules (`services/special_roles.py`):** a limit cannot exceed `member_counts()` (members who can sign in: role member,
  in the extension, not archived or suspended), cannot be below the people holding the role, and cannot be cleared while anyone
  holds it. `bulk_set_special_role_limits(actor, request, role, value, extension_ids=None)` runs `set_special_role_limits`
  once per extension (own transaction, ascending id) and returns `LimitBulkResult(done, unchanged, skipped)`; it is idempotent,
  so it needs no one-time token.
- **Tags on the people screens:** the list and detail views call `attach_special_roles(people)` (one query for the page) and the
  templates use `{% special_role_badges person %}`. The list filter `special_role` uses `filter_by_special_role()` (a subquery).

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
  history, service times or locations. Do not invent church facts (service times, addresses, verses attributed to JIL).
- **Content lookup:** `ChurchContent` is unique per `(key, language)`. The page tries the visitor's language,
  then English, then Filipino, and skips rows with an empty body. Loaders import models lazily inside
  functions. If a model field is renamed, edit only the loader in `services.py`.
- **Colors use tokens only.** The navy "deep" sections are `bg-txt-primary text-surface`:
  `txt-primary` is dark in light mode and light in dark mode, so they flip by themselves in every theme.
  The brand gold is the `highlight` token (`--color-highlight`, `--color-highlight-soft`,
  `--color-on-highlight`), defined in `input.css` (light default), `themes.css` (dark) and `theme_palette`
  (`highlight`, `on_highlight`; the soft shade is derived with `color-mix()`), so every theme can change it.
  Use it for fills, lines and button backgrounds only, never for text (it is unreadable on the flipped
  light sections): `bg-highlight`, `border-highlight`, `hover:bg-highlight-soft`, and always
  `text-on-highlight` for text on top of it. Never name a token after its color.
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

## Dashboards
- **Where:** `apps/dashboards/`: `urls.py` (namespace `dashboards`: `superadmin/`, `admin/`, `coordinator/`, `home/`),
  `views.py` (four thin views, each `never_cache` + `role_required`), `services.py` (`get_dashboard_context(user,
  version_code, path)`, all the logic), templates in `apps/dashboards/templates/dashboards/`, tests in
  `apps/dashboards/tests.py`. Role home URLs still live only in `accounts/decorators.py` (`ROLE_HOME_NAMES`).
- **One template per role** (`superadmin.html`, `admin.html`, `coordinator.html`, `member.html`) extending
  `dashboard_base.html`, so a role can change on its own. Shared sections are includes: `_daily_verse`,
  `_continue_reading`, `_quick_links`, `_coming_soon`; leaders also get `_leader_overview` with the charts
  `_bar_list` and `_column_chart`. Members get no overview.
- **Shared sections:** greeting (device clock, "Welcome" without JavaScript); verse of the day in the version from the
  `bible_version` cookie (`VERSION_COOKIE` in `services.py`, must match the reader); continue reading (newest
  `BibleChapterRead`, suggests the next chapter, crosses book ends); quick links taken from `NAV_ITEMS` through
  `build_nav()` (so they always match the sidebar; Home and Django admin are left out; routes that do not exist yet
  show "Soon"); "Coming soon" cards for the Bible streak, goals and events (replace each with the real widget when
  its app has views).
- **Leader overview:** headline tiles, people by role (not archived) and by status (everyone), people per extension
  (super-admin and admin only, top 10), and three trend charts over the last 8 gathering dates: attendance,
  first-timers, tithes and offering. Trends use **uploaded** sheets only and their **frozen** values (never a
  recount); sheets on the same date are added together. The coordinator's numbers are limited to their own
  extension. Only the super-admin sees super-admin rows. Amounts are shown in pesos (`CURRENCY` in `services.py`).
- **Scoping:** the only code that narrows data to a coordinator's extension is `_scope_people`, `_scope_attendance`
  and `_scope_tithes` in `services.py`. `User.objects.for_user(user)` now exists for people, so `_scope_people` can use it;
  `_scope_attendance(user)` already returns `Attendance.objects.for_user(user)` limited to UPLOADED sheets (a reopened
  sheet drops out until it is uploaded again); tithes still need their own helper.
- **Privacy:** tithe amounts appear only on leader dashboards and are never written to `audit_log` or
  `notification.data`. The dashboard writes nothing except today's `daily_verse` reference row.
- **Charts:** server-rendered with Tailwind (no JavaScript, no library); colors are tokens only (`bg-accent`,
  `bg-border`). Every value is also written as text and each column chart has an `aria-label` listing its values.
  Rebuild Tailwind after changing the templates (`make tailwind-build`).
- **ESV on the dashboard:** a member whose version is ESV makes the dashboard fetch (or reuse from the cache) the
  verse's chapter through `esv.get_verses()`, so the normal call budget, locks and cache limits apply. The ESV
  notice, "(ESV)" and the esv.org link are printed from `versions.py` in `_daily_verse.html`.

## Nightly jobs (each is a management command in its own app)
`purge_archived` (core, children first, files before rows, skips extensions
still in use), `archive_inactive_users`, `auto_transfer_extension`,
`cleanup_email_tokens` (accounts), `complete_ended_goals` (faith),
`cleanup_notifications`, `cleanup_audit_log`; every 15 minutes
`send_event_reminders`; hourly `fetch_jil_videos`. Jobs log with
`source='system_job'` and no actor. (`import_bible` is run by hand, not on a schedule.)

## Frontend conventions
- Colors come from CSS custom properties (design tokens) generated from the
  active `theme_palette`, never raw Tailwind palette classes or `dark:`. Use
  utilities such as `bg-bg`, `bg-surface`, `text-txt-primary`,
  `text-txt-secondary`, `border-border`, `bg-accent`, `bg-highlight`. Light/dark is the
  `data-mode` / `.dark` state, not a Tailwind variant.
- `--color-on-primary`, `--color-on-accent` and `--color-on-highlight` are the text colors for filled
  primary, accent and highlight surfaces; use them instead of `text-white`.
- Icons: self-hosted Heroicons sprite,
  `<use href="{% static 'icons/sprite.svg' %}#name">`. No icon fonts or CDNs.
  `aria-hidden="true"` on the icon, `aria-label` on the parent button/link. Icons inside Alpine toggles
  (`x-show`) go in a wrapper `<span>`, since `includes/icon.html` only accepts `name` and `class`.
  An icon name missing from the sprite renders as nothing, so check `sprite.svg` when adding one.
- Layouts: `base_public.html` for logged-out pages, `base_app.html` for the
  authenticated app. Nav entries are data in `accounts/navigation.py` (`NAV_ITEMS`), not markup: never hardcode a
  link in a sidebar or bottom-bar template. Page templates extend one of these two and fill the
  `content` block (and `title`, `page_title` where they exist). Never extend `base.html` directly from a page.
- **Template comments:** `{# ... #}` is single-line only; a multi-line one renders as page text.
  Use `{% comment %} ... {% endcomment %}` for anything longer than one line or that
  mentions template tags. The one exception is the file header: it is an HTML comment
  (`<!-- ... -->`) placed before `{% extends %}`, listing the blocks the template fills, the context variables it
  expects and, for includes, a usage example. It must contain no template tags,
  because anything tag-like inside an HTML comment still executes. Never put a comment
  inside a tag. In JS and CSS use `/* ... */` or `//`.
- **Includes and blocks:** `base_public.html` composes `includes/public_navbar.html` and `includes/footer.html`
  inside `{% block body %}`: a `min-h-dvh` flex column holding the navbar, a `flex-1`
  `<main>` that centers the `content` block (`items-center justify-center px-4 py-10`),
  then the footer. Pages never include the footer themselves. An `{% include %}` outside a block is ignored in a child
  template, so keep includes inside blocks. A `{% block %}` inside a file that is only `{% include %}`d never
  overrides anything. Public pages fill only `content` (and `main_align`).
- Public navbar: on `md` and up it is a three-column grid (`md:grid-cols-[1fr_auto_1fr]`) so
  the links stay centered whatever the brand text width; below `md` it is a flex row. The
  mobile menu is `absolute inset-x-0 top-full` under the sticky header, so it overlays the
  page instead of pushing it down. Keep the nav and menu `max-w-*` the same.
- The navbar Login button uses `{% url 'accounts:login' %}`. The About / Give links are in-page
  anchors (`#about`, `#give`) that exist only on the landing page; give them real URLs before
  adding other public pages. `{% url %}` on a missing route raises `NoReverseMatch`.
- Fonts are self-hosted from `static/fonts/`. `font-main` is Space Grotesk (body and UI, declared as `'Grotesk'`);
  `font-mono` is JetBrains Mono (account ids, amounts). Change a font by editing
  `--font-main` / `--font-mono` and the matching `@font-face` in `input.css`. The Space Grotesk `@font-face`
  says `format('woff2')` but points at a `.ttf` file; use `format('truetype')` to match the file. Font file names
  with spaces or commas need URL-encoding in `@font-face`; prefer renaming them to plain names.
- Reusable component classes live in `input.css` under `@layer components`:
  `.btn`, `.btn-primary`, `.btn-secondary`, `.btn-ghost`, `.btn-highlight`, `.btn-danger`, `.label`, `.input`,
  `.input-error`, `.field-error`, `.field-help`, `.card`. Use them before writing
  new one-off utility strings, and build new ones from tokens only.
- Light/dark: `base.html` sets `data-mode` and `.dark` on `<html>` from
  `localStorage` (falling back to the system setting) before first paint, and
  exposes `window.toggleMode()`. When `user_settings` is wired up, the server
  value `theme_mode` takes over. Dark values live in `static/css/themes.css`.
- Logos: `includes/logo.html` draws the SVG from `static/images/` as a CSS mask,
  so its color follows the theme tokens. Options: `variant` (`mark` | `full`),
  `plate`, `size`, `decorative`.
- `input.css` must contain `[x-cloak] { display: none !important; }`. Put
  `x-cloak` on anything Alpine shows or hides on load, to avoid a flash.
- **Toasts and confirm dialogs are global.** `static/js/ui.js` registers the Alpine store
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
- **App shell (`base_app.html`):** the header shows the logo (`brand_mark.html`, the one place its size is set) and
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
- **App shell layers:** the page (`bg-bg`) is the lowest layer. The icon rail and the header are `bg-shell` and float above it:
  a hairline (`border-shell-border`, level across the rail's menu row and the header) plus a soft shadow that falls onto the
  page. The sidebar panel, the mobile bottom bar and its More sheet use the same color and sit higher, with a bigger shadow and
  light glass (`supports-[backdrop-filter]:bg-shell/85 supports-[backdrop-filter]:backdrop-blur-xl` over a solid fallback).
  `--color-shell` and `--color-shell-border` are derived in `input.css` (`color-mix()` from `--color-surface` and
  `--color-primary`), so they follow light/dark mode (higher layers are lighter in dark mode) and custom themes. Shadows are a
  black `shadow-[...]` with low alpha. Cards, tables and forms stay flat with borders; no glass behind dense data.
- **Public pages:**
  - Sizing: `<main>` already centers and pads the page, so a public page sets only its own width (`w-full max-w-*`)
    and adds no outer padding. If a page is not vertically centered, check the real `base_public.html` and the navbar
    height before adding offsets. The one exception is the landing page, which is full width (see Landing page).
  - Footer (`includes/footer.html`): sits in normal flow (never `fixed`, which covers content on short screens),
    `border-t border-border bg-bg`, `text-txt-secondary` (not `text-txt-muted`, which fails contrast). The year comes
    from `{% now "Y" %}`. A `{% block %}` inside an included file overrides nothing; pass values with
    `{% include ... with church_name="..." %}`. The landing page also has its own long footer block; the shared footer
    still follows it and acts as the copyright line.
  - Accessibility: body text 16px or larger, touch targets at least 44px, contrast AA in both modes, and no meaning
    carried by color alone (for example a locked Bible version shows its reason as text, not just a grey button).
  - Visual direction: calm, minimal, human. Flat surfaces, borders instead of shadows, one accent color, real church
    context (greeting, first-time note, verse) instead of generic marketing copy. Glass is optional and subtle: keep a
    solid fallback and add `supports-[backdrop-filter]:bg-surface/70 supports-[backdrop-filter]:backdrop-blur-md` over at
    most one soft glow (`bg-accent/20 blur-3xl`). Opacity modifiers on tokens work in light and dark. Never use glass
    behind tables or dense data.
- **Login page pattern (`accounts/login.html`):** two sides from `md` up (greeting, first-time
  note and verse on the left, form on the right), stacked below `md`; the verse is hidden
  below `md`. The greeting uses Alpine (`x-data` with the device clock) and falls back to
  "Welcome" without JavaScript. Show password is a checkbox (`x-data="{ show: false }"`,
  `x-bind:type`, `x-cloak` on the checkbox label) on the same row as "Forgot password?".
  Failed logins and a badly shaped account id share one generic error box (`role="alert"`)
  listing `non_field_errors` and `identifier.errors`. The account id input is `font-mono`
  with `autocomplete="username"` and `autocapitalize="none"`; the password is never echoed
  back. Inputs are enlarged with `px-4 py-3.5 text-base` on top of `.input`.

## Known footguns
- `migrate` from the host cannot reach `POSTGRES_HOST=db`; run it in the `web`
  container. `makemigrations` works from the host.
- Running `migrate` before `AUTH_USER_MODEL` is set causes
  `InconsistentMigrationHistory`; fix by `docker compose down -v` (dev only).
- Core migration `0002_seed_and_triggers` depends on the first migration of
  several apps; if an app's tables move to a later migration, update its
  `dependencies`.
- pgAdmin shows FKs as `NO ACTION`; that is expected (see Model conventions). pgAdmin access needs the dev-only
  port from `docker-compose.override.yml.example`; never publish the db port in production.
- `TemplateDoesNotExist` for `base_public.html` means `TEMPLATES['DIRS']` is
  missing `BASE_DIR / 'templates'`. Static files need
  `STATICFILES_DIRS = [BASE_DIR / 'static']`. After adding folders, restart the
  `web` container (or rebuild) so they are mounted.
- `{% extends %}` needs the full file name: `"base_public.html"`, not `"base_public"`.
- Django 5+ logout is POST-only; use a form with `{% csrf_token %}`, not a link.
- Rebuild Tailwind (`make tailwind-build`) after adding classes in templates,
  or run `make tailwind-watch`.
- `static/js/ui.js` must be loaded with `defer` before `alpine.min.js` in `base.html`.
  The `alpine:init` listener has to exist before Alpine starts, otherwise `$store.ui`
  is undefined and toasts and confirm dialogs silently do nothing. Keep
  `{% include 'includes/messages.html' %}` in `base.html` after `{% block body %}`.
- A form with `data-confirm` submits normally if `ui.js` fails to load, so the server
  must never rely on the dialog for safety.
- A `position: fixed` footer or bar covers page content on short screens; keep the footer in
  normal flow.
- **A key in `.env` is not enough.** `ESV_API_KEY` reaches the app only if `docker-compose.yml` passes it to the `web`
  service (`env_file: .env` or `environment:`) AND the settings file defines `ESV_API_KEY`. After changing either,
  run `docker compose up -d` (a plain `restart` does not re-read environment variables). Quick check:
  `docker compose exec web python manage.py shell -c "from apps.bible import esv; print(repr(esv.lock_reason()))"`
  (`''` means available; `'ESV is not set up yet.'` means no key reached the app).
- `import_bible` needs the container to reach the internet when you use `--url`; use `--file` otherwise.
- A new field on `User` (such as `suffix` or `phone_number`) breaks every page, login included, with
  `column app_user.<name> does not exist` until `makemigrations accounts` and `migrate` have run in the `web` container:
  the auth backend reads `app_user` on every request.
- The URL namespace `admin` is Django's built-in admin. The Admin role's screens use `admin_area`, so reverse names such
  as `admin_area:user_list`, never `admin:user_list`.
- The attendance routes exist only where `include("apps.attendance.area_urls")` is added. If `superadmin.py`, `admin_area.py`
  or `coordinator_area.py` lacks it, `{% role_url 'sheet_list' %}` and every attendance link fail for that role. A new
  attendance route goes in `attendance/area_urls.py` once, never in a role url module.
- `attendance/print.html` is standalone on purpose. Do not make it extend `base_app.html` or reuse `includes/logo.html`: the app
  shell's tokens, dark mode and the logo mask would put colours and an empty logo on paper. Rebuilding Tailwind does not affect it.
- The people templates are shared by three roles. Link through `{% role_url %}` and gate buttons with
  `{% person_actions %}`; a hardcoded `superadmin:` URL works for the super-admin and raises `NoReverseMatch` or sends an
  admin or coordinator to the wrong area.

## Working rules for agents
- Use Docker commands; run `check` and `makemigrations --check --dry-run` after any model change.
- Never edit an applied migration; add a new one.
- Do not commit `.env`, `venv/`, `theme/tailwindcss`, media uploads or db dumps.
- Keep `.env.example` in sync with settings.
- **Document the code so it is easy to read.** Every new module, class and
  function gets a short docstring saying what it is for. Add a comment for the
  why when the reason is not obvious (a constraint, a trigger, a workaround).
  Each template starts with a header comment (see Template comments). When
  you change structure, commands or behaviour, update `README.md` and this file
  in the same change.
- Tests must not touch real external services (see Behaviour rules). A test that needs a logged-in user creates an
  activated one (see Activation).
- Prefer the smallest relevant command or page load to verify a change.
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
  - Accounts: login (`/accounts/login/`, `/accounts/logout/`), forced password change (`/accounts/change-password/`), profile
    (`/accounts/profile/`), `role_required` (`accounts/decorators.py`).
  - Super-admin extension management (`/superadmin/extensions/`).
  - People management for three roles, one set of screens with role-based rules: super-admin (`/superadmin/users/`),
    admin (`/admin/users/`) and coordinator (`/coordinator/users/`); optional contact details (suffix, phone, address);
    special member roles with per-extension limits (`/admin/special-roles/`, `/superadmin/special-roles/`); default
    passwords for the super-admin and admins (see People screens for admin and coordinator).
  - Public registration and approvals (`/accounts/register/`, role-scoped registration approval inboxes), matching, one-time password emails and nightly request cleanup (see Accounts).
  - A global toast and confirm dialog (`static/js/ui.js`, rendered by `includes/messages.html`).
  - Bible: reader with three versions (KJV and Ang Dating Biblia from the database, ESV from api.esv.org), version
    picker with locking, chapter reads (Next counts a chapter as read), a per-account bookmark with an automatic bookmark
    after 20 seconds, ESV call limits, locks and a size-capped cache, and the super-admin
    switches for every version (`/superadmin/site-settings/`, see Bible), and the verse of the day.
  - Faith (bricks 1 to 4 and 10): goals with optional times, daily check-ins, streaks (daily open, Bible, perfect day,
    per goal), goal detail and history pages, dashboard streak and goals-today widgets, goal reminders and the nightly
    completion of ended goals (see Faith).
  - Buddy (bricks 6 to 9): requests and partnerships, messages with nudge and "prayed", and the partner's goals
    (see Buddy).
  - Prayer (brick 5): member send form and the role-scoped inbox with replies and mark as done (see Prayer).
  - Dashboards: one page per role (super-admin, admin, coordinator, member) with the verse of the day, continue
    reading, quick links, streaks and today's goals and, for leaders, counts and charts (see Dashboards).
  - Attendance (bricks 1 to 14): gender and age networks, model changes, permissions and scoping, draft services,
    summary, upload, reopen, the sheet screens for every role, A4 print page, first timer conversion, repeat-visitor
    auto-transfer, reset-to-password-change, guest promotion and personal history (see Attendance).
  - Tithes: reports on tithes and offering per attendance sheet, denomination count, finalize with a bypass
    request flow, households, A4 print page and "My tithes" (see Tithes).
  - Events (calendar, list, notices and reminders), Web Push on top of the bell, and the Site settings sections
    `jobs` and `retention` (see Events and Push notifications).
- **Not built yet:**
  - Brute-force login throttling.
  - Bell list lines for several notification types (`admin_alert`, `buddy_request`, `buddy_accepted`, `buddy_message`,
    `goal_reminder`, `prayer_request`, `prayer_replied`): the bell items exist, the list needs a generic line for each.
  - An unread dot on the Buddy Messages tab (`buddy.messaging.unread_count(user)` exists but is not wired).
  - Beyond the list above, some services, views and forms are not built yet: features are being added on top of
    the models, one app at a time.

Reference docs (the source of truth for behaviour):
- `docs/schema.sql`: original SQL design, with comments on every rule
- `docs/flows.md`: the feature flows (login, attendance, tithes, buddy, ...)
- `docs/verify.sql`: read-only checks to run in pgAdmin

## Tech stack
Python 3.12, Django 5.1+ (tested on 5.2 and 6.0), PostgreSQL 16, Tailwind CSS
(standalone CLI), Alpine.js, Docker Compose. Outgoing HTTP (the ESV API) uses the
standard library, so there is no extra Python package for it (Web Push adds `pywebpush`, see Push notifications).

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
  `sidebar.html`, `sidebar_rail.html`, `sidebar_utilities.html`, `nav_link.html`, `section_tabs.html`, `brand_mark.html`, `topbar.html`, `bottom_nav.html`, `messages.html`, `modal.html`, `breadcrumb.html`).
  Page templates that belong to one app go in that app's `templates/<app>/` folder
  (e.g. `apps/pages/templates/pages/landing.html`, `apps/accounts/templates/accounts/login.html`,
  `apps/bible/templates/bible/chapter.html`).
- `static/`: `css/input.css` (Tailwind source and design tokens), `css/themes.css`
  (dark overrides), `css/output.css` (generated, not committed), `fonts/`,
  `icons/sprite.svg`, `images/` (logos), `js/alpine.min.js`, `js/ui.js` (toast and
  confirm store), `js/goals.js` (goal check-ins), `js/buddy_chat.js` (Buddy messages).
- `theme/` (Tailwind CLI binary, gitignored), `docs/`.
- `production/` (`docker-compose.prod.yml`, `deploy/scheduler.py`): production only; see Nightly jobs.
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
docker compose exec web python manage.py cleanup_first_timer_guests          # one-off: dry run; add --apply to delete guests left by never-uploaded drafts
docker compose exec web python manage.py test apps.core.test_text -v 2
docker compose exec web python manage.py test apps.bible
docker compose exec web python manage.py test apps.faith apps.buddy apps.prayer
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
  `theming` (it registers the signal). Use `faith.apps.FaithConfig` for `faith` (`ready()` connects the chapter-read listener).
- `MIDDLEWARE` includes the faith daily-open middleware (`apps.faith.middleware`, one line, after authentication).
- `TIME_ZONE = 'Asia/Manila'`, `USE_TZ = True`.
- Plain `date` columns (check-ins, streaks, daily verse, attendance, chapter reads) use the
  Manila calendar date.
- `ESV_API_KEY` (settings, from `.env`; empty means "ESV not set up"). The `web` service in
  `docker-compose.yml` must receive it (`env_file: .env` or an `environment:` entry) and
  `.env.example` must list it. Keep it secret: Crossway forbids sharing or publishing it.

## Apps, tables, and where logic belongs

| App | Tables | Logic that lives here |
|---|---|---|
| `core` | `site_setting`, `retention_policy`, `submission_token` | Abstract bases (`TimestampedModel`, `ArchivableModel`), `archived_matches_status()`, generic archive / restore / force-delete, upload validation and storage helpers (`uploads.py`), nightly `purge_archived`, permission hierarchy helpers, retention screen, job-setting readers (`job_settings.py`). `site_setting` also holds the Bible switches and ESV state (keys `bible_api_enabled`, `bible_esv_enabled`, `bible_esv_state`, `bible_esv_cache_lock`) |
| `audit` | `audit_log` | `log_action()` in `audit/services.py` (strips sensitive keys, sends `action_logged` from `audit/signals.py`), log viewer, `cleanup_audit_log` |
| `accounts` | `extension`, `app_user`, `special_role`, `user_special_role`, `extension_special_role_limit`, `default_password`, `email_token`, `user_extension_history` | Login/logout/forced password change, forgot + reset password, email verification, profile, create/manage accounts, extensions, special roles and limits, default passwords, `archive_inactive_users`, `auto_transfer_extension`, `cleanup_email_tokens`, `mark_attended()` |
| `theming` | `theme`, `theme_palette`, `user_settings` | Theme CRUD (both palettes in one transaction), contrast warning (including `highlight` / `on_highlight`), CSS generation + cache, context processor; `UserSettings` row created by `signals.py` |
| `pages` | `church_content`, `landing_image`, `donation_account`, `jil_video` | Landing page (`get_landing_context()` in `pages/services.py`, a default for every block) and terms editing, donation page, `fetch_jil_videos` |
| `bible` | `bible_verse`, `bible_esv_cache`, `daily_verse`, `bible_chapter_read`, `bible_bookmark` | Reader (KJV, Ang Dating Biblia, ESV), `import_bible`, ESV client with limits, locks and cache, version picker, daily verse (`daily_verse.py`, `seed_daily_verses`), `record_chapter_read()`, `finish_chapter()`, bookmark |
| `faith` | `goal`, `goal_checkin`, `user_daily_activity`, `user_streak` | Goals, check-ins, schedule rules (`schedule.py`), streak engine (`streaks.py`), stats, read-only overview pictures (`overview.py`), partner view (`partner.py`), daily-open middleware, chapter-read listener, `complete_ended_goals`, `send_goal_reminders` |
| `events` | `event` | Event CRUD, change/cancel notices, `send_event_reminders` |
| `resources` | `monthly_theme`, `monthly_resource`, `weekly_powerpoint`, `general_resource` | Resource permissions (`permissions.py`) and transactional create/edit/publish/archive/restore/delete services (`services.py`); a month with its cover picture, resource pictures and weekly presentations, plus standalone general resources |
| `attendance` | `attendance`, `attendance_member`, `attendance_first_timer` | Sheets, draft services (ticks, first timers, search), summary, upload (freeze counts, update `last_attended_at`), reopen (admin) |
| `tithes` | `tithes_offering`, `tithe_entry`, `tithe_entry_giver`, `tithe_household`, `tithe_household_member`, `tithes_bypass_request` | Entries and co-givers, live totals, denomination count, finalize and bypass flow, reopen, households, print page, "My tithes" (see Tithes) |
| `buddy` | `user_buddy`, `buddy_request`, `buddy_note` | Request/accept (transactional one-partner check), messages, nudges and "prayed" notes (`messaging.py`), end partnership, view partner goals, `close_stale_end_requests` |
| `prayer` | `prayer_request`, `prayer_reply` | Send, inbox (coordinator scope, anonymous masking), reply, mark as done (sends the default reply). There is no delete route |
| `notifications` | `notification`, `notification_preference`, `notification_rule` | `notify()`, bell + list, preferences, the admin-alert receiver (`notifications/listeners.py`, applies `notification_rule` to `action_logged`), `cleanup_notifications` |

Before adding a model, put it in the app whose data it shares a lifecycle
with. Only create a new app if the answer is "none".

### Resources
- **Shape:** a month (`MonthlyTheme`) holds everything for that month: an optional cover picture, its resource
  pictures (`MonthlyResource`) and its weekly presentations (`WeeklyPowerpoint.monthly_theme`). General resources
  stay standalone (no month). Management is admin and super-admin only, shown as the "Resources" tab of the Website
  hub; its switcher has two entries, Monthly themes and General resources. Weekly presentations have no switcher
  entry: they are added, edited and archived on the month page (`/resources/manage/weekly/` stays as the full index,
  for archived and not-yet-assigned rows). Every mutating function in `apps/resources/services.py` calls
  `require_resource_manager`, runs inside `transaction.atomic()`, and logs the change through `audit.log_action()`.
- A monthly theme is dated on the first of a month and unique for that month. Only active, published themes and their
  pictures are visible to coordinators and members; managers can inspect drafts and archived records
  (`can_view_monthly_theme` in `views.py`). Coordinators can also see a month's active weekly presentations and the
  general resources; members cannot.
- **Cover picture:** `cover_image` + `cover_original_filename` on `monthly_theme` (JPG, PNG, WebP, `PHOTO_UPLOAD_MAX_MB`).
  It is stored in PRIVATE storage like every resource file and served inline by `theme_cover`
  (`/resources/months/<id>/cover/`), so a draft month never leaks its cover. Create/edit forms can set, replace
  (`cover`) or clear it (`remove_cover`); a new upload wins over remove. `MonthlyTheme.has_cover` tells the templates.
  Showing the cover on the public landing page is NOT built (it would need a public copy).
- **Resource pictures:** `MonthlyResource` rows are now mainly pictures (`kind`: background, logo, wallpaper, other).
  `validate_monthly_file` accepts a picture OR a document, so rows from before pictures stay valid.
  `MonthlyResource.is_image` drives thumbnails; pictures are served inline by `monthly_resource_image`
  (`/resources/monthly-files/<id>/image/`, 404 for documents) and any row can be downloaded through
  `monthly_resource_download`. Adding: `create_monthly_resources` / `monthly_resource_bulk_create`
  (`manage/themes/<id>/files/upload/`, POST, up to 20 pictures per upload, one transaction per file, the title comes
  from the file name, bad files are reported and never block the rest). Edit and delete one picture keep the existing
  screens; delete removes the row and its file (there is no archive for pictures).
- **Weekly presentations:** each has one required file and, from the forms, a REQUIRED month (the column stays
  nullable for old rows). `weekly_create` accepts `?theme=<id>` to start inside a month; saving returns to that month.
  The archive button on a month page posts `from_theme=1` so it returns there.
- Audit entries contain record ids and changed field names, never file names or contents.
- The management screens are under `/resources/manage/` in the `resources` namespace. Admin and super-admin share the
  screens; only the super-admin can permanently delete archived records. Mutations are POST-only, use the global
  confirmation dialog, and report results through Django messages/toasts.
- Templates live in `apps/resources/templates/resources/manage/` (month grid `theme_list.html`, month page
  `theme_detail.html`, `theme_form.html`, `resource_form.html`, the two lists). `resources/_cover.html` is the shared
  cover-or-placeholder include. The library is at `/resources/`: published months as cover cards for every role, plus
  general resources for coordinators and above; `/resources/months/<id>/` (`resources:month`, `month.html`) shows one
  month's pictures and, for coordinators and above, its weekly presentations. Every document download uses a
  permission-checked view and the original filename; resource storage has no public URL.
- Run `apps.resources.tests.test_permissions`, `apps.resources.tests.test_services`, `apps.resources.tests.test_views`,
  and `apps.resources.tests.test_downloads` when changing these rules or screens.

### Landing management
- Admin and super-admin manage landing text under `/manage/landing/` (the "Landing" tab of the Website hub).
  `apps/pages/views.py` remains the public landing view; management logic is in
  `manage_services.py`, `manage_views.py`, and `manage_urls.py`.
- Editable English keys are `landing_description`, `mission`, `vision`,
  `core_values`, and `donation_instructions`. Empty text deletes the English row
  so the landing loader uses its default; donation instructions default to empty.
- Text is rendered escaped by Django, with line breaks preserved. Core values
  are entered one per line. Audits record the content key and version/state only,
  never the submitted text. The landing loader's language fallback is unchanged.
- Landing photos have separate `hero` and `gallery` placements, each with its own
  active list, ordering, and fallback defaults. Existing rows migrate to `hero`;
  active photos are capped at 12 per placement. Upload, replace, toggle, and
  delete ask for confirmation; ordering applies immediately.
- Run `apps.pages.tests.test_manage_content` and `apps.pages.tests.test_landing`
  and `apps.pages.tests.test_manage_images` when changing landing management.

### Donation accounts and footer links
- Admin and super-admin manage both under `/manage/landing/` (namespace `pages:pages_manage`):
  `donation-accounts/` and `footer-links/`. In the Website hub the donation accounts are the "Give page" tab (it also
  owns the public `/give/` page) and are NOT part of the "Landing" tab, even though the path sits under `/manage/landing/`:
  the Give page tab matches the longer path, so it wins. Same two guards as the rest of landing management:
  `landing_manager_required` on the view, `require_landing_manager` at the top of every service.
- **Donation accounts** are public giving details only (GCash, Maya, a bank): `payment_app`, account
  name, account number, optional SWIFT code, a note (`instructions`), an optional QR image, an
  `is_active` flag and `sort_order`. There is no login, verification or limit for visitors. The
  active flag only lets an admin hide an outdated account; with no active account the public page
  shows "Giving details have not been published yet" and never invents text. Never store a PIN,
  password or card number there. QR images are public photos (`donation_qr/` under `MEDIA_ROOT`);
  a replaced or deleted image is removed after commit by `FileCleanupModel`. Create, edit, hide or
  show, and delete each ask for confirmation, then show a toast. Audit rows (`pages.donation_account_*`)
  carry before and after values for the public fields, `changed_fields`, and only `qr_image_set`
  for the image, never a file name or content. An edit that leaves the QR alone skips validating that
  file (it would read its size from disk).
- **Footer links** are the "More" list of the landing footer: one JSON list `[{id, label, url}]` in
  the `site_setting` row `pages_footer_links`, handled only by `apps/pages/site_settings.py`
  (`get_footer_links`, `replace_footer_links`, `reset_footer_links`, `validate_footer_url`).
  No row means the built-in `FOOTER["more"]` links (given ids like `default-privacy-policy`); a row
  means exactly that list, even an empty one; Reset deletes the row. Saving the list that already
  applies changes nothing and writes no audit row. Up and down apply immediately; add, edit, delete
  and reset ask first. At most 20 links; labels up to 100 characters.
- **URL rule:** `http`/`https` with a host, `mailto:` or `tel:` with a value, a path starting with a
  single `/`, or a `#anchor`. Spaces, control characters and backslashes are refused (browsers
  ignore a tab inside `java\tscript:`), as are `javascript:`, `data:`, `//host` and bare
  `example.com`. A stored row that fails the rule is ignored and the defaults are shown. Audit rows
  (`pages.footer_links_saved`, `pages.footer_links_reset`) hold the label and address lists.
- Footer addresses, phones, affiliates and social links stay in `FOOTER` in `pages/services.py`
  (not managed yet). The page copy of `FOOTER` is built by `_footer_with_managed_links()`.
- Templates: `apps/pages/templates/pages/manage/` (`donation_accounts`, `donation_account_form`,
  `footer_links`, `footer_link_form`). Run `apps.pages.tests.test_manage_donation` and
  `apps.pages.tests.test_manage_footer` (shared helpers in `apps/pages/tests/helpers.py`) when
  changing them, and `make tailwind-build` after template changes.

### Donation page and About
- `/give/` (`pages:give`, `give_view`) is public: no login, no form, no amount field, no database write
  (POST gives 405). It shows the managed `donation_instructions` text (English, escaped, line breaks kept)
  and the active `DonationAccount` rows (`_donation_accounts.html`). No instructions saved: only the
  accounts. No active account: the "Giving details have not been published yet" note. Nothing is invented.
  Context comes from `get_give_context()` in `pages/services.py`.
- The landing page no longer holds giving details. Its `#give` block is an invitation with a button to
  `/give/`; the `#about` and `#give` anchors still exist. Giving instructions and accounts are managed from
  the "Give page" tab of the Website hub (`donation_accounts.html`, reached from Manage > Website), not from the "Landing" tab.
- `/about/` (`pages:about`, `about_view`) is in-app for every role (`role_required` with all four roles). It
  shows mission, vision and core values from `get_about_context()` (managed content, else `DEFAULT_ABOUT`; core
  values are one per line) plus a Give section that links to `/give/`. About is its own link in the account menu in `topbar.html` (next to Settings), not part of Settings and not in `NAV_ITEMS`.
- The public navbar links to `{{ landing_url }}#about` and the real `/give/` URL, and shows "Open app" instead
  of "Login" for a signed-in visitor. The shared footer is in normal flow and links to `/give/`.
- Run `apps.pages.tests.test_give` and `apps.pages.tests.test_about` when changing these pages.

### Dependency rules
1. Direction: `core` <- `audit` <- `accounts` <- everything else. `theming`
   depends on `accounts`; `tithes` depends on `attendance`; `attendance` depends
   on `events`; `bible` uses `core` (`site_setting`) and `accounts` (`role_required`, roles).
   `faith` reads `bible` only through a signal. `buddy` reads `faith` (`faith.partner`, `faith.streaks`; the streak import is
   guarded) and never the other way. `dashboards` imports `apps.faith.overview` and `faith.permissions.can_use_goals`
   (read-only). `faith` and `buddy` read job settings from `core/job_settings.py`, never from `accounts`.
2. `audit` and `notifications` never import feature apps. They refer to other
   records with `entity_type` / `entity_id`, not foreign keys.
3. Cross-app reactions use signals, not imports upward:
   `audit.log_action()` sends `action_logged` and `notifications` listens to
   apply `notification_rule`; the daily open streak is recorded inside `faith` (`faith/middleware.py`, which skips
   polling requests that send `Accept: application/json`); `bible` sends `bible.signals.chapter_read`
   and `faith` listens to it for the Bible streak (`bible` never imports `faith`):
   `faith.listeners.on_chapter_read` is connected in `FaithConfig.ready()` (with a `dispatch_uid`) and calls
   `streaks.record_read(entry.user, entry.read_date)`.
4. Each app owns its jobs as management commands. Business logic goes in
   `services.py`, wrapped in `transaction.atomic()`, calling `log_action()` and
   `notify()` in the same transaction. Views stay thin.

## Model conventions
- Every model sets `db_table` to the schema table name. The purge trigger
  matches `retention_policy.entity_type` to the table name, so a new archivable
  table needs: `db_table`, a `retention_policy` row, a trigger + partial index
  (add to a new migration, copy the pattern in `core/migrations/0002`).
- **Retention rule (core migration 0002):** `goal` (180 days) and `prayer_request` (90 days) already have
  `retention_policy` rows. The `set_purge_at()` trigger runs `BEFORE INSERT OR UPDATE OF archived_at` and returns early when
  `archived_at` did not change, so a changed period applies ONLY to records archived from now on. Records already archived
  keep their old `purge_at`. The Retention screen says so. (`notification` and `audit_log` are not archivable; their own
  cleanup commands read the policy when they run.)
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
  SET NULL; extensions with people or records RESTRICT. A constraint that ties a
  nullable `SET_NULL` column to another column must be one-way (see `user_buddy_end_request_time`), or deleting the
  referenced user violates it.
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
  fields on `app_user`, or the `user_buddy` end-request columns and `buddy_note.kind`) is added there too.

## Roles and routing
- Four roles: super-admin, admin, coordinator, member. Role is stored in
  `User.role`; do not infer it from `is_staff` or `is_superuser`.
- `is_staff` / `is_superuser` are reserved for the super-admin, since they
  control access to Django's built-in admin.
- Django's built-in admin is mounted at `/django-admin/`. `/admin/` is the
  custom Admin role area, not Django admin. Never "fix" this by moving
  Django admin back to `/admin/`.
- Route groups: `/superadmin/`, `/admin/`, `/coordinator/`, and member
  pages at the root (the Bible is at `/bible/`, open to all four roles; goals at `/goals/`, Buddy at `/buddy/`, prayer
  requests at `/prayer/`). Each group has its own urls module and URL
  namespace: `superadmin`, `admin_area` and `coordinator_area`. Never name one `admin`: that is Django's own admin.
- Every view in a role group must be protected with `role_required(...)`.
  Hiding a link in a template is not access control.
- Coordinator-scoped data must be fetched through the `for_user(user)`
  queryset helper, not with ad hoc `.filter(extension=...)` calls in views. For people this is
  `User.objects.for_user(actor)` (see People screens for admin and coordinator); for attendance it is
  `Attendance.objects.for_user(actor)` (see Attendance); for tithes it is `TithesOffering.objects.for_user(actor)` (see Tithes);
  for prayer it is `PrayerRequest.objects.mine(user)` and `.inbox_for(actor)` (see Prayer).
- Use `{% url 'admin:index' %}` to link to Django admin, never a hardcoded
  path.

## Behaviour rules agents must not break
- **Account id:** `<counter from account_number_seq><year, Manila>`, generated
  in `User.save()`. Never reused, never edited. `bulk_create()` skips it, so
  create users one by one.
- **Special-role limits** are enforced in a transaction with the extension row
  locked. An extension with no limit row uses the default of 2 per role. A limit can never be below 2
- **Uploaded attendance and finalized tithes reports** are locked; only an admin reopens them
  (logged and alerts admins). Force delete is super admin only, archived rows
  only, logged before deleting; delete files from disk first.
- **Privacy:** never write passwords, tokens, goal text, notes, prayer text or
  tithe amounts, bypass messages, decision notes, bypass reasons, chat messages to `audit_log` or `notification.data`. Notifications say that
  something happened; the linked page does the permission check. Anonymous
  prayer requests hide the sender in UI/API but keep `user_id` (never rendered). Two push-only exceptions
  show a little text in the push body and nowhere else: `goal_reminder` (the goal title) and `buddy_message`
  (the sender name and about the first 40 characters).
- **Enumeration:** forgot-password always shows the same message.
- **Licensed Bible text (ESV):** never store it anywhere except `bible_esv_cache` (at most 450 verses in total).
  Not in `bible_verse` (a database constraint refuses it), not in `daily_verse.verse_text`, not in logs, not in
  `audit_log`. Every page that shows ESV text carries the copyright notice, "(ESV)" and a link to www.esv.org.
- **Tests never call outside services.** Mock `apps.bible.esv.esv_client.fetch_chapter`; tests that depend on ESV
  being locked or open set `ESV_API_KEY` with `override_settings`, so a real key in `.env` never changes them.

## Accounts: login, status, password change, profile
- **Type vs status vs role are three separate things.** `User.person_type` (`accounts.models.PersonType`, next to `Gender` and
  `AgeNetwork`): `guest` (a non-member who has attended), `member_record` (shown as "Member (no account)": a member whose
  attendance and tithes are managed but who has no sign-in) and `member_account` (shown as "Member": full access; the default).
  `role` still means the four roles; a database check (`app_user_non_account_is_member`) says only `member` can be a guest or
  a no-account member. `status` is `active`, `suspended` or `archived`. "Needs password change" is the `must_change_password`
  flag, shown as a tag (`User.tag_codes`, see People screens), never a status. "Deactivated" and "Not activated" no longer exist anywhere.
  Tithes accept only `member_record` and `member_account`, never guests.
- **Account id is generated for every person** in `User.save()`, guests and no-account members included (the login field stays
  non-null). Never reused, never edited. Guests and no-account members get `set_unusable_password()` (`UserManager.create_user`,
  `_set_initial_password` returns `PasswordInfo("none")`), and a reset refuses them.
- **Who can sign in:** ONE answer, `User.can_sign_in` (member_account, not archived, not suspended). `User.is_active` returns it,
  so every check built on `is_active` (`permissions._usable`, attendance `_signed_in` / `_can_sign_in`, Django sessions) also
  looks at the type. Never write a fresh "active" test from `status` alone. A guest or no-account member never holds a seat, a
  special role (`assign_special_role` refuses them; `member_counts` leaves them out), a buddy, goals, prayer requests or a
  notification. Only a leader changes a type: guest to `member_record` (`promote_guest`), `member_record` to `member_account`
  (`grant_account`, or approval in brick 4). Both change the EXISTING row; never create a second row for the same person.
- **Login:** account id + password, or a *verified* email. Wrong account and
  wrong password show the same message. Log failures. Archived and suspended accounts, guests and no-account members are
  denied; `must_change_password` users go to the change-password page.
- **Login implementation:** `AccountIdOrEmailBackend` (listed in
  `AUTHENTICATION_BACKENDS`) accepts an account id or a verified email and
  rejects archived and suspended users and anyone who is not a `member_account`; `accounts.services.attempt_login()` calls Django's
  `authenticate()` then `login()` (so `user_logged_in` fires), writes failures to
  `audit_log` (`account.login_failed`, status failed, reason code in `after`; never the
  password or the typed identifier)
  and reports whether a password change is needed (`needs_password_change`). Every failure shows
  `forms.GENERIC_LOGIN_ERROR`. `next` is only followed if
  `url_has_allowed_host_and_scheme` passes. Post-login landing URLs are in
  `decorators.ROLE_HOME_NAMES` (the `dashboards` routes). Brute-force
  throttling is not built yet.
- **Status:** `active`, `suspended` (a reversible login block set by the super-admin or an admin; never purged; `User.is_active`
  is false, so open sessions end at once) and `archived`. "Inactive for a year" is an automatic archive with
  `archive_reason='inactive'`. Restore and unsuspend ALWAYS go back to `active` (`_usable_status` is gone); a person who still
  has `must_change_password` keeps the flag and is sent to the change-password page at the next sign-in.
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
- **Password change:** `accounts/services/password_change.py` (`user_needs_password_change`, `complete_password_change`), form
  `SetNewPasswordForm` (not `ChangePasswordForm`, which is the profile page), view `change_password_view`, template
  `accounts/change_password.html`, route `/accounts/change-password/` (`accounts:change_password`). A person must change their
  password when `must_change_password=True`. It is set on a leader-created account, on a reset and (brick 4) on approval. A reset
  NO LONGER touches `status`. `complete_password_change` locks the row, sets the password, clears the flag, re-signs the session
  (`update_session_auth_hash`) and logs `account.password_set` (flag logged as `needs_change`, before False/after: the key
  must not contain "password" or `log_action` drops it). It is idempotent; suspended, archived and non-`member_account` people
  are left alone. `PasswordChangeRequiredMiddleware` (`accounts/middleware.py`, after `MessageMiddleware`) redirects everything
  except `accounts:change_password` and `accounts:logout` while the flag is set. Tests that sign a user in create them with
  `must_change_password=False` and `person_type="member_account"`, or the middleware redirects every request. No
  `notification_rule` row used the old action names, so there was nothing to migrate there.
- **Registration and approvals:** `RegistrationRequest` (`registration_request`) stores public requests. Names are normalized with `core.text.clean_text`, phone digits with the accounts contact helper, and addresses are checked with `accounts.address.check_address`. Request personal data (name, birth date, email, phone, address) is NEVER written to `audit_log`, `notification.data` or logs; audit data contains ids/counts only. `/accounts/register/` is public, `never_cache`, rejects signed-in users and uses a honeypot, one-time `submission_token` and per-IP throttle. Suspended exact matches and any `member_account` match receive the same neutral refusal. Guest and unmatched requests remain pending. A matching `member_record` is auto-approved only when `site_setting.registration_auto_approve` is on and a non-console email backend is configured; otherwise it stays pending. The switch is super-admin-only and warns that anyone who knows the member's name and birth date can claim the record. Pending same-identity/same-email repeats report the duplicate message; corrected emails join the same possible-duplicate group, capped at three pending requests per name and birth date. `registration_requests_for(actor)` is the only approval scope: admins/super-admins see all; coordinators see their extension. Approval creates or grants a `member_account` with the requested extension, default MEMBER password and `must_change_password=True`, and closes other pending same-identity requests as `duplicate`. One email sends Account ID, sign-in URL and temporary password. If email fails, a show-once page presents the credentials. Passwords never enter audit, notification, logs, request rows or session. `cleanup_registration_requests` nightly deletes decided requests after `REGISTRATION_REQUEST_RETENTION_DAYS` (default 90).
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
  an extension can be archived while people still belong to it (the coordinator seat is cleared, the people keep their link);
  it cannot be archived while it is the main church; only archived extensions can be deleted, and never while any person
  (archived ones included) still belongs to it.
- **Archived extensions with people:** archiving does not need an empty extension. Members keep their `extension` link, the
  coordinator seat is cleared, and the audit row records `active_people` and the old `coordinator_id`. Permanent deletion
  (`force_delete_extension`, `bulk_delete_extensions` and the nightly `purge_archived`) is refused or skipped while ANY person
  row, archived ones included, still points at the extension; they must be moved out first. The detail page shows the
  "will be permanently deleted on" date only when nobody belongs to the extension.
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
  `must_change_password` silently disappears. Log it under another name (for example `needs_change`).
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
- **Guards:** you cannot archive, suspend, reset, delete or change the role of yourself; the last active
  super-admin cannot be archived, suspended or demoted (checked under a lock on all super-admin rows). Who may
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
  sessions (the hash changes). "Skip password change" on create (`skip_password_change`) is for test accounts that should not go through the change-password
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
    unsuspend). Never force delete; never super-admins or other admins.
  - Coordinator: acts on members of their OWN extension only, of every type (create, edit details, reset password,
    archive, restore, promote a guest, give a no-account member an account). No role or extension change, no suspend,
    no delete, no typed passwords. (Brick 1 had dropped archive and restore from `_ACTOR_RULES`; brick 2 puts them back.)
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
- **Tags and types on the People screens (brick 2):** `User.tag_codes` gives the tags in order (Needs password change, then
  Guest / Member (no account) / Member, then Suspended or Archived); `{% person_tags person %}`
  (`people_tags.TAG_LABELS`, template `_person_tags.html`) draws them. "Needs password change" is only for people who can sign
  in. Every badge carries its words, so colour is never the only signal. `User.is_blocked` is true for a suspended person. The
  People list has a Type filter (`person_type`): blank means Members (`member_account`), then Members (no account), Guests, All
  types. Anyone out of the actor's reach stays out whatever the filter says (`for_user` runs first).
- **Guests and no-account members screen:** `/superadmin/guests/`, `/admin/guests/`, `/coordinator/guests/` (`guest_list`,
  template `guest_list.html`, `GuestFilterForm`). It lists `NO_ACCOUNT_TYPES` through `for_user` (a coordinator sees their own
  extension only) with search, a type, status and extension filter, and per-row buttons from `available_actions`. Rows open the
  normal detail page (`user_detail`), which serves every type. Rows have checkboxes and a bulk bar (`guestSelect()`
  in the template, buttons from `_guest_bulk_buttons`: promote, suspend, unsuspend, restore, archive; no reset password or
  delete). It posts to the same `user_bulk` view and `run_bulk` service as the People list (`promote` is a bulk action next to
  the others; people it does not apply to are skipped with a reason) with `back=guests`, a whitelisted flag that returns to
  the guests screen.
- **Promote and give an account:** `PROMOTE` (`promote_guest`, route `users/<pk>/promote/`) turns a guest into a
  `member_record`; `GRANT_ACCOUNT` (`grant_account`, route `users/<pk>/grant-account/`) turns a `member_record` into a
  `member_account` with `must_change_password=True` and shows the password once (same rules as a new account: typed by an admin,
  else the actor's default, else generated; one-time token). Both change the EXISTING row (same Account ID and history),
  only for an ACTIVE person (a suspended person is unsuspended first, an archived one restored first), lock the person row, and
  log `account.promote` / `account.grant_account` (type before and after, never a password). Offering a button needs the
  type and status (`available_actions`); a guest is never offered Reset password. Suspend and unsuspend work on guests and
  no-account members for the super-admin and admin; a suspended person is blocked (brick 3 stops them being ticked on a sheet,
  brick 4 stops them registering). Guests and no-account members are archived by the inactivity job like everyone else (brick 4
  makes sure the job includes them) and purged by the existing retention trigger.
- **Special roles** (`services/special_roles.py`, `views/special_roles.py`): `tithes_offering` and `attendance`. The
  super-admin and admins (any extension) and a coordinator (their own) give or take them on a member's detail page. Only a
  member who can sign in (not archived, not suspended) can hold one. `assign_special_role` locks the extension row, then
  the person, and refuses when the limit is full (no `extension_special_role_limit` row means the default of 2). Limits are set per
  extension on `/admin/special-roles/` and `/superadmin/special-roles/` (super-admin and admin only,
  `set_special_role_limits`, all or nothing; a limit is never below 2 or below the current holders, and an empty value
  resets it to 2). Archiving or moving a person clears their roles (`_clear_special_roles`). Audit actions:
  `special_role.assign`, `special_role.remove`, `special_role.limit`.
- **Default passwords:** personal to the super-admin and each admin, one per role beneath them (super-admin: admin,
  coordinator, member; admin: coordinator, member); a coordinator keeps none. The password a person gets depends on WHO
  creates or resets them (`services.users.default_password_row`): the super-admin their own (the global default); an admin
  their own, else the global one (`global_default_row`: the most recently changed row of an active super-admin); a
  coordinator the global MEMBER one only. No default means a generated password. Only the super-admin and admin may type a
  password or skip the password change (`can_type_password`); a coordinator's new members always get the global member password
  and must change it at first sign-in. Screen: `views/default_passwords.py` with
  `accounts/people/default_passwords.html` (super-admin and admin; an admin with none of their own for a role sees that the
  global one is used). A coordinator has no screen: the "Default password" button on their people list POSTs to
  `coordinator_area:default_password_show` (`reveal_default_password`), which shows the global member password read-only in
  the show-once dialog and writes the audit row `default_password.view` (who looked, never the password). Each default is
  stored as a hash plus an encrypted copy (`DefaultPassword.password_encrypted`, key `DEFAULT_PASSWORD_KEY`).

## Attendance
Bricks 1 to 14 and the attendance integration for the Access revamp are implemented. In addition to the sheet workflow,
guest promotion and "My attendance":
- **Repeat visitors (brick 11):** after three distinct uploaded sheets at an extension, an active member whose home extension differs is transferred there. A draft or a single visit never changes their extension. The transfer is performed by `accounts.services.users.auto_transfer_visitor`, updates `user_extension_history` with reason `auto_transfer`, clears extension-specific roles, and logs extension ids only.
- **Account reset (brick 12):** a reset sets `must_change_password=True` (the status is left as it is); the account id remains the sign-in identifier and a password change is required on the next login. The password hash change ends existing sessions.
- **My attendance (brick 14):** own uploaded-sheet history only, newest first, with no user id accepted from the request. The first-visit row appears once its sheet is uploaded.

The source plan `docs/attendance.md` is not present in this checkout; the defaults above are the implemented acceptance assumptions.
- **Models** (`attendance/models.py`): `Attendance` (one sheet per extension, date and label; `taken_by`, `uploaded_by`,
  frozen `summary` JSON that is NULL while a draft; `is_locked` is true when uploaded), `AttendanceMember` (a tick, with a
  SNAPSHOT of name, person type, gender, age network and home extension taken at tick time; gender and age network may be blank
  until the taker fills them in), `AttendanceFirstTimer` (first, middle, last name, suffix, required gender and age network,
  optional phone and birth date, linked guest, and converted person; names stored lowercase and cleaned in `save()`, shown with
  `full_name`).
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
  - `search_people` is a DELIBERATE exception to `for_user` (a visitor belongs to another extension): it searches active people
    of every type, needs the right to edit the draft, asks for 3+ characters (fewer returns `[]`), returns at most 20 people and
    only `id`, `name`, `home_extension` ("-" for an admin or super-admin) and `age_network`. Never birth date, phone or address.
  - `mark_present` returns `(row, created)`. The row snapshots name, person type, home extension, gender and age network (the age network as
    of the SHEET date). The person's own gender, birth date and stored age network win; what the taker supplies only fills
    blanks. A gender is stored on the row only. An age network is stored on the row AND on the person (when they have no birth
    date), and `set_member_category` also updates the person's stored age network when the taker changes it, so nobody is asked
    every Sunday. A row with a missing category is saved blank and flagged on the screen; the upload refuses blanks.
    A second tick of the same person changes nothing.
  - `add_first_timer` saves the first-timer ROW only, after the "same person?" check is answered as new. No person record
    exists while the sheet is a draft, so a first timer never shows in the Guests list, the member list, `search_people` or
    `roster_people` until the sheet is uploaded (see "When the guest is created"). Its `AddFirstTimerResult` (`first_timer` or
    `matches`, `needs_confirmation`) works: when people with the
    same name exist and `confirm_new` is false, nothing is saved and the matches come back for the "same person?" prompt. A birth
    date, when given, DECIDES the age network. The same first timer twice on one sheet is refused unless birth dates differ.
    An earlier unconverted first timer of the same extension with the same name counts like an existing person: without
    `confirm_new` nothing is saved and the result carries `previous_visits` (see First timer to member).
  - The "same person?" match for an active guest marks that existing row present instead of making another person. `mark_present`
    refuses a person who is already linked as a first timer on that sheet (they
    would count twice). `remove_first_timer` can remove a converted row (only that visit is deleted, never the promoted person).
  - **When the guest is created:** `upload_sheet` creates one guest (`_create_guest_for_row`: `person_type=guest`, the sheet's
    extension, an `INITIAL` extension history row) for every first-timer row that has none, inside the upload transaction, so a
    failed upload creates nobody. A row that already has a person (reopened sheet, promoted first timer) is skipped, so a second
    upload never makes a second guest. `convert_first_timer` does the same for a draft row when a leader promotes it early.
    `remove_first_timer` deletes the row and, when the visit already had a guest (after a reopen, or an older row), deletes that
    guest too through `_discard_unused_guest`, but only an ACTIVE guest that no other first-timer row, tick or conversion uses
    (a promoted, suspended or archived person is kept, and so is a guest a database protection still refers to). Edits to a first
    timer on a draft reach the guest because it is built from the row at upload; after a reopen the existing guest is not updated.
  - Ticks, first timers and `taken_by` changes are working data and are NOT audited.
- **Duplicate-name rule** (`accounts.services.users.find_duplicate_people`, one rule for user create and edit and for the
  attendance "same person?" prompt; `find_duplicate_person` returns its first result). Same first name, last name and suffix;
  archived people count; a match is dropped only when both sides have a birth date and they differ. `middle_name=None` means
  any middle name (used by attendance).
- **Summary** (`attendance/summary.py`): `build_summary(sheet)` counts the STORED rows only (never current birth dates) with grouped
  queries and returns a `SheetSummary` (`summary` JSON for `Attendance.summary`, `members_present`,
  `first_timers_count`, `total_present`, `incomplete`). It serves the live preview on a draft and the value frozen at upload, so
  they cannot disagree. `summary_for(sheet)` returns the frozen numbers for an uploaded sheet and counts live otherwise (draft or
  reopened); screens and the print page call it. `incomplete_rows(sheet)` lists member rows missing a gender or age network
  (the upload refuses while any exist). `summary_table(summary)` gives the display rows (Men and Women by the four age networks,
  for first timers, guests and members, with totals) for the screen and the print page. A row without a category counts in
  `members_present` but in no cell; upload refuses incomplete rows, so a frozen summary always adds up.
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
- **Archive and force delete** (`archive_sheet(actor, sheet_id, *, request=None)` and `force_delete_sheet(actor, sheet_id, *,
  request=None)` in `attendance/services.py`; rules `can_archive` and `can_force_delete` in `attendance/permissions.py`).
  Archive: admin and super-admin on a draft or uploaded sheet; a coordinator on a DRAFT of their own extension only; special
  members never. It sets `status` and `archived_at` together (`save(update_fields=[...])`, so the purge trigger sets `purge_at`),
  keeps the frozen counts, and the sheet leaves the dashboards and the repeat-visitor count (uploaded sheets only).
  `last_attended_at` is NOT rolled back. Force delete: admin and super-admin on an ARCHIVED sheet only (archive first); this
  is the one place where an admin, not only the super-admin, may force delete. It writes the audit row first, then deletes the
  tick and first-timer rows and the sheet through the ORM; people are never deleted (a draft's first timers never had a person record, and the guests of an uploaded sheet stay).
  Both lock the sheet row, re-check permission on the locked row, and are idempotent (a repeat gives a friendly
  `ServiceError`), so they need no one-time token. Audit actions: `attendance.archive` (a draft, no alert),
  `attendance.archive_uploaded` and `attendance.force_delete`; they carry the old status and counts only (no names), and the
  last two have a `notification_rule` (core migration `0004_seed_attendance_archive_rules`, after `0003_submission_token`; never edit `0002_seed_and_triggers`) that alerts admins.
  **Restore** (`restore_sheet(actor, sheet_id, *, request=None)`, rule `can_restore`): admin and super-admin any archived
  sheet; a coordinator only an archived sheet of their own extension that was a DRAFT when archived (`uploaded_at` empty;
  a reopen clears it), so they can undo their own archive but never bring an uploaded sheet's numbers back. Archiving keeps
  `uploaded_at`, so a sheet returns as UPLOADED when it has one (its numbers count on the dashboards again) and as a DRAFT
  otherwise; `archived_at` is cleared in the same save (the purge trigger clears `purge_at`). Audit `attendance.restore`
  (status only, no alert).
  Views `sheet_archive`, `sheet_restore` and `sheet_force_delete` (POST, `sheet_action`; routes `archive/`, `restore/` and
  `force-delete/` under a sheet, names `sheet_archive`, `sheet_restore`, `sheet_force_delete`). A form on the list posts
  `next` (the list's own URL with its filters and page: `views._list_next` accepts only this area's list path, never another
  page or site) so the person stays on the same filters; from a sheet's own page Archive and Delete land on the list and
  Restore lands on the restored sheet. The sheet's own page uses `attendance/_sheet_danger_actions.html` (`data-confirm`
  forms, `.btn-danger`; Restore asks nothing), shown by the `can_archive`, `can_restore` and `can_force_delete` flags that
  `_danger_flags` adds to the detail, workspace and review contexts. Do not write a template tag inside an HTML comment in
  that partial: Django runs it.
  **Row menu on the sheet list** (`attendance/sheet_list.html`): each row with something allowed (`has_actions`, set with
  `can_archive`, `can_restore`, `can_force_delete` by `views._with_actions`) has a three-dots button and the same menu on
  right click or long press (Open, Open in new tab, then Restore, Archive, Delete permanently as allowed). ONE Alpine
  component on the `<ul>` holds the open menu (`openId`) and its position; menus are `position: fixed`, clamped into the
  viewport once they are displayed (`x-show` shows a tick late, so placement waits for it), and close on Escape (focus
  goes back to the button), Tab, an outside click, a scroll or a resize. Arrow keys move between items. Use the object form
  of `x-bind:style` there, because a string form would wipe the `display: none` that `x-show` sets. Rows without actions
  keep the browser's own menu. The sheet list already hides archived sheets by default (`status=current`); the Archived
  choice of the status filter shows them.
- **Guest promotion** (`convert_first_timer` in `attendance/services.py`): one-time token, lock the sheet and first-timer row,
  promote its linked guest in place through `accounts.services.users.promote_guest`, then set `converted_user` on every first-timer
  row linked to that same person. A first-timer row without a guest link (every row on a draft, and older rows) gets one guest record through `_create_guest_for_row` before promotion. The
  Account ID, person row, home extension and attendance history stay; no sign-in account, password, or attendance member row is
  created. The audit event carries ids only. `previous_first_timer_visits` lists earlier unpromoted visits in the same extension;
  `member_attendance_history(user)` reads uploaded sheets newest first, showing those linked first-timer rows as "First visit".
- **Promote screen** (`sheet_first_timer_convert` in `views.py`, route `sheets/<pk>/first-timers/convert/` in `area_urls.py`):
  a `sheet_action` POST renders a confirmation page with no credentials. The first timer may be on this sheet or an earlier
  one (the "visited before" prompt); with `mark_here` the promoted person is also ticked present on this draft. The convert
  button says "Promote to member". There is no 58 mm welcome slip or print-only conversion code; account creation elsewhere
  continues to use its existing show-once credential dialog.
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
  (people by name, then first timers by name) with Name, Belongs to, Men and Women by Adult, YA, Youth, C, a G guest column and
  an FT column, a tick in the cell of the person's stored gender and age network (a first timer gets their gender and age tick AND the FT tick),
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

## Tithes

Tithes and offering for one attendance sheet: the digital "Reports on Tithes & Offering" paper form.
Code: `apps/tithes/` (`models.py`, `permissions.py`, `services.py`, `views.py`, `forms.py`, `area_urls.py`,
`urls.py`, templates in `templates/tithes/`, tests in `tests/`). `tithes` depends on `attendance`;
`accounts`, `audit` and `notifications` never import it.

### Models (all money is `Decimal(12,2)`, never float)

- `tithes_offering` (`TithesOffering`): ONE per attendance sheet (two services in a day = two reports).
  `status` draft/finalized, `offering_amount`, `denomination_breakdown` (JSON, nine text keys 1000..1).
  Frozen at finalize and NULL while a draft: `tithers_count`, `tithes_total`, `total_amount`,
  `breakdown_total`, `difference` (SIGNED, breakdown_total - total_amount), `finalized_by`, `finalized_at`,
  `bypass_reason` (only for a DIRECT admin bypass), `bypassed_by`. Every `*_by` is SET NULL.
- `tithe_entry` (`TitheEntry`): one envelope. `user` (SET NULL) + `giver_name` snapshot, `amount` > 0,
  unique(report, user).
- `tithe_entry_giver` (`TitheEntryGiver`): co-givers copied onto an entry (name snapshot, unique(entry, user)).
- `tithe_household` / `tithe_household_member`: a standing, dated household link. It ONLY pre-fills the
  co-givers of a new entry; saving copies them onto the entry, so later changes never rewrite history.
  One active membership per person (partial unique index).
- `tithes_bypass_request`: the coordinator's **finalize request** ("bypass" is only the historical name of the table,
  the model and the audit actions). `message`, signed `difference` as requested (**0 when the report balances**),
  `status` (pending, approved, rejected, withdrawn, voided, used), `decided_by`, `decided_at`, `decision_note`.
  At most one OPEN (pending or approved) request per report. A blank message on a balanced report is stored as a
  default text (`DEFAULT_BALANCED_MESSAGE` / `DEFAULT_EMPTY_MESSAGE`), so the "message required" constraint stays.

### Who may do what

| Action | Tithes special member (seat) | Coordinator (own extension) | Admin / super-admin |
| --- | --- | --- | --- |
| View / edit draft | Drafts of the extension where they hold the seat | Yes | Yes |
| Request to finalize / withdraw it | No | **Only the coordinator** | No |
| Approve / reject a request | No | No | Yes |
| Finalize | No | **Only with an approved request, balanced or not** | Yes (on their own; a mismatch needs an approved request or a typed reason) |
| Reopen | No | No | Yes |
| Mark "No tithes & offering" | No | Yes (own extension) | Yes |
| Manage households | No | Yes | Yes |
| Print | Drafts only | Yes | Yes |
| My tithes (own history) | Yes | Yes | Yes |

"Own extension" is the extension of the report's SHEET. `permissions.py` is the only place that decides;
`TithesOffering.objects.for_user(actor)` is the only scoping (views start from it, so an unreachable report
looks exactly like a missing one). A draft is LOCKED for special members and coordinators while a request is
open; only admin and super-admin can still edit it. A report whose sheet is archived is read-only.

### Services (`services.py`)

Each action runs in `transaction.atomic()`, locks the report row AND its sheet row, re-checks permission on
the LOCKED row (`require(can_..., actor, report)`) and refuses with `ServiceError` unless the report is a draft.
`build_totals(report)` is the ONE function for the live numbers and for the freeze; `frozen_totals_for(report)`
reads the stored numbers back. Working data (offering, entries, breakdown, households) is not audited.
`member_tithe_history(user)`, `entry_names(entry)` and `bypass_note_for(report)` are read-only helpers used by
My tithes and the print page.

### Finalize and bypass order

**A coordinator never finalizes on their own** (`permissions.needs_finalize_approval`): every finalize needs an admin's
approval, even when the numbers balance. An admin or super-admin finalizes a balanced report directly.

`finalize_report` (one transaction, one-time token consumed first):
1. consume the token (a replay returns `duplicate=True`);
2. lock the report, require a draft on an open sheet and `can_finalize`;
3. refuse an empty report (that is "No tithes & offering"), then `build_totals`;
4. approval: a coordinator ALWAYS needs an APPROVED request with the SAME stored difference (it becomes `used`).
   An admin needs nothing when balanced; for a mismatch, that approval or a typed `bypass_reason`; otherwise refuse;
5. write the frozen numbers, `finalized_by`, `finalized_at`, status; void any other open request;
6. log `tithes.finalize` (tither count and `balanced` flag only).

`request_bypass` (coordinator only, no open request, balanced or not; the message is required only for a mismatch)
-> `decide_bypass` (admin approves or rejects; if the difference changed meanwhile the request is VOIDED and nothing
is decided) -> the coordinator presses Finalize and `finalize_report` uses the approval once. Any draft edit that changes the difference voids an open request (`_after_edit`).
`reopen_report` (admin only) clears every frozen field; entries, offering and breakdown stay.

### No tithes & offering

For a service where nothing was collected. `finalize_no_tithes(actor, sheet)` (`services.py`) finalizes a report
with `tithers_count` 0 and every amount 0 (difference 0). **No new column or migration:** a finalized report with
no tithers and a Total of 0 IS a "no tithes" report (`is_no_tithes(report)` reads the frozen values), and
`finalize_report` still refuses an empty report, so only this button can make one.
- Who: `permissions.can_mark_no_tithes(actor, sheet)` for a sheet with no report yet (coordinator of the extension,
  admin, super-admin; never a special member, never an archived sheet); an existing draft goes through `can_finalize`.
  **An admin closes a sheet on their own. A coordinator asks** (`request_no_tithes(actor, sheet)`): ONE step that starts
  the report when the sheet has none and files a finalize request on the empty report ("Request no tithes" on the list,
  or the request form on an empty draft's review page). **When an admin approves it, the report is finalized at once**:
  `decide_bypass` sees an approve on an EMPTY report and calls `_freeze_no_tithes` in the same transaction
  (`finalized_by` = the approving admin, the request becomes `used`, `BypassDecisionResult.finalized` is True, the
  coordinator gets a "no tithes request was approved" notice). Any other approved request still waits for the
  coordinator to press Finalize. `_freeze_no_tithes` is the one place the zeros are written (also used by
  `finalize_no_tithes`). The list's one-step "No tithes" button (`finalize_no_tithes`) is admin-only.
- One transaction: find or create the report (the sheet row is locked first, like `create_report`), lock the report,
  require a draft, REFUSE when anything is already entered (offering, tithe or bills and coins), freeze zeros, void
  any stale open bypass request, log `tithes.finalize` with `no_tithes: True` (no amounts). No one-time token: a second
  press hits "finalized and locked".
- Screens: a "No tithes" button (with a confirm dialog) on a sheet row that has no report (`report_list.html`) and in the
  Finalize card of the review page for an empty draft (`can_no_tithes`). Route `sheets/<id>/no-tithes/` (`report_no_tithes`, POST, in `area_urls.py`).
  The review and print pages say "No tithes and offering" instead of the tithers and bills-and-coins tables; the list
  shows "No tithes" instead of an amount.
- An admin reopens it like any finalized report; it becomes an empty draft again. My tithes and the dashboards need no
  change (no entries; the trend adds 0 for that date).

### Screens and routes

One set of views serves four places (`superadmin`, `admin_area`, `coordinator_area`, and `tithes` for special
members). Routes are written ONCE in `area_urls.py` and mounted four times; `urls.py` adds the member-root-only
route. Guards: `tithes_screen` (login, right area, `can_use_area`, never cached), then `for_user`, then the
service re-check.

- `""` list, `requests/` pending requests, `households/...`, `sheets/<id>/start/`
- `<id>/` workspace (editable draft) or review, `<id>/review/`, `<id>/print/`. The workspace only edits (offering,
  givers, bills and coins) and has one "Review & finalize" link; **Finalize, the bypass request and "No tithes" live on
  the review page**, which is titled "Review and finalize" while the report is a draft. All of it is ONE card,
  `_finalize_panel.html` (it replaces `_finalize_form.html` and `_bypass_panel.html`): the Finalize button, the finalize
  request form, Withdraw, the admin's Approve / Reject and "Finalize as no tithes & offering". Actions on it return to
  `report_review` (`report_action(..., to="report_review")`).
- JSON helpers `<id>/givers/`, `<id>/givers/prefill/`
- `sheets/<id>/no-tithes/` (POST, admin): close a sheet as "no tithes and offering"
- `sheets/<id>/no-tithes/request/` (POST, coordinator): ask an admin to do it; the approval finalizes
- POST actions: `offering/`, `breakdown/`, `entries/add|update|remove/`, `bypass/request|withdraw|decide/`,
  `finalize/`, `reopen/`
- `mine/` ONLY in `tithes/urls.py` as `tithes:mine` (see My tithes)

### Print page

`report_print` (`<id>/print/`, template `tithes/print.html`) is a read-only GET that changes and logs nothing;
`can_print` has the same rule as `can_view` (a special member prints drafts only). It is STANDALONE: it does
not extend `base_app.html`, black text on white in any mode, inline CSS, no external fonts, no Tailwind, no
`includes/logo.html` (`images/black-logo.svg`). `@page { size: A4; margin: 12mm }`, `thead` repeats,
`tr { break-inside: avoid }`, no page numbers. "Save as PDF" is the browser's print destination; there is no
server-side PDF library. A finalized report prints its FROZEN numbers; a draft counts live and prints
"DRAFT, not finalized". A shared envelope prints once ("Juan Cruz & Maria Cruz") with its full amount. A
mismatch that was let through prints "Finalized with a difference of ..., approved by ..., reason ...".

### My tithes

`my_tithes` (`tithes:mine`, template `tithes/mine.html`): the signed-in person's own giving history, every role,
no seat needed. FINALIZED reports only (a draft, a reopened report and a report on an archived sheet are
excluded), entries where the person is the giver OR a co-giver, newest first, with "Shared with <names>" and
the FULL amount (never split). It reads `request.user` and nothing else: no person id from the URL, query string
or form. It skips `tithes_screen` on purpose and lives in `urls.py` only. Reached from the "Tithes" tab of the
"My journey" hub (`SECTION_TABS["journey"]`).

### Dashboards

`dashboards/services._scope_tithes(user)` is the one scoping helper: finalized reports on uploaded sheets, through
`for_user` (a coordinator sees only their own extension). The trend reads the FROZEN `tithes_total` and
`offering_amount`, never a recount, and adds reports on the same date together. Amounts use `CURRENCY` and appear
only on leader dashboards. Context keys: `tithe_bars` (tithes) and `offering_bars` (offering). The dashboard writes nothing.

### Privacy

Never write a tithe amount, bypass message, decision note or bypass reason to `audit_log` or
`notification.data`. Audit data is ids, counts and the `balanced` flag. Notifications say THAT something
happened; the linked page does the permission check. The message and the note live only on the request row.
The print page may show a request's message as the "reason" (only people who may print a finalized report
reach it: coordinator and admins) but never the decision note. Screens and print pages show names and amounts
only: never a birth date, phone number or address.

### Tests (`apps/tithes/tests/`, `apps/dashboards/tests/`)

Package, not `tests.py`. Test users: `must_change_password=False`, `person_type="member_account"`. Builders in
`tithes/tests/support.py` go through services only. `test_print` (draft, frozen numbers, shared entry, no
birth date/phone, direct and approved bypass notes, access), `test_history` (own entries only, co-giver sees the
shared one at full amount, draft/reopened/archived excluded, no id from the request), dashboards
`test_tithes_scope` (coordinator scoping, drafts and reopened reports out, frozen same-date sums).
Run: `docker compose exec web python manage.py test apps.tithes apps.dashboards`.

## Faith: goals, streaks and history

Code: `apps/faith/` (`models.py`, `permissions.py`, `services.py`, `stats.py`, `schedule.py`, `streaks.py`, `overview.py`,
`partner.py`, `middleware.py`, `listeners.py`, `views.py`, `views_overview.py`, templates in `templates/faith/`,
`static/js/goals.js`, tests in `tests/`).

### Schedule, streaks and middleware (Brick 1)
- `Goal` has optional `start_time` and `end_time` (database constraint `goal_time_range`). A goal with a start time is a
  "timed goal" (used by reminders).
- `faith/schedule.py` answers which dates a goal is scheduled on (`scheduled_dates(goal, start, end)`, inclusive of both ends).
- `faith/streaks.py` owns the streak rules: the daily open streak, the Bible streak (`record_read(user, read_date)`) and
  `streak_snapshot(user, today)`. Other apps read streaks only through it.
- `faith/middleware.py` records the daily open (one line in `MIDDLEWARE`). It skips polling requests (`Accept: application/json`).

### Goals (Brick 3)
- Routes (namespace `faith`, mounted at `/goals/` for every role): `""` Today (`faith:goals`), `new/`, `all/`,
  `<pk>/edit/`, `<pk>/check/` (POST, JSON or redirect), `<pk>/note/` (POST), `<pk>/change-schedule/`,
  `<pk>/archive/`, `<pk>/restore/`. Brick 4 adds `history/` and `<pk>/` (see below).
- Permissions: `faith/permissions.py` only. `can_use_goals` = `User.can_sign_in`; `can_manage_goal` = owner.
  A missing goal and another person's goal are both a 404 in views and `PermissionDenied` in services.
- Services (`faith/services.py`): `clean_goal_data` (used by form and services), `create_goal`, `update_goal`,
  `change_schedule`, `archive_goal`, `restore_goal`, `set_checkin`, `set_checkin_note`. Each is atomic, locks the
  person then the goal, and re-checks the owner. Cap: 20 active goals.
- Check-ins: the server decides "today" (`timezone.localdate()`); no service accepts a date. Unchecking sets
  `done=False` and keeps the row and note. Only `done=True` rows count in stats. Notes are private to the owner.
- Edit rules: start date cannot move later than the first check-in; end date cannot be earlier than today or the
  last done check-in (an unchanged past end date is allowed); repeat type and weekdays lock once ANY check-in row
  exists; "Change schedule" ends the old goal today and creates a linked goal (`previous_goal`) starting tomorrow;
  a completed goal given a future or empty end date becomes active again.
- Stats (`faith/stats.py`, all take `today`): `goal_streaks`, `streak_chips`, `today_overview`, `today_counts`,
  `goal_list`, `done_dates_for`. The unchecked current day never breaks a streak until midnight.
- Audit: `goal.create`, `goal.update`, `goal.change_schedule`, `goal.archive`, `goal.restore` carry the goal id and
  field NAMES only. Check-ins and notes are not audited. `faith/services.py::_audit` is the one place that calls
  `log_action`.
- Frontend: `static/js/goals.js` (plain global functions `goalToday`, `goalRow`, `goalForm`, loaded with a normal
  script tag before the markup). Check-in uses fetch with an optimistic update; errors reload so the server's
  Django message shows as a toast.
- Journey hub: the Goals tab is `TabItem("Goals", "faith:goals", prefixes=("/goals/",))` and shows for every role.

### Overview and history (Brick 4)
- `faith/overview.py` builds the read-only pictures. `goal_overview(goal, today)` and `global_overview(user, today)` take
  `today` as a parameter, never read check-in notes, and never write audit or notification rows. Also `streak_cards` and
  `today_summary` (thin DB wrappers). Archived goals are left out of global history; completed goals stay.
- An unticked goal scheduled today is "open" (outlined cell): not expected, never "missed" until midnight, never breaks a
  streak; ticking it counts as done. Perfect-day streak: only days with at least one scheduled goal take part; empty days
  neither add nor break; today is skipped until all are done. Week and month rates are rolling 7 and 30 days ending today.
  Best weekday needs 2 settled days on that weekday and at least 1 done. Daily-habit strips are 28 days.
- Routes: `/goals/<pk>/` (`faith:detail`, owner only, a missing or foreign goal is 404; archived goals are allowed because
  All goals lists them) and `/goals/history/` (`faith:history`). Per-goal nav is `faith/_goal_nav.html`
  (`{% include 'faith/_goal_nav.html' with active='today' %}` or `'all'`); a wrong url name hides a link, it does not crash.
- Visual rules: server-rendered CSS grid and inline SVG, no chart library, no JavaScript. Heatmap cells use `bg-accent` at
  /20 /40 /70 and full, `bg-highlight` for a perfect day, `bg-border` for missed; every cell has a `title` and
  `aria-label`, and the legend is written in words.
- Dashboards: `get_dashboard_context` adds `streaks` and `goals_today` (None for people who cannot use goals);
  `dashboards/_faith_widgets.html` (with `_streaks.html`, `_goals_today.html`) draws them.
- Tests: `apps.faith.tests.test_overview`, `apps.dashboards.tests.test_faith_widgets`.

### Reminders and completion (Brick 10)
- `complete_ended_goals` (nightly): active goals with `end_date` before today (Manila) become `completed` with
  `completed_at`. Idempotent. Audit `goal.complete`, source `system_job`, ids only.
- `send_goal_reminders` (every 5 minutes): timed goals scheduled today, not done, whose start time is within the last
  15 minutes, owner can sign in. Dedupe key `goal_reminder:<goal id>:<date>`. Off when `faith_goal_reminders_enabled`
  is false (Site settings > Jobs). The goal title appears in the push body only; the bell item and `notification.data`
  hold only `{"goal_id": id}`.

### Partner view (Brick 9)
- `faith/partner.py: partner_goal_overview(user, today)` is read-only and does no permission check; the caller (Buddy) must
  have checked. It shows only ACTIVE goals that have started and not ended. Check-in notes are never read (it only uses
  `stats.done_dates_for`). The 7-day strip ends today; cell states are done, missed, not_scheduled, open.

## Buddy

Code: `apps/buddy/` (`models.py`, `permissions.py`, `services.py`, `messaging.py`, `views.py`, `message_views.py`, `urls.py`,
templates in `templates/buddy/`, `static/js/buddy_chat.js`, tests in `tests/`). Mounted at `/buddy/` (namespace `buddy`).

### Requests and partnerships (Brick 6)
- Routes (every view `buddy_screen`): `""` home, `requests/send/<user_id>/`, `requests/<id>/accept|decline|cancel/` (POST only).
- Hub: `SECTION_TABS["buddy"]` = Buddy | Messages | Their goals; one `NavItem(tabs="buddy")`, visible to all roles.
- Permission: `buddy.permissions.can_use_buddy(user)` = `can_sign_in`, any role. `signable_q(prefix)` is its database twin
  (a test compares the two for every person type and status). `buddy_screen` guards every view (login + `never_cache` +
  `can_use_buddy`) instead of `role_required`, because any role may use Buddy.
- Services (`buddy/services.py`): atomic, users locked in ascending id order, state re-checked under lock. `send_request`
  (not yourself, both can sign in, neither has a LIVE buddy, max 3 pending outgoing, one pending request per pair in either
  direction), `accept_request` (creates the sorted pair, cancels every other pending request involving either person, quiet
  when repeated), `decline_request`, `cancel_request`, `candidates`, `partner_card`, `active_partnership`, `is_live`.
- A partnership is LIVE when both people can sign in. A row that is not live is "inactive": it never blocks a new partnership
  and is auto-ended (`ended_by` NULL, audit `buddy.end`, reason `partner_cannot_sign_in`) when either person sends or accepts
  a request. A person whose partner can no longer sign in sees the normal search screen.
- `candidates()` is a deliberate exception to `User.objects.for_user()`: any extension, any role that can sign in, max 20 per
  page, returns only id, name, extension name and state. Empty or short search: own extension (admins and super-admins:
  everyone); 3+ characters: everyone.
- Notification types `buddy_request`, `buddy_accepted` (declared in `NOTIFICATION_TYPES`): ids only in `data`, sender through
  `actor`. No push for them. Decline notifies nobody; requests auto-cancelled by an accept are not announced.
  Audit: `buddy.request`, `buddy.accept`, `buddy.decline`, `buddy.cancel`, `buddy.end`: ids, counts and a fixed reason only.
- Schema: `user_buddy` gains `end_requested_by`, `end_requested_at`, `end_request_note`, `partner_final_note`; constraints
  `user_buddy_end_request_time` (a requester always has a time; one-way on purpose, because SET_NULL on a deleted requester
  must not break the row) and `user_buddy_partner_note_needs_end`. `buddy_note` gains `kind` (message|nudge|prayed), the
  `buddy_note_kind_body` constraint and index `ix_buddy_note_buddy_id`. Old NULL-body notes were backfilled as nudges.
- Tests: `apps.buddy.tests.test_requests`, `test_candidates`, `test_views` (helpers in `tests/helpers.py`). Without the faith
  streak engine the partner card shows "Not available yet".

### Messages (Brick 8)
- `buddy/messaging.py` owns chat. Services: `send_message`, `send_nudge`, `send_prayed`, `mark_read`, `thread_for`,
  `poll_since`, `limits_for`, `seen_state`, `unread_count`. Kinds: message (1 to 1000 chars), nudge (1 per hour per person,
  rolling), prayed (1 per Manila day per person).
- Writes need a live partnership (both people can sign in) and lock the `UserBuddy` row (no joins), re-check it is still
  open, then check limits and consume the one-time token inside the same `atomic()`. A replay is a quiet no-op. When the
  partner cannot sign in the thread is read-only (`thread_for` uses the open row, not `is_live`; `mark_read` still works).
- No audit rows for any note. `notification.data` holds `kind` and `sender_id` only. Messages are removed only through the
  user cascade (never when a partnership ends).
- Push: type `buddy_message`; shows the sender name and about the first 40 characters, in the push only. `dedupe_key` is
  `buddy_msg:<recipient>:<sender>:<oldest unread note id from that sender>`, so a burst pushes once and the next push comes
  after the thread is read (a nudge then a message in one burst is one push).
- `thread_for` merges every period of the same sorted pair (50 per page, `before=<note id>`), with a divider holding the end
  date and both final notes between periods.
- Routes under `/buddy/messages/` take no ids: `messages/` (page), `send/`, `nudge/`, `prayed/`, `poll/`, `earlier/`, `read/`.
  JSON endpoints; the page is `buddy/messages.html` with the `buddyChat()` Alpine component. Bell items are not marked read
  when the thread opens.

### Their goals (Brick 9)
- `partner_goals_for(viewer)` in `buddy/services.py` returns the LIVE partner's goals; the partner is read from the viewer's
  own `UserBuddy` row, never from the request. Route `/buddy/goals/` (`buddy:goals`) takes no id. No partner: a friendly empty
  state. Inactive partner: goals hidden. Reads are not audited and the partner is not notified.
- Templates `buddy/partner_goals.html` and `_partner_goal_card.html`; data from `faith.partner.partner_goal_overview`.
- Tests: `apps.buddy.tests.test_partner_goals`, `test_messages`.

### Ending a partnership
- `close_stale_end_requests` (nightly) closes end requests the partner has not answered after `buddy_end_request_days`
  (default 7, 1 to 90, Site settings > Jobs).

## Prayer
- Only people with role `member` who can sign in and have an extension send requests (`prayer.permissions.can_send`). Max 5
  per Manila day, text 1 to 2000 characters, one-time token on the send form.
- `PrayerRequest.objects.mine(user)` and `.inbox_for(actor)` are the ONLY scoping rules. Coordinator: requests of their own
  extension. Admin and super-admin: requests whose extension has no coordinator who can sign in. Archived requests are never
  listed.
- `permissions.sender_label(request, viewer)` is used by every template: anonymous reads "Anonymous" for everyone, "You
  (anonymous)" for the sender. `user_id` is stored but never rendered.
- Status is `open` -> `replied` (final) or `archived`. A reply is the last step: there is no "completed" status. "Mark as done" (`mark_done`) sends `DEFAULT_REPLY` as a normal reply, only while the request is open.
- Services (`send_prayer_request`, `reply_to_request`, `mark_done`) are atomic, re-check on the locked row, and answer
  "no longer exists" for both a missing and an out-of-reach request. Audit: `prayer.send`, `prayer.reply`,
  ids only. Notifications `prayer_request` (to the extension's coordinators, else admins) and `prayer_replied` (to the sender)
  carry no text and no name.
- Routes: member `/prayer/` (`prayer:list`, `prayer:send`). The inbox is written once in `prayer/area_urls.py`, mounted at
  `/coordinator/prayer/`, `/admin/prayer/`, `/superadmin/prayer/` (names `prayer_inbox`, `prayer_detail`, `prayer_reply`, `prayer_complete`). No delete route.
- Navigation: members keep "Prayer"; coordinator, admin and super-admin get "Prayer inbox" in Manage.

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
- **Bookmark and Next:** "I'm done for now" and any "mark as read" control are removed. Next (POST `bible:finish`,
  `after=next`) is the only way a chapter counts as read: `services.finish_chapter()` calls `record_chapter_read()` (a
  `BibleChapterRead`), moves the person's single `BibleBookmark` to the following chapter (crossing book ends), on a new row
  sends `bible.signals.chapter_read`, and the view redirects there. The target is computed on the server, never read from
  the form. The last chapter (Revelation 22) shows Finish (same url, no `after`): the bookmark stays and a toast is shown.
  Previous is a plain link and counts nothing. A GET never writes. When the text is locked or not imported, Next is a plain
  link and changes nothing.
- **Auto bookmark:** while the chapter text is showing, an Alpine component on the article POSTs to `bible:bookmark` once
  after 20 seconds on the page (counted only while `document.visibilityState` is `visible`). It sends `X-Requested-With`,
  and `bookmark_view` answers 204 with no message; a normal form post still redirects with a message. Locked and
  not-imported chapters render no component. The manual "Bookmark" button stays (it sets the bookmark without counting a
  read); its badge follows the component state.
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
  Sections today: `bible` (`apps/bible/site_section.py`, order 10), `special-roles` (order 20), `registration` (order 30),
  `jobs` (order 40) and `retention` (order 50). `accounts` never imports
  feature apps; they register themselves. A section's POST views keep their own `role_required` and service guard: the
  registry only decides what is displayed. Section icons must exist in `sprite.svg` (`jobs` uses `clock` and `calendar`).
  The `jobs` and `retention` views are mounted with `path("site-settings/", include("apps.accounts.urls.site_jobs"))` in the
  super-admin url module; `SITE_SETTINGS_URL_NAME` at the top of `accounts/views/job_settings.py` must match the real url
  name of the Site settings page.
- **`jobs` (super-admin):** the goal reminder switch (`faith_goal_reminders_enabled`) and `buddy_end_request_days` (1 to 90).
  Services in `accounts/services/job_settings.py`; readers in `core/job_settings.py` (so faith and buddy never import
  accounts). Forms post the wanted state; an unchanged value writes no audit row; a change logs `site_setting.*` with
  before/after.
- **`retention` (super-admin):** lists `retention_policy` rows, edits days (1 to 3650) or "never". Audit
  `retention_policy.change` (before and after days). Rows cannot be created here. A changed period applies only to records
  archived from now on (see Model conventions).
- **Special-role limits live in two places with one panel.** The super-admin uses the "Special roles" section; an admin uses
  the "Special roles" button on `/admin/users/` (`admin_area:special_role_limits`). Both include
  `accounts/people/_special_role_limits.html` with `views.special_roles.limits_context()`. A super-admin GET of
  `/superadmin/special-roles/` redirects to the section; POSTs still go to `special_role_limits`.
- **Limit rules (`services/special_roles.py`):** a limit cannot exceed `limit_cap()` (`member_counts()`, the members who can sign in: role member,
  in the extension, not archived or suspended, but never less than 2), cannot be below 2 or below the people holding the role,
  and an empty value resets it to 2. `bulk_set_special_role_limits(actor, request, role, value, extension_ids=None)` runs `set_special_role_limits`
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
  | Support the ministry | a link to `/give/`; the accounts and instructions are on that page (see Donation page and About) | none |
  | Long footer "More" links | `site_setting` key `pages_footer_links` (see Donation accounts and footer links) | `FOOTER["more"]`; the rest of `FOOTER` (offices, social, affiliates) is not managed yet |
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
  `#contact`. Keep every anchor. The public navbar links to `/#about` and to the real `/give/` page.
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
  `apps/dashboards/tests/`. Role home URLs still live only in `accounts/decorators.py` (`ROLE_HOME_NAMES`).
- **One template per role** (`superadmin.html`, `admin.html`, `coordinator.html`, `member.html`) extending
  `dashboard_base.html`, so a role can change on its own. Shared sections are includes: `_daily_verse`,
  `_continue_reading`, `_quick_links`, `_coming_soon`, `_faith_widgets` (with `_streaks` and `_goals_today`; each role
  template includes `_faith_widgets.html` next to `_coming_soon.html`); leaders also get `_leader_overview` with the charts
  `_bar_list` and `_column_chart`. Members get no overview.
- **Shared sections:** greeting (device clock, "Welcome" without JavaScript); verse of the day in the version from the
  `bible_version` cookie (`VERSION_COOKIE` in `services.py`, must match the reader); continue reading (the person's
  `BibleBookmark` only, as `{"label", "url"}`; no bookmark gives a "Start reading" link to Genesis 1, `start_url`; the old
  "newest read" logic is gone); quick links taken from `NAV_ITEMS` through
  `build_nav()` (so they always match the sidebar; Home is left out; routes that do not exist yet
  show "Soon"); streak cards and today's goals (`streaks` and `goals_today`, None for people who cannot use goals, from
  `apps.faith.overview`, read-only); a "Coming soon" card for events only (replace it with the real widget when
  the events widget exists).
- **Leader overview:** headline tiles, people by role (not archived) and by status (everyone), people per extension
  (super-admin and admin only, top 10), and three trend charts over the last 8 gathering dates: attendance,
  first-timers, tithes and offering. Trends use **uploaded** sheets only and their **frozen** values (never a
  recount); sheets on the same date are added together. The coordinator's numbers are limited to their own
  extension. Only the super-admin sees super-admin rows. Amounts are shown in pesos (`CURRENCY` in `services.py`).
- **Scoping:** the only code that narrows data to a coordinator's extension is `_scope_people`, `_scope_attendance`
  and `_scope_tithes` in `services.py`. `User.objects.for_user(user)` now exists for people, so `_scope_people` can use it;
  `_scope_attendance(user)` already returns `Attendance.objects.for_user(user)` limited to UPLOADED sheets (a reopened
  sheet drops out until it is uploaded again); `_scope_tithes(user)` returns finalized reports on uploaded sheets through `TithesOffering.objects.for_user` (see Tithes).
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
that any person, archived included, still belongs to), `archive_inactive_users`, `auto_transfer_extension`,
`cleanup_email_tokens` (accounts), `complete_ended_goals` (faith),
`close_stale_end_requests` (buddy), `cleanup_notifications`, `cleanup_registration_requests`, `cleanup_audit_log`; every
15 minutes `send_event_reminders`; every 5 minutes `send_goal_reminders` (faith); hourly `fetch_jil_videos`. Jobs log with
`source='system_job'` and no actor. (`import_bible` is run by hand, not on a schedule.)
- **Scheduling:** production runs them with the `scheduler` service in `production/docker-compose.prod.yml`
  (`production/deploy/scheduler.py`). In `scheduler.py` the NIGHTLY order is archive first, purge last; the 5-minute job is
  `EVERY_5_MIN`. The dev compose file has NO scheduler: run jobs by hand with
  `docker compose exec web python manage.py <job>`.
- Super-admins tune the reminder switch and `buddy_end_request_days` in Site settings > Jobs.

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
  An icon name missing from the sprite renders as nothing, so check `sprite.svg` when adding one (recent additions
  to check: `flame`, `clock`, `calendar`).
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
- The navbar Login button uses `{% url 'accounts:login' %}` (a signed-in visitor sees "Open app"). About links to
  `/#about` (landing anchor) and Give links to the real `/give/` page, so both work from any public page. `{% url %}` on a missing route raises `NoReverseMatch`.
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
    text. No `bg-red-*` or `text-white`. Inline error text uses `text-danger` (use the project's error token if it differs).
- **App shell (`base_app.html`):** the header shows the logo (`brand_mark.html`, the one place its size is set) and
  the page title. From `sm` up an icon rail (its top button opens the panel) plus an expandable panel (Alpine store `sidebar`,
  `expanded`, `open()`, `close()`, `toggle()`); below `sm` a bottom bar or a drawer from `mobile_nav_style`
  (`user_settings.mobile_nav_style`, default `bottom`). The context processor
  `accounts.context_processors.navigation` supplies `nav` and `mobile_nav_style` and must be in `TEMPLATES`.
  To change a menu edit `NAV_ITEMS` in `accounts/navigation.py` (order = list order, `roles=`, `bottom=True`,
  `short=` for a shorter label on the mobile bottom bar only, `group=`, `exact=True` for home pages); `icon` must
  exist in `static/icons/sprite.svg`. Unresolvable url
  names render as disabled placeholders, never `NoReverseMatch`.
  **Hubs and page tabs:** a `NavItem` with `tabs="key"` is ONE sidebar link (to the first visible tab that resolves,
  lit on every tab's pages) whose sub-pages are `TabItem`s in `SECTION_TABS["key"]`. Never put sub-pages in the
  sidebar; add a `TabItem`. Hubs: `journey` (Goals | Attendance | Tithes), `people` (People | Guests | Approvals),
  `website` (Landing | Give page | Resources) and `buddy` (Buddy | Messages | Their goals, shown to every role). `base_app.html` draws the tab row above `content` for every page whose
  path belongs to a hub (`{% block hub_tabs %}{% hub_tabs %}{% endblock %}`, tag in `accounts/templatetags/nav_tags.py`,
  markup in `includes/section_tabs.html`); a page hides it by overriding that block with an empty one, and
  `{% section_tabs "key" %}` places a row by hand. A tab owns sibling pages with `TabItem.also` (url names) and whole
  areas with `TabItem.prefixes` (literal path prefixes); the longest match wins. Sidebar groups: no heading (personal)
  and "Manage" (leaders). Django admin is in the account menu in `topbar.html` for the super-admin only, not in
  `NAV_ITEMS`. Tests: `apps.accounts.tests.test_navigation`. `NAV_ITEMS` holds only strings (no imports
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
  attendance route goes in `attendance/area_urls.py` once, never in a role url module. The same holds for
  `include("apps.prayer.area_urls")` (prayer inbox).
- `attendance/print.html` is standalone on purpose. Do not make it extend `base_app.html` or reuse `includes/logo.html`: the app
  shell's tokens, dark mode and the logo mask would put colours and an empty logo on paper. Rebuilding Tailwind does not affect it.
- The tithes routes exist only where `include("apps.tithes.area_urls")` is added. A new tithes route goes in
  `tithes/area_urls.py` once, never in a role url module. `mine/` is the one exception and lives in `tithes/urls.py`.
- `tithes/print.html` is standalone on purpose: do not make it extend `base_app.html` or reuse `includes/logo.html`.
- The people templates are shared by three roles. Link through `{% role_url %}` and gate buttons with
  `{% person_actions %}`; a hardcoded `superadmin:` URL works for the super-admin and raises `NoReverseMatch` or sends an
  admin or coordinator to the wrong area.
- The faith daily-open middleware and the Buddy chat poll: the poll sends `Accept: application/json` so it is not counted
  as a daily open. A new background-polling endpoint must do the same.
- `notify()` keeps a `dedupe_key` for ever. A key that must repeat later needs a part that changes (see `buddy_msg:...`
  and `goal_reminder:<goal id>:<date>`).
- The goal and Buddy `Meta.constraints` that tie a `SET_NULL` column to another column must be one-way (see Buddy).

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
- Use {% comment %} because the templating raises error
- Tests must not touch real external services (see Behaviour rules). A test that needs a logged-in user creates an
  one with `must_change_password=False` and `person_type="member_account"` (see Password change).
- Prefer the smallest relevant command or page load to verify a change.

### Events (brick 10: services and permissions)
- `event` gained `location`, `cancelled_at`, `cancelled_by` (migration `event_location_cancel`), a check that `end_at` is after
  `start_at`, and an index on `start_at`. `extension` NULL = global event. Cancel is final: the row stays, marked cancelled
  (`Event.is_cancelled`); it is not archived.
- Who may do what lives only in `events/permissions.py`. Views use `visible_events(actor)` and answer 404 outside it. Global
  events: everyone sees, admin and super-admin manage. Extension events: that extension's members and coordinator see, admin,
  super-admin and that coordinator manage. Events tie to the extension, never the coordinator account.
- `events/services.py`: `create_event`, `update_event`, `cancel_event`. Each checks permission, runs in `transaction.atomic()`,
  locks the row, raises `ServiceError`, calls `log_action()` (`event.create`, `event.update`, `event.cancel`; descriptions never
  reach the audit log, only a `description_changed` flag). The start of an event that already started cannot be moved; an
  event that already ended cannot be cancelled; scope cannot change after creation.
- Notices (`event_changed`, `event_cancelled`, declared in `events/services.py`): sent for a time or place change and for a
  cancellation only (not for title or description). Recipients: every ACTIVE account for a global event, the extension's ACTIVE
  members and coordinator for an extension event; never the actor, never suspended or archived accounts, and never guests or no-account members, never
  anyone with in-app alerts off for that type. `notification.data` holds only the title and what changed.
- `notifications/services.py`: `notify()` (one person, optional `dedupe_key`) and `notify_many()` (ids, bulk insert). Both skip
  people who turned off in-app alerts of that type and never import a feature app.
- Tests: `apps.events.tests.test_permissions`, `apps.events.tests.test_services` (builders in `tests/helpers.py`).

### Events screens (brick 11: calendar and list)

- **Where:** `apps/events/` `calendar.py` (month grid, pure helpers), `forms.py` (`EventForm`), `views.py`, `urls.py` (namespace
  `events`, mounted at `/events/`), templates in `apps/events/templates/events/`, tests `tests/test_calendar.py`, `tests/test_views.py`.
- **Routes:** `index/` (redirects to the view used last on this device; the sidebar points here), `calendar/`, `list/`, `new/`,
  `<pk>/`, `<pk>/edit/`, `<pk>/cancel/` (POST only). The chosen view is the `events_view` cookie (per device, one year).
- **Every view** is `never_cache` + `role_required` (all four roles). Events are looked up through `visible_events(request.user)`,
  so an event outside the person's reach is a 404. Buttons come from `available_actions`; the services still check again.
- **Calendar:** Manila time, weeks start on Sunday, `?month=YYYY-MM` (bad value = this month). A multi-day event shows on each
  day; one that ends exactly at midnight does not spill onto the next day. Desktop shows chips (3 per day, then "+N more");
  phones show dots and tapping a day shows that day's agenda (Alpine keeps only the selected day). Global chips: gold edge;
  extension chips: accent edge; cancelled: struck through. Managers: clicking a day opens `new/?date=YYYY-MM-DD` (start 9:00 AM).
- **List:** grouped by local start date, 25 per page. Upcoming = not yet over (`Coalesce(end_at, start_at) >= now`, the same rule
  as `cancel_event`), soonest first; Past = latest first. Filters: all, global, my extension (coordinator and member), and an
  extension picker for admin and super-admin (`?scope=` and `?extension=`; bad values fall back to "all").
- **Form:** scope is chosen at creation only. A coordinator can pick only their own extension; admins also get "Everyone (global
  event)". The start is locked once the event has started. Save and Cancel ask for confirmation (`data-confirm`). Create carries
  a one-time token (`new_submission_token`).

### Push notifications (brick 12: foundation)

- **What:** Web Push on top of the bell, via the `pywebpush` package (the one extra Python dependency; everything else
  outgoing still uses the standard library). The bell item is always created; a push is a best-effort extra.
- **Table:** `push_subscription` (model `PushSubscription`): `user` (CASCADE), `endpoint`, `endpoint_hash` (SHA-256, unique),
  `p256dh`, `auth`, `label` ("Chrome on Android", never the raw user agent), `created_at`, `last_success_at`. Endpoint and
  keys are private: never in `audit_log`, `notification.data` or logs.
- **Config (`.env`, in `.env.example`):** `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` (raw base64url), `VAPID_SUBJECT`
  (`mailto:` or https). Make keys with `manage.py generate_vapid_keys`. With any of the three empty, `push.is_enabled()` is False,
  every push control is hidden and the bell works alone. Also `PUSH_TTL_SECONDS`, `PUSH_ASYNC` and `PUSH_ENDPOINT_HOSTS`
  (settings). Changing the keys later stops pushes to existing devices: people turn notifications off and on again.
- **Code:** `notifications/push.py` (delivery, `is_enabled`, `endpoint_allowed`, `device_label`, `clean_message`,
  `send_to_users`, `dispatch_after_commit`), device services in `notifications/services.py` (`register_device`,
  `unregister_device`, `remove_device`, `is_registered`, `list_devices`), thin JSON views in `notifications/push_views.py`
  (urls `notifications/push_urls.py`, namespace `push`, mounted at `/notifications/push/`), `push_context.push_config`
  (`push_enabled`, `push_public_key`), the `{% push_device_card %}` tag (`templatetags/push_tags.py`,
  `templates/notifications/_push_card.html`) for the Settings page, `static/js/push.js` (Alpine `pushDevice`, loaded before
  Alpine by `base.html`), `static/js/sw.js` (served at `/sw.js` by `push_views.service_worker` so its scope is the whole site),
  `static/manifest.webmanifest` and `static/images/icon-192.png` / `icon-512.png` (iPhone needs the manifest).
- **`notify(..., push={"title", "body", "url"})`** and `notify_many(..., push=...)`: push goes out AFTER commit (a rollback
  sends nothing), in a background thread when `PUSH_ASYNC` is on, only to people who got a bell item (so a muted type or a
  duplicate `dedupe_key` never pushes). A push failure is logged (subscription id and HTTP status only) and never raises.
  `push.send_to_users()` is the synchronous version for management jobs.
- **Rules:** the payload is a title, a short body and a relative link; `clean_message` drops anything else and falls back to
  `/notifications/` for a link that is not a path on this site. The server only pushes to hosts in `PUSH_ENDPOINT_HOSTS`
  (stops a user registering an internal address as a device). Up to 10 devices per person. A 404 or 410 from the push
  service deletes the device. The test button has a 10 second cooldown per person.
- **Privacy exceptions (push body only):** `goal_reminder` shows `Time for "<title>"`; `buddy_message` shows the sender name
  and about the first 40 characters. The bell item and `notification.data` never hold that text.
- **Browsers:** desktop browsers and Android work. iPhone and iPad need iOS 16.4+ and Add to Home Screen first; the card
  explains it. Needs HTTPS in production (`localhost` / `127.0.0.1` is fine for development).
- **Tests:** `apps.notifications.test_push` (flat module: `notifications/tests.py` already exists, so no `tests/` package).
- **Open item:** the bell list needs a generic line for `buddy_request`, `buddy_accepted`, `buddy_message` (for example
  "<name> sent you a message"; `data["kind"]` is `message`, `nudge` or `prayed`), `goal_reminder` ("A goal is due"),
  `prayer_request`, `prayer_replied` and `admin_alert`. Declare each type the way `events` declares `event_changed` so it
  can be muted.
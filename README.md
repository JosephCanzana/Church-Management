# Church-Management

Church management system for Jesus Is Lord Church (JIL) and its local
branches ("extensions"): member accounts and hierarchy, attendance, tithes and
offering, faith goals and Bible reading streaks, accountability buddies,
prayer requests, events, and shared worship resources.

## Tech stack
- Django, PostgreSQL 16, Docker Compose
- Tailwind CSS (standalone CLI) and Alpine.js
- Python 3.12, Django 5.1+ (tested on 5.2 and 6.0)

## Setup

### Prerequisites
- Docker and Docker Compose
- `make`

### First-time setup
1. Clone the repo.
2. Copy `.env.example` to `.env` and fill in the values.
3. Download the Tailwind CLI (one-time):
   ```bash
   curl -sLO https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-linux-x64
   chmod +x tailwindcss-linux-x64
   mv tailwindcss-linux-x64 theme/tailwindcss
   ```
4. Download Alpine.js (one-time):
   ```bash
   mkdir -p static/js
   curl -sLo static/js/alpine.min.js https://cdn.jsdelivr.net/npm/alpinejs@3.14.9/dist/cdn.min.js
   ```
5. Build and migrate:
   ```bash
   docker compose up --build
   docker compose exec web python manage.py migrate
   docker compose exec web python manage.py createsuperuser
   ```
   `createsuperuser` asks for **Account id**: press Enter, it is generated
   (format: counter starting at 1000 + year, e.g. `10002026`). Then enter
   first name, last name and password. Log in with the account id.

### Resetting dev data and seeding a super-admin
Add a password for the seeded account to `.env` (it is never stored in code):
```
SEED_SUPERADMIN_PASSWORD=choose-a-dev-password
```
Then, after any reset:
```bash
docker compose down -v
docker compose up --build -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py seed_superadmin
```
The command prints the account id (on a fresh database the first id is `1000` plus
the year, e.g. `10002026`). Log in at `/accounts/login/`. It only runs with `DEBUG=True` and
does nothing if a super-admin already exists.

### Day-to-day
```bash
docker compose up
make tailwind-watch      # separate terminal
```
App: http://127.0.0.1:8001/  Admin: http://127.0.0.1:8001/admin/

> Use `127.0.0.1`, not `localhost` (known IPv6/IPv4 quirk in local dev).

## Project structure

```
church_management/   # settings package (config only, not an app)
core/                # shared abstract models, site settings, retention policy, purge job
audit/               # audit_log (append-only)
accounts/            # extensions, custom User, roles, default passwords, email tokens, app navigation
theming/             # themes (light + dark palettes) and per-user display settings
pages/               # landing page content, donation info, latest JIL video
bible/               # verse of the day, chapters read
faith/               # goals, check-ins, daily activity, streaks
events/              # events and reminders
resources/           # monthly themes, weekly PowerPoint, general resources
attendance/          # attendance sheets, members, first timers
tithes/              # tithes and offering records
buddy/               # accountability partners
prayer/              # prayer requests and replies
notifications/       # bell, preferences, admin alerts
dashboards/          # temporary role landing pages (no models)
templates/  static/  theme/
docs/                # schema.sql, flows.md, verify.sql (reference)
```

| App | Tables |
|---|---|
| `core` | `site_setting`, `retention_policy` |
| `audit` | `audit_log` |
| `accounts` | `extension`, `app_user`, `special_role`, `user_special_role`, `extension_special_role_limit`, `default_password`, `email_token`, `user_extension_history` |
| `theming` | `theme`, `theme_palette`, `user_settings` |
| `pages` | `church_content`, `landing_image`, `donation_account`, `jil_video` |
| `bible` | `daily_verse`, `bible_chapter_read` |
| `faith` | `goal`, `goal_checkin`, `user_daily_activity`, `user_streak` |
| `events` | `event` |
| `resources` | `monthly_theme`, `monthly_resource`, `weekly_powerpoint`, `general_resource` |
| `attendance` | `attendance`, `attendance_member`, `attendance_first_timer` |
| `tithes` | `tithes_offering`, `tithe_entry` |
| `buddy` | `user_buddy`, `buddy_request`, `buddy_note` |
| `prayer` | `prayer_request`, `prayer_reply` |
| `notifications` | `notification`, `notification_preference`, `notification_rule` |

42 tables in total (`dashboards` has none). Table names are fixed with `db_table` and match
`docs/schema.sql`.

## Roles and routes

| Role        | Base path        | Access                                           |
|-------------|------------------|--------------------------------------------------|
| Super-admin | `/superadmin/`   | Own dashboard, with a link to Django admin       |
| Admin       | `/admin/`        | Manages extensions, settings, and coordinators   |
| Coordinator | `/coordinator/`  | Manages their own extension only                 |
| Member      | `/home/`         | Personal pages (attendance, tithes, prayer, etc.)|

Login: `/accounts/login/` (account id or verified email), `/accounts/logout/` (POST only)
and `/accounts/activate/` (placeholder until activation is built). Forgot-password and
reset are not built yet; the link on the login page is a placeholder. People with a
temporary or never-set password are sent to `/accounts/activate/` after logging in.

After login each role lands on its own temporary page (served by the `dashboards` app,
which only says which role it is) until the real dashboards exist. Opening another
role's page sends you back to your own. Where each role lands is set in
`accounts/decorators.py` (`ROLE_HOME_NAMES`).

Django's built-in admin lives at `/django-admin/` (not `/admin/`) and is
for the super-admin only. Only users with `is_staff=True` can open it.

## Frontend setup

- **Stack:** Tailwind CSS (standalone CLI), Alpine.js, self-hosted fonts and Heroicons sprite.
- **Templates:** project-level layouts live in `templates/` (`base.html`, `base_public.html`,
  `base_app.html`). Shared pieces live in `templates/includes/` (`public_navbar.html`,
  `footer.html`, `logo.html`, `icon.html`, `sidebar.html`, `sidebar_rail.html`, `sidebar_utilities.html`, `nav_link.html`, `brand_mark.html`, `topbar.html`, `bottom_nav.html`, `messages.html`). App-specific pages live in
  `<app>/templates/<app>/`.
- **Public layout:** `base_public.html` wraps each page with the public navbar (brand, centered
  links, mode toggle, login button, mobile dropdown) and the public footer. The footer
  (`includes/footer.html`) sits in normal flow below the content, so it never covers it.
- **App layout and navigation:** `base_app.html` is the signed-in layout. From `sm` up there is an icon
  rail (`sidebar_rail.html`, tooltips on hover and focus) whose top button expands it into the full panel (`sidebar.html`) over the page, with a backdrop. Below `sm` it is either a bottom
  bar with a "More" sheet or the same panel as a slide-in drawer (opened by a header button), chosen by
  `user_settings.mobile_nav_style`. Every link comes from the `NAV_ITEMS` list in
  `accounts/navigation.py`: add, remove or reorder a line, set `roles=`, and `bottom=True` for the bottom bar
  (4 slots, the rest go under More). A url name that does not exist yet shows as a dimmed placeholder.
  Needs `accounts.context_processors.navigation` in `TEMPLATES` and the Alpine stores `sidebar` and `mode`
  in `static/js/ui.js`. With `DEBUG=True`, `?nav=bottom` or `?nav=hamburger` forces a mobile style for the
  session (`?nav=off` clears it).
- **Static files:** `static/css/input.css` is the Tailwind source and holds the design tokens.
  `static/css/output.css` is generated and not committed. Other folders: `fonts/`, `icons/`,
  `images/`, `js/`.
- **Build CSS:** `make tailwind-build` (once) or `make tailwind-watch` (while developing).
- **Light and dark mode:** the toggle in the public navbar saves the choice in the browser.
  Dark colors are in `static/css/themes.css`.
- **Toasts and confirm dialogs:** global, defined in `static/js/ui.js` (Alpine store `ui`) and
  rendered by `includes/messages.html`. Django `messages` show as toasts. Add
  `data-confirm="..."` to a form or its submit button to ask for confirmation first, or call
  `$store.ui.confirm({...})` / `$store.ui.toast(...)` from Alpine. `ui.js` loads with `defer`
  before `alpine.min.js`.
- **Settings required:** `TEMPLATES['DIRS'] = [BASE_DIR / 'templates']` and
  `STATICFILES_DIRS = [BASE_DIR / 'static']`.
- **Public pages:** the landing page (`/`, `pages/templates/pages/landing.html`) has static
  copy: a hero with a Log in button, what members and leaders get, and a closing call to
  action. The `pages` app will feed it later. The login page
  (`accounts/templates/accounts/login.html`) shows a time-of-day greeting, a first-time note
  and a verse on the left and the form on the right (stacked on small screens), with a
  show-password checkbox and a forgot-password link.
- **Placeholder links:** the navbar's Login button, its About / Give links and the login
  page's Forgot password link are `href="#"` until those pages exist. The landing page's
  Log in buttons point to the login page (`/accounts/login/`).

## How the data works (short version)
- **Roles:** super admin > admin > coordinator > member. Special roles
  (`tithes_offering`, `attendance`) are added on top, with a per-extension limit.
- **Archive, then purge:** archiving sets `archived_at`; a database trigger
  fills `purge_at` from `retention_policy`. A nightly job deletes rows past
  `purge_at`. Attendance and tithes are never auto-deleted by default.
- **Frozen records:** uploaded attendance and tithes totals are frozen; only an
  admin can reopen them (logged).
- **Audit:** every change is written to `audit_log`; passwords, tokens, goal
  text, notes and prayer text are never logged.

## Verifying the install

```bash
# 0. login and audit tests
docker compose exec web python manage.py test accounts audit -v 2

# 1. config + models match migrations (both must print nothing bad)
docker compose exec web python manage.py check
docker compose exec web python manage.py makemigrations --check --dry-run

# 2. full schema test suite (uses a throwaway test database)
docker compose exec web python manage.py test core.test_schema -v 2
```
The suite checks all 42 tables, seed data, purge triggers, account-id
generation, login, delete rules and the database constraints.

### Checking the role redirects
1. Log in as each role. You should land on `/superadmin/`, `/admin/`, `/coordinator/` or
   `/home/`, and the page should say which role it is.
2. As a member, open `/admin/`. You should be sent back to `/home/`.
3. Logged out, open `/superadmin/`. You should reach the login page with
   `?next=/superadmin/`.

### Inspecting the database with pgAdmin
1. `cp docker-compose.override.yml.example docker-compose.override.yml`
   (git-ignored) and run `docker compose up -d`.
2. pgAdmin: Register > Server. Host `127.0.0.1`, port `5433`, database /
   username / password from your `.env`.
3. Open Query Tool on the database and run the blocks in `docs/verify.sql`.
4. Optional: right-click the database > ERD Tool to see the relationships.

pgAdmin will show foreign keys as `NO ACTION`. That is expected: Django applies
`on_delete` rules (CASCADE, SET NULL, RESTRICT) in Python, not in the database.

## Troubleshooting
- **"password authentication failed" on `db`:** you changed `.env` Postgres
  credentials after the volume was created. `docker compose down -v` then
  `docker compose up --build` (wipes local dev data).
- **`InconsistentMigrationHistory` mentioning `admin.0001_initial`:**
  `migrate` ran before `AUTH_USER_MODEL = 'accounts.User'` was set. Wipe the dev
  volume (above) and migrate again.
- **`postgres.E005 ... must be in INSTALLED_APPS`:** add
  `'django.contrib.postgres'` to `INSTALLED_APPS` (required by `ArrayField` on
  Django 6.0).
- **Account ids come out empty or fail the format check:** the
  `account_number_seq` sequence is created by `core` migration
  `0002_seed_and_triggers`. Make sure it has been applied.
- **Pages unstyled:** `make tailwind-watch` is not running.
- **`TemplateDoesNotExist` for a page in a new app:** the app is missing from
  `INSTALLED_APPS`, so its `templates/` folder is not searched.
- **Inspect from the CLI:** `docker compose exec web python manage.py dbshell`,
  then `\dt`, `\d <table>`, `\pset pager off`.

## License
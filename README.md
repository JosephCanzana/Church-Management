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
accounts/            # extensions, custom User, roles, default passwords, email tokens
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

42 tables in total. Table names are fixed with `db_table` and match
`docs/schema.sql`.

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
# 1. config + models match migrations (both must print nothing bad)
docker compose exec web python manage.py check
docker compose exec web python manage.py makemigrations --check --dry-run

# 2. full schema test suite (uses a throwaway test database)
docker compose exec web python manage.py test core.test_schema -v 2
```
The suite checks all 42 tables, seed data, purge triggers, account-id
generation, login, delete rules and the database constraints.

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
- **Inspect from the CLI:** `docker compose exec web python manage.py dbshell`,
  then `\dt`, `\d <table>`, `\pset pager off`.

## License
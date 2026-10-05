# AGENTS.md

## Project overview
Church-Management is a Django + PostgreSQL system for Jesus Is Lord Church
(JIL) and its local branches ("extensions"). Scope: accounts and hierarchy,
attendance, tithes and offering, faith goals and Bible streaks, accountability
buddies, prayer requests, events, resources, notifications and audit logging.

**Status:** all 14 apps and 42 models exist, with migrations and seed data.
`core.test_schema` verifies them. Services, views, forms and templates are
mostly not built yet: the next work is building features on top of the models.

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
- `templates/` (`base.html`, `base_public.html`, `base_app.html`, `includes/`),
  `static/`, `theme/` (Tailwind CLI binary, gitignored), `docs/`.
- `Dockerfile`, `docker-compose.yml`, `Makefile`, `requirements.txt`, `.env.example`.

## Commands (always through Docker)
```bash
docker compose up --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser   # Enter at "Account id"
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
| `audit` | `audit_log` | `log_action()`, log viewer, `cleanup_audit_log` |
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
- Keep `{# ... #}` comments on their own line, never inside a tag.

## Known footguns
- `migrate` from the host cannot reach `POSTGRES_HOST=db`; run it in the `web`
  container. `makemigrations` works from the host.
- Running `migrate` before `AUTH_USER_MODEL` is set causes
  `InconsistentMigrationHistory`; fix by `docker compose down -v` (dev only).
- Core migration `0002_seed_and_triggers` depends on the first migration of
  several apps; if an app's tables move to a later migration, update its
  `dependencies`.
- pgAdmin shows FKs as `NO ACTION`; that is expected (see above).
- pgAdmin access needs the dev-only port from
  `docker-compose.override.yml.example`; never publish the db port in production.

## Working rules for agents
- Use Docker commands; run `check` and `core.test_schema` after any model change.
- Never edit an applied migration; add a new one.
- Do not commit `.env`, `venv/`, `theme/tailwindcss`, media uploads or db dumps.
- Keep `.env.example` in sync with settings.
- Prefer the smallest relevant command or page load to verify a change.
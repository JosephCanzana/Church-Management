#!/usr/bin/env bash
# Reset the PRODUCTION database: wipes ALL app data and rebuilds an empty schema.
#
# Run from anywhere (it moves to production/ by itself):
#   bash production/deploy/reset-db.sh                 # backup first, then reset
#   bash production/deploy/reset-db.sh --no-backup     # skip the safety backup
#   bash production/deploy/reset-db.sh --wipe-media    # also delete uploaded files
#   bash production/deploy/reset-db.sh --no-superuser  # do not ask for a super-admin at the end
#
# What it does, in order:
#   1. shows what will be destroyed and asks you to type  reset <database name>
#   2. saves a compressed dump to data/backups/ (unless --no-backup)
#   3. stops web + scheduler so nothing writes during the reset
#   4. drops and recreates the database (the Postgres volume itself is kept)
#   5. starts web again; its entrypoint re-runs migrations (rebuilds tables,
#      the account-number sequence and the purge triggers)
#   6. starts the scheduler and goes straight into the super-admin prompts
#
# Restore a backup made by this script:
#   dc="docker compose -f docker-compose.prod.yml"; cd production
#   $dc stop web scheduler
#   $dc exec -T db sh -c 'psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\" WITH (FORCE)" -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\""'
#   gunzip -c data/backups/<file>.sql.gz | $dc exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
#   $dc up -d web scheduler
set -euo pipefail

cd "$(dirname "$0")/.."          # production/
COMPOSE_FILE="docker-compose.prod.yml"
dc() { docker compose -f "$COMPOSE_FILE" "$@"; }
die() { echo "ERROR: $*" >&2; exit 1; }

DO_BACKUP=1
WIPE_MEDIA=0
MAKE_ADMIN=1
for arg in "$@"; do
  case "$arg" in
    --no-backup)  DO_BACKUP=0 ;;
    --wipe-media) WIPE_MEDIA=1 ;;
    --no-superuser) MAKE_ADMIN=0 ;;
    -h|--help)    sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)            die "unknown option: $arg (try --help)" ;;
  esac
done

[ -f "$COMPOSE_FILE" ] || die "$COMPOSE_FILE not found in $(pwd)"
[ -f .env ] || die ".env not found in $(pwd); this script is for the production stack"

# The database must be running to ask it for its own name.
dc up -d --wait db >/dev/null
DB_NAME="$(dc exec -T db printenv POSTGRES_DB | tr -d '\r\n')"
[ -n "$DB_NAME" ] || die "could not read POSTGRES_DB from the db container"

echo
echo "  PRODUCTION DATABASE RESET"
echo "  ------------------------------------------------------------"
echo "  Database : $DB_NAME  (stack: $(pwd))"
echo "  Deletes  : every account, extension, attendance sheet, tithe record,"
echo "             prayer request, goal, notification and the audit log."
if [ "$WIPE_MEDIA" = 1 ]; then
  echo "  Media    : uploaded files in data/media WILL ALSO BE DELETED"
else
  echo "  Media    : uploaded files are kept (they will be orphaned; use --wipe-media to delete)"
fi
if [ "$DO_BACKUP" = 1 ]; then
  echo "  Backup   : a dump is saved to data/backups/ first"
else
  echo "  Backup   : NONE (--no-backup). This cannot be undone."
fi
echo "  The site is offline while this runs."
echo
read -r -p "  Type 'reset $DB_NAME' to continue: " answer
[ "$answer" = "reset $DB_NAME" ] || { echo "Cancelled. Nothing was changed."; exit 1; }

if [ "$DO_BACKUP" = 1 ]; then
  mkdir -p data/backups
  backup="data/backups/pre-reset-$(date +%Y%m%d-%H%M%S).sql.gz"
  echo "==> Saving backup to $backup"
  if ! dc exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip > "$backup" \
     || [ "$(stat -c %s "$backup")" -lt 200 ]; then
    rm -f "$backup"
    die "backup failed, so nothing was reset. Fix that or re-run with --no-backup."
  fi
  chmod 600 "$backup"
  echo "    saved ($(du -h "$backup" | cut -f1))"
fi

echo "==> Stopping web and scheduler"
dc stop web scheduler >/dev/null

echo "==> Recreating database $DB_NAME"
dc exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
  -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\" WITH (FORCE)" \
  -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\""' >/dev/null

if [ "$WIPE_MEDIA" = 1 ]; then
  echo "==> Deleting uploaded media"
  dc run --rm --no-deps --entrypoint sh web -c 'find /app/media -mindepth 1 -delete'
fi

echo "==> Starting web (runs migrations; this can take a minute)"
dc up -d --wait web
echo "==> Starting scheduler"
dc up -d scheduler >/dev/null

if [ "$MAKE_ADMIN" = 1 ]; then
  echo
  echo "==> Create the first super-admin"
  echo "    At 'Account id' just press Enter (it is generated), then enter first name, last name and password."
  dc exec web python manage.py createsuperuser \
    || echo "    Skipped or failed. Run it later: docker compose -f $COMPOSE_FILE exec web python manage.py createsuperuser"
fi

echo
echo "Done. $DB_NAME is empty and migrated."
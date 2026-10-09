#!/usr/bin/env bash
# Update the production site to the newest code on its git branch.
#
#   bash production/deploy/update.sh              # show what changed, ask, then deploy
#   bash production/deploy/update.sh -y           # no questions
#   bash production/deploy/update.sh --force      # rebuild even if there is nothing new
#   bash production/deploy/update.sh --backup     # take a database backup even without migrations
#   bash production/deploy/update.sh --no-backup  # skip the automatic backup before migrations
#
# What it does:
#   1. fetches and lists what changed (migrations, styles/templates, app code, requirements, env/settings)
#   2. if there are NEW MIGRATIONS: saves a database backup first (stops if that fails)
#   3. fast-forwards the code (refuses if the server copy has local edits or has diverged)
#   4. rebuilds the image: app code, templates and Tailwind CSS are baked into it, so a bare
#      `git pull` changes nothing visible. Then restarts web + scheduler; migrate and collectstatic
#      run automatically when web starts
#   5. checks: no unapplied migrations, stylesheet rebuilt, site answers
set -euo pipefail

main() {
  cd "$(dirname "$0")/.."        # production/
  local COMPOSE_FILE="docker-compose.prod.yml"
  dc() { docker compose -f "$COMPOSE_FILE" "$@"; }
  die() { echo "ERROR: $*" >&2; exit 1; }
  say() { echo "==> $*"; }

  local ASSUME_YES=0 FORCE=0 BACKUP=auto arg
  for arg in "$@"; do
    case "$arg" in
      -y|--yes)    ASSUME_YES=1 ;;
      --force)     FORCE=1 ;;
      --backup)    BACKUP=yes ;;
      --no-backup) BACKUP=no ;;
      -h|--help)   sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
      *)           die "unknown option: $arg (try --help)" ;;
    esac
  done

  [ -f "$COMPOSE_FILE" ] || die "$COMPOSE_FILE not found in $(pwd)"
  [ -f .env ] || die ".env not found in $(pwd); this script is for the production stack"
  local REPO; REPO="$(git rev-parse --show-toplevel 2>/dev/null)" || die "not inside a git repository"
  g() { git -C "$REPO" "$@"; }

  local branch
  branch="$(g symbolic-ref --short -q HEAD)" || die "detached HEAD (after a rollback?). Run: git -C $REPO switch main"
  g rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1 || die "branch '$branch' has no upstream to update from"
  if [ -n "$(g status --porcelain --untracked-files=no)" ]; then
    g status --short --untracked-files=no >&2
    die "the server copy has uncommitted edits (listed above). Discard them (git restore <file>) or commit them from your dev machine."
  fi

  say "Checking for new commits"
  g fetch --quiet || die "git fetch failed (network or credentials?)"
  local OLD NEW changed="" migrations=""
  OLD="$(g rev-parse HEAD)"; NEW="$(g rev-parse '@{u}')"
  if [ "$OLD" != "$NEW" ] && ! g merge-base --is-ancestor "$OLD" "$NEW"; then
    die "this copy has commits that are not on the remote, or the history diverged. Fix it in git first."
  fi
  if [ "$OLD" = "$NEW" ]; then
    if [ "$FORCE" = 0 ]; then
      echo "Already up to date ($(g rev-parse --short HEAD)). Use --force to rebuild anyway."
      exit 0
    fi
    echo "Nothing new, rebuilding because of --force."
  else
    changed="$(g diff --name-only "$OLD" "$NEW")"
    migrations="$(g diff --name-only --diff-filter=AMR "$OLD" "$NEW" | grep -E '(^|/)migrations/[0-9][^/]*\.py$' || true)"
    echo
    g log --oneline --no-decorate "$OLD..$NEW" | head -15
    echo
  fi

  has() { printf '%s\n' "$changed" | grep -Eq "$1"; }
  local n_files; n_files="$(printf '%s\n' "$changed" | grep -c . || true)"
  echo "  $n_files file(s) changed:"
  [ -n "$migrations" ]                           && echo "   - NEW MIGRATIONS (database structure changes):" && printf '       %s\n' $migrations
  has '(\.html$|^static/css/|^theme/)'           && echo "   - templates / styles  -> Tailwind CSS will be rebuilt"
  has '\.py$'                                    && echo "   - Python code         -> app and scheduler restart"
  has '^requirements[^/]*\.txt$'                 && echo "   - requirements        -> packages reinstalled"
  has '^static/'                                 && echo "   - static assets       -> republished by collectstatic"
  has '^production/'                             && echo "   - production/ files   -> image or compose settings changed"
  has '(^|/)(env\.production\.example|\.env\.example)$|^church_management/settings\.py$' \
    && echo "   - settings / env example changed -> check production/.env for NEW variables after this (see notes at the end)"
  echo

  local do_backup=0
  case "$BACKUP" in
    yes)  do_backup=1 ;;
    no)   [ -n "$migrations" ] && echo "  WARNING: new migrations but --no-backup given." ;;
    auto) [ -n "$migrations" ] && do_backup=1 ;;
  esac
  [ "$do_backup" = 1 ] && echo "  A database backup will be saved first."
  echo "  The site is unavailable for a short while during the restart."
  echo
  if [ "$ASSUME_YES" = 0 ]; then
    read -r -p "Deploy now? [Y/n] " yn
    case "${yn:-Y}" in [Yy]*) ;; *) echo "Cancelled. Nothing was changed."; exit 1 ;; esac
  fi

  local backup=""
  if [ "$do_backup" = 1 ]; then
    dc up -d --wait db >/dev/null
    mkdir -p data/backups
    backup="data/backups/pre-update-$(date +%Y%m%d-%H%M%S)-$(g rev-parse --short "$OLD").sql.gz"
    say "Saving database backup to $backup"
    if ! dc exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip > "$backup" \
       || [ "$(stat -c %s "$backup")" -lt 200 ]; then
      rm -f "$backup"
      die "backup failed, so nothing was changed. Fix that, or re-run with --no-backup."
    fi
    chmod 600 "$backup"
    echo "    saved ($(du -h "$backup" | cut -f1))"
  fi

  if [ "$OLD" != "$NEW" ]; then
    say "Updating code ($(g rev-parse --short "$OLD") -> $(g rev-parse --short "$NEW"))"
    g merge --ff-only --quiet "$NEW"
  fi

  local stamp; stamp="$(mktemp)"; trap 'rm -f "$stamp"' EXIT
  say "Rebuilding and restarting (a few minutes; web migrates and collects static files on start)"
  if ! dc up -d --build --wait; then
    echo >&2
    echo "ERROR: the rebuild or restart failed." >&2
    echo "  - If the BUILD failed, the old containers are still running the old version." >&2
    echo "  - Logs: docker compose -f $PWD/$COMPOSE_FILE logs --tail 100 web" >&2
    echo "  - Go back to the previous code: git -C $REPO checkout $(g rev-parse --short "$OLD") && re-run this script with --force" >&2
    [ -n "$backup" ] && echo "  - If a migration already ran, restore $backup (restore steps: DEPLOY.md, section 5)." >&2
    exit 1
  fi

  say "Checking the result"
  local problems=0

  if dc exec -T web python manage.py migrate --check >/dev/null 2>&1; then
    echo "  [ok]   all migrations applied"
  else
    echo "  [FAIL] unapplied migrations remain: docker compose -f $COMPOSE_FILE exec web python manage.py migrate"; problems=1
  fi

  local css="data/static/css/output.css"
  if [ -s "$css" ] && [ "$css" -nt "$stamp" ]; then
    echo "  [ok]   stylesheet rebuilt and published ($(du -h "$css" | cut -f1))"
  elif [ -s "$css" ]; then
    echo "  [warn] stylesheet exists but was not refreshed by this deploy ($css)"; problems=1
  else
    echo "  [FAIL] $css is missing or empty: pages will be unstyled. Check the build log."; problems=1
  fi

  envget() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d "\"' " || true; }
  local host port code="" i
  host="$(envget ALLOWED_HOSTS | cut -d, -f1)"; port="$(envget WEB_PORT)"; port="${port:-8090}"
  if [ -n "$host" ]; then
    for i in 1 2 3 4 5; do
      code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -H "Host: $host" "http://127.0.0.1:$port/" || true)"
      case "$code" in 200|301|302) break ;; esac
      sleep 3
    done
    case "$code" in
      200|301|302) echo "  [ok]   site answers (HTTP $code)" ;;
      *)           echo "  [FAIL] site answered HTTP ${code:-none}. See: docker compose -f $COMPOSE_FILE logs --tail 100 web"; problems=1 ;;
    esac
  else
    echo "  [skip] site check (ALLOWED_HOSTS not found in .env)"
  fi

  echo
  [ -n "$backup" ] && echo "Backup of the previous database: $backup  (code before this update: $(g rev-parse --short "$OLD"))"
  has '(\.html$|^static/css/|^theme/)' && \
    echo "Styles changed: hard-refresh the browser (Ctrl+Shift+R). If it still looks old, Cloudflare may be serving a cached copy: purge /static/css/output.css (Caching > Configuration > Purge Cache)."
  has '(^|/)(env\.production\.example|\.env\.example)$|^church_management/settings\.py$' && \
    echo "Settings/env changed: compare 'git -C $REPO diff $(g rev-parse --short "$OLD") HEAD -- production/env.production.example .env.example' with production/.env and add any new variables, then: docker compose -f $COMPOSE_FILE up -d"
  if [ "$problems" = 0 ]; then echo "Update complete."; else echo "Update finished WITH PROBLEMS (see above)."; exit 1; fi
}

main "$@"
#!/usr/bin/env bash
# collect_brick.sh: bundle the files one dashboard brick needs into a single text file.
#
# Run from the project root (the folder that has manage.py):
#   ./collect_brick.sh list          show every brick and the files it needs
#   ./collect_brick.sh 1             write brick-context/brick-1-context.txt
#   ./collect_brick.sh 2 --zip       also write brick-context/brick-2-files.zip (paths kept)
#
# Upload the .txt (or the zip) together with the matching brick section of
# DASHBOARD-PLAN.md. Missing files are listed at the end: a file a brick will
# CREATE is expected to be missing, anything else may live at another path.

set -u
shopt -s nullglob

OUT_DIR="${OUT_DIR:-brick-context}"

# One function per brick. One path or glob per line. Each brick repeats every
# file it needs on purpose, so a brick can be built in a fresh chat or account.

brick_1() { cat <<'EOF'
apps/dashboards/services.py
apps/dashboards/views.py
apps/dashboards/urls.py
apps/dashboards/tests/*.py
apps/attendance/models.py
apps/attendance/summary.py
apps/tithes/models.py
apps/prayer/models.py
apps/prayer/permissions.py
apps/events/permissions.py
apps/accounts/models.py
apps/core/models.py
EOF
}

brick_2() { cat <<'EOF'
apps/dashboards/services.py
apps/dashboards/charts.py
apps/dashboards/views.py
apps/dashboards/urls.py
apps/dashboards/templates/dashboards/*.html
apps/dashboards/tests/*.py
apps/accounts/templatetags/*.py
apps/accounts/navigation.py
static/css/input.css
EOF
}

brick_3() { cat <<'EOF'
apps/dashboards/services.py
apps/dashboards/charts.py
apps/dashboards/views.py
apps/dashboards/urls.py
apps/dashboards/templates/dashboards/*.html
apps/dashboards/tests/*.py
apps/accounts/navigation.py
apps/accounts/decorators.py
apps/accounts/templatetags/*.py
templates/includes/section_tabs.html
apps/buddy/services.py
apps/faith/overview.py
apps/events/permissions.py
static/css/input.css
EOF
}

brick_4() { cat <<'EOF'
apps/dashboards/services.py
apps/dashboards/charts.py
apps/dashboards/views.py
apps/dashboards/urls.py
apps/dashboards/area_urls.py
apps/dashboards/templates/dashboards/*.html
apps/dashboards/tests/*.py
apps/attendance/templates/attendance/print.html
apps/tithes/templates/tithes/print.html
apps/prayer/area_urls.py
apps/accounts/navigation.py
apps/accounts/templatetags/*.py
church_management/urls.py
EOF
}

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

list_all() {
  local n
  for n in 1 2 3 4; do
    echo "== Brick $n =="
    "brick_$n" | sed 's/^/  /'
    echo
  done
}

build() {
  local n="$1" zip_it="${2:-}"
  if ! declare -F "brick_$n" >/dev/null; then
    echo "Unknown brick: $n (use 1 to 4, or 'list')" >&2
    exit 1
  fi
  if [ ! -f manage.py ]; then
    echo "Run this from the project root (manage.py not found here)." >&2
    exit 1
  fi

  mkdir -p "$OUT_DIR"
  local out="$OUT_DIR/brick-$n-context.txt"
  local -a files=() missing=()
  local -A seen=()
  local pattern f
  local -a matches

  while IFS= read -r pattern; do
    [ -z "$pattern" ] && continue
    # Unquoted on purpose: lets the shell expand globs; nullglob drops non-matches.
    matches=( $pattern )
    local found=0
    for f in "${matches[@]}"; do
      # A plain path that does not exist stays in the list (nullglob only
      # drops real globs), so check every match is an actual file.
      [ -f "$f" ] || continue
      found=1
      [ -n "${seen[$f]:-}" ] && continue
      seen[$f]=1
      files+=("$f")
    done
    [ "$found" -eq 0 ] && missing+=("$pattern")
  done < <("brick_$n")

  {
    echo "# Brick $n context, collected $(date '+%Y-%m-%d %H:%M')"
    echo "# ${#files[@]} files. Each file starts with a ===== path ===== line."
    echo
    for f in "${files[@]}"; do
      echo "===== $f ====="
      cat "$f"
      echo
    done
  } > "$out"

  local chars
  chars=$(wc -c < "$out" | tr -d ' ')
  echo "Wrote $out"
  echo "  files: ${#files[@]}   size: $chars chars   about $((chars / 4)) tokens"

  if [ "${#missing[@]}" -gt 0 ]; then
    echo "  not found (new file for this brick, or a different path):"
    for pattern in "${missing[@]}"; do echo "    - $pattern"; done
  fi

  if [ "$zip_it" = "--zip" ]; then
    if command -v zip >/dev/null 2>&1; then
      local z="$OUT_DIR/brick-$n-files.zip"
      rm -f "$z"
      zip -q "$z" "${files[@]}"
      echo "Wrote $z"
    else
      echo "  zip is not installed, skipped the zip (sudo apt install zip)." >&2
    fi
  fi
}

case "${1:-}" in
  list)        list_all ;;
  1|2|3|4)     build "$1" "${2:-}" ;;
  -h|--help|"") usage 0 ;;
  *)           usage 1 ;;
esac

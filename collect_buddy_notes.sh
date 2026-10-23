#!/usr/bin/env bash
set -u

OUT="buddy_notes_context"
rm -rf "$OUT" "$OUT.zip"
mkdir -p "$OUT"

# Copy a file, keeping its folder structure. If the exact path doesn't exist,
# look for it anywhere in the project by path suffix.
copy_file() {
  local f="$1"
  if [ -f "$f" ]; then
    cp --parents "$f" "$OUT/"
    return
  fi
  local found
  found=$(find . -path "*/$f" -type f \
    -not -path "./$OUT/*" -not -path "*/node_modules/*" -not -path "*/.venv/*" | head -n 1)
  if [ -n "$found" ]; then
    cp --parents "$found" "$OUT/"
  else
    echo "WARNING: missing $f"
  fi
}

FILES=(
  "apps/buddy/models.py"
  "notifications/services.py"
  "notifications/push.py"
)
for f in "${FILES[@]}"; do
  copy_file "$f"
done

# Existing Alpine components that poll or post (x-data plus fetch / setInterval / setTimeout / $fetch)
grep -rlE "x-data|Alpine\.data" \
  --include="*.html" --include="*.js" \
  . 2>/dev/null \
  | grep -vE "^\./$OUT/|node_modules|\.venv|staticfiles|/dist/|\.min\.js" \
  | while read -r f; do
      if grep -qE "fetch\(|setInterval|\\\$fetch|XMLHttpRequest" "$f"; then
        cp --parents "$f" "$OUT/"
      fi
    done

# Zip it
zip -r "$OUT.zip" "$OUT" >/dev/null

echo
echo "Collected files:"
find "$OUT" -type f | sort
echo
echo "Created $OUT.zip"
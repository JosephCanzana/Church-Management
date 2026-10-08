#!/usr/bin/env python3
"""apply.py: copy the extension-pages update into the Church-Management project.

Unzip the archive in the project root (the folder with manage.py), then run:

    python apply.py            # apply (asks nothing, backs up first)
    python apply.py --dry-run  # show what would change, touch nothing
    python apply.py --revert   # undo the last apply from its backup

Every file that already exists is copied to .apply_backup/<timestamp>/ before it is
overwritten. Files that did not exist are recorded, so --revert deletes them again.
No migration is needed. Needs only Python 3.
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path.cwd()
HERE = Path(__file__).resolve().parent
PAYLOAD = HERE / "payload"
BACKUPS = ROOT / ".apply_backup"


def payload_files():
    return sorted(p.relative_to(PAYLOAD) for p in PAYLOAD.rglob("*") if p.is_file())


def check_root():
    if not (ROOT / "manage.py").exists():
        sys.exit("Run this from the project root (the folder that contains manage.py).")
    if not PAYLOAD.is_dir():
        sys.exit("The payload folder is missing. Unzip the whole archive first.")


def apply(dry):
    files = payload_files()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = BACKUPS / stamp
    manifest = {"created": [], "replaced": []}
    for rel in files:
        dest = ROOT / rel
        same = dest.exists() and dest.read_bytes() == (PAYLOAD / rel).read_bytes()
        state = "same" if same else ("replace" if dest.exists() else "new")
        print(f"  {state:8} {rel}")
        if dry or same:
            continue
        if dest.exists():
            saved = backup / rel
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, saved)
            manifest["replaced"].append(str(rel))
        else:
            manifest["created"].append(str(rel))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PAYLOAD / rel, dest)
    if dry:
        print("\nDry run only: nothing was changed.")
        return
    if manifest["created"] or manifest["replaced"]:
        backup.mkdir(parents=True, exist_ok=True)
        (backup / "manifest.json").write_text(json.dumps(manifest, indent=2))
        print(f"\nApplied. Backup of replaced files: {backup.relative_to(ROOT)}")
    else:
        print("\nNothing to do: every file already matches.")
        return
    # templatetags needs to be a package for {% load ui_tags %}
    print(
        "\nNext:\n"
        "  make tailwind-build\n"
        "  docker compose exec web python manage.py check\n"
        "  docker compose exec web python manage.py test core.test_text accounts.test_extension_address -v 2\n"
        "If a replaced file was edited since this update was made, compare it with the backup."
    )


def revert():
    if not BACKUPS.is_dir() or not any(BACKUPS.iterdir()):
        sys.exit("No backup found.")
    latest = sorted(p for p in BACKUPS.iterdir() if (p / "manifest.json").exists())[-1]
    manifest = json.loads((latest / "manifest.json").read_text())
    for rel in manifest["replaced"]:
        shutil.copy2(latest / rel, ROOT / rel)
        print(f"  restored {rel}")
    for rel in manifest["created"]:
        target = ROOT / rel
        if target.exists():
            target.unlink()
            print(f"  removed  {rel}")
    shutil.rmtree(latest)
    print(f"\nReverted using {latest.name}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="show changes without writing")
    parser.add_argument("--revert", action="store_true", help="undo the last apply")
    args = parser.parse_args()
    check_root()
    revert() if args.revert else apply(args.dry_run)


if __name__ == "__main__":
    main()

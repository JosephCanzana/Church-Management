"""Bulk add of extensions and people (Site settings > Bulk add).

Two steps, same code path:
  * `validate()` reads the file and judges every row. It writes NOTHING.
  * `commit()` validates AGAIN (so a duplicate created since the preview is still caught), then adds each
    importable row in its OWN transaction. One bad row never undoes or blocks the others.

Rules this file keeps (see AGENTS.md):
  * Super-admin only: `require_site_settings_manager` runs first, the view adds `role_required`.
  * Rows go through the SAME services as the single-add screens, so every rule, audit row and notification
    applies unchanged. Nothing here writes to the models directly.
  * Passwords are shown once and never stored, logged or put in the session.
  * Personal data (names, birth dates, phones, addresses) never reaches the logs or the summary audit row,
    which holds counts only.
  * A one-time submission token stops a double submit; a transaction-level advisory lock plus a re-check inside
    each row's transaction stops two imports racing to create the same person.

Everything that calls an EXISTING service is in the ADAPTERS block below. If a signature differs in your code,
that block is the only place to change.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.accounts.bulk_import import parser, rules
from apps.accounts.permissions import require_site_settings_manager
from apps.core.services import ServiceError  # VERIFY: where ServiceError / token helpers live

logger = logging.getLogger(__name__)

KINDS = ("extensions", "people")
ADVISORY_LOCK_KEY = 727_001  # any constant; shared by every bulk add so two cannot interleave per row

SPECS = {"extensions": rules.EXTENSION_SPEC, "people": rules.PEOPLE_SPEC}
TEMPLATE_COLUMNS = {
    "extensions": ("name", "country", "province", "municipality", "barangay", "postal_code", "street", "building_number"),
    "people": ("first_name", "middle_name", "last_name", "suffix", "gender", "birth_date", "extension",
               "person_type", "phone_number"),
}


@dataclass
class Outcome:
    """What happened to one importable row during commit."""

    line: int
    label: str
    status: str  # "created" | "skipped" | "failed"
    message: str = ""


@dataclass
class Report:
    """Everything the preview and result screens need."""

    kind: str
    file_problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    results: list[rules.RowResult] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)
    secrets: list[list[str]] = field(default_factory=list)  # show-once rows: account id, name, password

    @property
    def summary(self) -> dict[str, int]:
        return rules.summarize(self.results)

    @property
    def created(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "created")

    @property
    def not_created(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status != "created"]


# =====================================================================================================================
# ADAPTERS: the only code that touches existing services. VERIFY each call against your real signatures.
# =====================================================================================================================

def _all_extensions() -> dict[str, list[tuple[int, bool]]]:
    """norm(name) -> [(id, archived)] for every extension, loaded once per run."""
    from apps.accounts.models import Extension

    out: dict[str, list[tuple[int, bool]]] = {}
    for pk, name, archived_at in Extension.objects.values_list("id", "name", "archived_at"):
        out.setdefault(rules.norm(name), []).append((pk, archived_at is not None))
    return out


def _check_extension_data(data: dict) -> list[str]:
    """Real address rules for an extension (province > municipality > barangay, 4-digit postal code)."""
    from apps.accounts.services import clean_extension_data  # documented in AGENTS.md

    try:
        clean_extension_data(data)  # VERIFY: raises ServiceError / ValidationError, or returns errors
    except ServiceError as exc:
        return [str(exc)]
    except Exception as exc:  # a Django ValidationError carries .messages
        return list(getattr(exc, "messages", [str(exc)]))
    return []


def _check_person_data(data: dict) -> list[str]:
    """Real contact rules: a partly filled address needs a country; Philippine address checks."""
    from apps.accounts import address
    from apps.accounts.services import users

    errors: list[str] = []
    try:
        users.check_contact({k: data.get(k, "") for k in ("phone_number", *address.ADDRESS_FIELDS)})  # VERIFY
    except Exception as exc:
        errors.extend(getattr(exc, "messages", [str(exc)]))
    return errors


def _find_person_duplicates(data: dict) -> list[str]:
    """Messages for people already in the database with the same name (and birth date rule)."""
    from apps.accounts.services import users

    matches = users.find_duplicate_people(  # VERIFY: argument names and return type
        first_name=data["first_name"], middle_name=data["middle_name"], last_name=data["last_name"],
        suffix=data["suffix"], birth_date=data["birth_date"],
    )
    return [f"Already exists as Account ID {p.account_id} (same name{' and birth date' if data['birth_date'] else ''})."
            for p in matches]


def _extension_exists(name: str) -> bool:
    from apps.accounts.models import Extension

    return Extension.objects.filter(name__iexact=" ".join(name.split())).exists()


def _create_extension(actor, request, data: dict) -> str:
    """Create one extension through the normal service. Returns a label for the outcome."""
    from apps.accounts.services import create_extension

    ext = create_extension(actor, request, data)  # VERIFY: argument order, and whether it wants the whole dict
    return getattr(ext, "name", data["name"])


def _create_person(actor, request, data: dict) -> tuple[str, str | None]:
    """Create one person. Returns (account id, password or None when the type has no sign-in)."""
    from apps.accounts.models import Extension
    from apps.accounts.services import users
    from apps.core.services import new_submission_token

    payload = {**data, "extension": Extension.objects.get(pk=data["extension_id"]), "role": "member"}
    # create_user spends its own one-time token, so every row needs a fresh one.
    result = users.create_user(actor, request, payload, token=new_submission_token())
    if result.duplicate:
        raise ServiceError("Already exists (blocked by the submission token).")
    password = result.password
    # PasswordInfo may expose .value or .password; show it only for people who can sign in.
    secret = getattr(password, "value", None) or getattr(password, "password", None) or (
        password if isinstance(password, str) else None
    )
    return result.user.account_id, secret


def _audit_summary(actor, request, kind: str, report: Report) -> None:
    """One summary row with COUNTS ONLY (each person already got their own row from the create service)."""
    from apps.audit.services import log_action

    log_action(
        action=f"{'extension' if kind == 'extensions' else 'account'}.bulk_import",
        actor=actor,
        request=request,
        entity_type="extension" if kind == "extensions" else "app_user",
        entity_id=None,
        entity_label="bulk add",
        after={"created": report.created, "skipped": len(report.not_created), "rows": len(report.results)},
    )

def _consume_token(token: str) -> None:
    from apps.core.services import consume_submission_token  # VERIFY import path

    with transaction.atomic():
        consume_submission_token(token)


# =====================================================================================================================

def _read(kind: str, upload: bytes | None, pasted: str) -> tuple[str, Report]:
    """Return (text, report with any file-level problem filled in)."""
    report = Report(kind=kind)
    try:
        text, notes = parser.read_input(upload, pasted)
    except parser.FileProblem as exc:
        report.file_problems.append(str(exc))
        return "", report
    report.notes.extend(notes)
    return text, report


def validate(actor, kind: str, upload: bytes | None = None, pasted: str = "") -> tuple[str, Report]:
    """Judge every row without writing anything. Returns (the text that was read, the report)."""
    require_site_settings_manager(actor)
    if kind not in KINDS:
        raise ServiceError("Unknown bulk add type.")
    text, report = _read(kind, upload, pasted)
    if report.file_problems:
        return text, report
    parsed = parser.parse(text, SPECS[kind])
    report.notes.extend(parsed.notes)
    report.file_problems.extend(parsed.problems)
    if report.file_problems:
        return text, report
    if kind == "extensions":
        existing = {k: all(a for _, a in v) for k, v in _all_extensions().items()}
        report.results = rules.validate_extensions(parsed.rows, existing, _check_extension_data)
    else:
        report.results = rules.validate_people(
            parsed.rows, _all_extensions(), timezone.localdate(), _check_person_data, _find_person_duplicates
        )
    return text, report


def commit(actor, request, kind: str, text: str, token: str) -> Report:
    """Validate again, then add every importable row, each in its own transaction."""
    _, report = validate(actor, kind, pasted=text)
    if report.file_problems:
        raise ServiceError(" ".join(report.file_problems))
    if not report.summary["importable"]:
        raise ServiceError("There is nothing to add: every row has a problem or already exists.")
    _consume_token(token)  # a spent token cannot be replayed, even by two simultaneous requests

    for row in report.results:
        if not row.importable:
            continue
        outcome = _add_row(actor, request, kind, row, report)
        report.outcomes.append(outcome)
    _audit_summary(actor, request, kind, report)
    return report


def _add_row(actor, request, kind: str, row: rules.RowResult, report: Report) -> Outcome:
    """Add ONE row in its own transaction and turn any failure into a plain outcome."""
    try:
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", [ADVISORY_LOCK_KEY])
            # Re-check inside the lock: someone may have added the same row since the preview.
            if kind == "extensions":
                if _extension_exists(row.data["name"]):
                    return Outcome(row.line, row.label, "skipped", "Already exists (added since the check).")
                label = _create_extension(actor, request, row.data)
            else:
                if _find_person_duplicates(row.data):
                    return Outcome(row.line, row.label, "skipped", "Already exists (added since the check).")
                account_id, secret = _create_person(actor, request, row.data)
                label = row.label
                if secret:
                    report.secrets.append([account_id, label.title(), secret])
        return Outcome(row.line, label, "created")
    except ServiceError as exc:  # the normal rules said no; their message is meant for people
        return Outcome(row.line, row.label, "failed", str(exc))
    except IntegrityError:
        return Outcome(row.line, row.label, "skipped", "Already exists (blocked by the database).")
    except Exception as exc:  # never lose the rest of the file to one surprise
        logger.error("bulk add row failed: kind=%s line=%s error=%s", kind, row.line, type(exc).__name__)
        return Outcome(row.line, row.label, "failed", "Unexpected problem. Nothing was saved for this row.")


def template_csv(kind: str) -> str:
    """A header-only CSV (no sample rows, so an untouched template can never create anyone)."""
    return ",".join(TEMPLATE_COLUMNS[kind]) + "\n"


def problems_csv(report: Report) -> str:
    """Rows that need attention, with a `problem` column, ready to fix and upload again."""
    import csv
    import io

    columns = list(TEMPLATE_COLUMNS[report.kind])

    def safe(value) -> str:
        text = "" if value is None else str(value)
        return "'" + text if text[:1] in rules.FORMULA_START else text  # opening this in Excel must not run anything

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["line", "status", *columns, "problem"])
    for r in report.results:
        if r.status != "ok":
            writer.writerow([r.line, r.status, *(safe(r.values.get(c, "")) for c in columns), safe(" | ".join(r.reasons))])
    return buffer.getvalue()

"""tithes.permissions: ONE place that decides who may see and do what on a tithes report.

Views start from `TithesOffering.objects.for_user(actor)`, and every service calls one of the
`can_*` checks (or `require`) again on the LOCKED row: hiding a button is not access control.

Who is who:
  super-admin, admin  any extension
  coordinator         their own extension only
  special member      a member holding the `tithes_offering` special role, for THAT extension only,
                      and only while they can sign in (not archived, not suspended)

"Own extension" always means the extension of the report's SHEET (`report.attendance.extension`).

Permission table (S = special member, C = coordinator, A = admin or super-admin):
  start a report for a sheet            S seat | C own extension | A any   (never on an archived sheet)
  view / edit the draft                 S drafts | C own extension | A any
                                        a draft is LOCKED for S and C while a bypass request is open
                                        (pending or approved); only A can still edit it
  request a bypass / withdraw it        C only (own extension, draft) | S no | A no
  approve or reject a request           A only (draft)
  finalize                              C own extension | A any (an admin may also bypass directly) | S no
  reopen a finalized report             A only
  manage households                     C own extension | A any | S no
  print or save as PDF                  any report the person can open (a special member: drafts)
  one person's tithe history            A only (the card on the people detail page) | C no | S no

A report whose sheet is ARCHIVED is read-only: every write check below is False for it, while the
view and print checks still answer from `can_view`.

Whether the breakdown balances, or an approved request exists, is NOT decided here: that is the
service's job. These functions answer "may this person do this kind of thing here".

Page-level check for the screens: `can_use_area` (may this person open the tithes screens at all?).
It answers before any report exists; every report is still checked through `for_user` and `can_*`.

Functions take `(actor, report)` and return a bool. They touch the database only to ask who holds a
seat (`has_tithes_role`), to read the sheet when it was not loaded with the report, and to look for an
open bypass request (`can_edit_draft`). Two take another second argument: `can_create` a SHEET (the
report does not exist yet) and `can_manage_households` an extension id.
"""
from django.core.exceptions import PermissionDenied

from apps.accounts.models import Role, UserSpecialRole
from apps.attendance.models import Attendance

from .models import SPECIAL_ROLE, TitheBypassRequest, TithesOffering

_ADMINS = (Role.SUPER_ADMIN, Role.ADMIN)


def _signed_in(actor):
    """True for a real person who can sign in (not anonymous, archived, suspended, a guest or a no-account member)."""
    return actor is not None and getattr(actor, "is_authenticated", False) and actor.is_active


def _own_extension(actor, extension_id):
    """True when `actor` belongs to the extension with this id."""
    return extension_id is not None and actor.extension_id == extension_id


def _sheet_is_open(report):
    """True unless the report's sheet is archived (an archived sheet makes its report read-only)."""
    return report.attendance.status != Attendance.Status.ARCHIVED


def has_tithes_role(user, extension_id):
    """True when `user` holds the tithes special role for that extension and can sign in.

    The seat is looked up per extension because "own extension" is the SHEET's extension.
    """
    if not _signed_in(user) or extension_id is None:
        return False
    return UserSpecialRole.objects.filter(
        user=user, role_id=SPECIAL_ROLE, extension_id=extension_id,
    ).exists()


def has_open_bypass_request(report):
    """True while a bypass request on this report is pending or approved (the draft is then locked)."""
    return report.bypass_requests.filter(status__in=TitheBypassRequest.OPEN_STATUSES).exists()


def can_view(actor, report):
    """May `actor` open this report at all? Same rule as `TithesOffering.objects.for_user`."""
    if not _signed_in(actor):
        return False
    if actor.role in _ADMINS:
        return True
    extension_id = report.attendance.extension_id
    if actor.role == Role.COORDINATOR:
        return _own_extension(actor, extension_id)
    # A special member only reaches drafts; a finalized report is closed to them.
    return report.status == TithesOffering.Status.DRAFT and has_tithes_role(actor, extension_id)


def can_create(actor, sheet):
    """May `actor` start a report for this attendance SHEET (the report does not exist yet)?

    Admin or super-admin: any sheet. Coordinator: a sheet of their own extension. Special member: a sheet
    of the extension where they hold the seat. Never on an archived sheet. Whether the sheet already has a
    report is the service's check (one report per sheet).
    """
    if not _signed_in(actor) or sheet is None or sheet.status == Attendance.Status.ARCHIVED:
        return False
    if actor.role in _ADMINS:
        return True
    if actor.role == Role.COORDINATOR:
        return _own_extension(actor, sheet.extension_id)
    return has_tithes_role(actor, sheet.extension_id)


def can_edit_draft(actor, report):
    """May `actor` change the offering, the entries and the breakdown of this report?

    Needs a draft on a sheet that is not archived, and `can_view`. While a bypass request is open
    (pending or approved) only an admin or super-admin may still edit: for everyone else it is locked.
    """
    if report.status != TithesOffering.Status.DRAFT or not can_view(actor, report) or not _sheet_is_open(report):
        return False
    if actor.role in _ADMINS:
        return True
    return not has_open_bypass_request(report)


def can_review(actor, report):
    """May `actor` open the read-only review of this report (never one on an archived sheet)?"""
    return can_view(actor, report) and _sheet_is_open(report)


def can_finalize(actor, report):
    """May `actor` finalize this draft? The coordinator of the sheet's extension, or an admin or super-admin.

    Never a special member. The open-request lock does not apply: a coordinator finalizes with an
    approved request. Whether the breakdown balances, or an approved request exists, or an admin gave a
    reason to bypass directly, is checked by the service.
    """
    if not _signed_in(actor) or report.status != TithesOffering.Status.DRAFT or not _sheet_is_open(report):
        return False
    if actor.role in _ADMINS:
        return True
    return actor.role == Role.COORDINATOR and _own_extension(actor, report.attendance.extension_id)


def can_reopen(actor, report):
    """May `actor` send a finalized report back to draft? Admin or super-admin only, sheet not archived."""
    return (
        _signed_in(actor)
        and actor.role in _ADMINS
        and report.status == TithesOffering.Status.FINALIZED
        and _sheet_is_open(report)
    )


def can_request_bypass(actor, report):
    """May `actor` file a bypass request on this draft, or withdraw one? ONLY the coordinator of the extension.

    Not an admin (they decide, or bypass directly) and not a special member. Whether the report really
    has a difference, or already has an open request, is the service's check.
    """
    return (
        _signed_in(actor)
        and actor.role == Role.COORDINATOR
        and report.status == TithesOffering.Status.DRAFT
        and _own_extension(actor, report.attendance.extension_id)
        and _sheet_is_open(report)
    )


def can_decide_bypass(actor, report):
    """May `actor` approve or reject a bypass request on this draft? Admin or super-admin only."""
    return (
        _signed_in(actor)
        and actor.role in _ADMINS
        and report.status == TithesOffering.Status.DRAFT
        and _sheet_is_open(report)
    )


def can_manage_households(actor, extension_id):
    """May `actor` create and change the households of this extension (an id, not a report)?

    The coordinator of that extension, or an admin or super-admin. Never a special member.
    """
    if not _signed_in(actor) or extension_id is None:
        return False
    if actor.role in _ADMINS:
        return True
    return actor.role == Role.COORDINATOR and _own_extension(actor, extension_id)


def can_print(actor, report):
    """May `actor` print or save as PDF? Any report they can open (a special member: drafts)."""
    return can_view(actor, report)


def can_view_person_giving(actor):
    """May `actor` see ONE person's tithe history (the card on the people detail page)?

    Admin and super-admin only. A coordinator is deliberately left out: they work with the reports of
    their own extension, but a person's giving across every service is not theirs to browse. Not a
    special member either. This is about the person's whole history, so there is no report to check.
    """
    return _signed_in(actor) and actor.role in _ADMINS


def require(check, actor, obj=None):
    """Raise PermissionDenied unless `check(actor, obj)` is true; services call this on the locked row.

    `check` is one of the functions above, e.g. `require(can_finalize, actor, report)`. For `can_create`
    pass the sheet, and for `can_manage_households` the extension id, as the third argument.
    """
    if not check(actor, obj):
        raise PermissionDenied("You cannot do this on this tithes report.")


# ----------------------------------------------------------------- page-level check
def seat_extension_ids(actor):
    """Ids of the extensions where `actor` holds the tithes special role (empty if they cannot sign in)."""
    if not _signed_in(actor):
        return []
    return list(
        UserSpecialRole.objects.filter(user=actor, role_id=SPECIAL_ROLE).values_list("extension_id", flat=True)
    )


def can_use_area(actor):
    """May `actor` open the tithes screens (the report list)?

    Super-admin and admin: always. Coordinator: when they belong to an extension. Anyone else: only with
    the tithes seat somewhere. This is the page-level gate; each report is still checked through `for_user`.
    """
    if not _signed_in(actor):
        return False
    if actor.role in _ADMINS:
        return True
    if actor.role == Role.COORDINATOR:
        return actor.extension_id is not None
    return bool(seat_extension_ids(actor))

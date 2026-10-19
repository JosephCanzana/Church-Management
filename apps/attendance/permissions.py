"""attendance.permissions: ONE place that decides who may see and do what on a sheet.

Views start from `Attendance.objects.for_user(actor)`, and every service calls one of the
`can_*` checks (or `require`) again: hiding a button is not access control.

Who is who:
  super-admin, admin  any extension
  coordinator         their own extension only
  special member      a member holding the `attendance` special role, for THAT extension only,
                      and only while they can sign in (not archived, not suspended)

Permission table (S = special member, C = coordinator, A = admin or super-admin):
  create a draft                       S own extension | C own extension | A any
  mark members, add first timers       draft only, same people as above
  choose who took the attendance       C own extension, draft | A any draft | S no
  read-only review                     S drafts | C own extension | A any
  save and upload                      C own extension, draft | A only while the extension has no coordinator
  open an uploaded sheet               C read-only | A read-only | S no
  reopen an uploaded sheet             A only
  convert a first timer to a member    C own extension | A any (draft or uploaded sheet) | S no
  print or save as PDF                 any sheet the person can open

Page-level checks for the screens (brick 8): `can_use_area` (may this person open the attendance screens at all?)
and `creatable_extensions` (the extensions they may start a draft for, as a queryset). They answer for the PAGE,
before any sheet exists; every sheet is still checked through `for_user` and the `can_*` functions.

Functions take `(actor, sheet)` and return a bool; none of them touch the database except the
two that must ask who holds a seat (`has_attendance_role`, and the coordinator-less upload rule).
"""
from django.core.exceptions import PermissionDenied

from apps.accounts.models import Extension, Role, UserSpecialRole

from .models import Attendance

#: Code of the special role (see accounts `special_role`).
SPECIAL_ROLE = "attendance"

_ADMINS = (Role.SUPER_ADMIN, Role.ADMIN)


def _signed_in(actor):
    """True for a real person who can sign in (not anonymous, not archived, not suspended, a member with an account: `is_active` includes the person type)."""
    return actor is not None and getattr(actor, "is_authenticated", False) and actor.is_active


def _own_extension(actor, extension_id):
    """True when `actor` belongs to the extension with this id."""
    return extension_id is not None and actor.extension_id == extension_id


def has_attendance_role(user, extension_id):
    """True when `user` holds the attendance special role for that extension and can sign in.

    "Own extension" always means the SHEET's extension, so the seat is looked up per extension.
    """
    if not _signed_in(user) or extension_id is None:
        return False
    return UserSpecialRole.objects.filter(
        user=user, role_id=SPECIAL_ROLE, extension_id=extension_id,
    ).exists()


def can_view(actor, sheet):
    """May `actor` open this sheet at all? Same rule as `Attendance.objects.for_user`."""
    if not _signed_in(actor):
        return False
    if actor.role in _ADMINS:
        return True
    if actor.role == Role.COORDINATOR:
        return _own_extension(actor, sheet.extension_id)
    # A special member only reaches drafts; an uploaded sheet is closed to them.
    return sheet.status == Attendance.Status.DRAFT and has_attendance_role(actor, sheet.extension_id)


def can_create(actor, extension_id):
    """May `actor` start a draft sheet for this extension (the sheet does not exist yet)?"""
    if not _signed_in(actor):
        return False
    if actor.role in _ADMINS:
        return True
    if actor.role == Role.COORDINATOR:
        return _own_extension(actor, extension_id)
    return has_attendance_role(actor, extension_id)


def can_edit_draft(actor, sheet):
    """May `actor` mark people, add or change first timers and set categories on this sheet?"""
    return sheet.status == Attendance.Status.DRAFT and can_view(actor, sheet)


def can_set_taken_by(actor, sheet):
    """May `actor` change who took the attendance on this draft?

    The coordinator of the sheet's extension, or an admin or super-admin. Never a special member.
    """
    return can_edit_draft(actor, sheet) and actor.role != Role.MEMBER


def can_review(actor, sheet):
    """May `actor` open the read-only review (draft or uploaded, never an archived sheet)?"""
    return sheet.status in (Attendance.Status.DRAFT, Attendance.Status.UPLOADED) and can_view(actor, sheet)


def can_upload(actor, sheet):
    """May `actor` save this draft as the locked record?

    The coordinator of the sheet's extension, while the sheet is a draft. Open item in the plan
    (default taken): when the extension has NO coordinator, nobody could ever upload, so an admin
    or super-admin may, and only while that stays true. To make it coordinator-only, delete the
    last `if` block.
    """
    if sheet.status != Attendance.Status.DRAFT or not _signed_in(actor):
        return False
    if actor.role == Role.COORDINATOR:
        return _own_extension(actor, sheet.extension_id)
    if actor.role in _ADMINS:
        return sheet.extension.coordinator_id is None
    return False


def can_reopen(actor, sheet):
    """May `actor` send an uploaded sheet back to draft? Admin or super-admin only."""
    return _signed_in(actor) and actor.role in _ADMINS and sheet.status == Attendance.Status.UPLOADED


def can_convert(actor, sheet):
    """May `actor` turn a first timer of this sheet into a member account (brick 10)?

    The coordinator of the sheet's extension, or an admin or super-admin, on a draft or an uploaded
    sheet. Never a special member, and never an archived sheet. Conversion creates an account, so it
    is a people action as much as an attendance one.
    """
    return (
        can_view(actor, sheet)
        and actor.role != Role.MEMBER
        and sheet.status in (Attendance.Status.DRAFT, Attendance.Status.UPLOADED)
    )


def can_print(actor, sheet):
    """May `actor` print or save as PDF? Any sheet they can open (a special member: drafts)."""
    return can_view(actor, sheet)


def require(check, actor, sheet=None):
    """Raise PermissionDenied unless `check(actor, sheet)` is true; services call this first.

    `check` is one of the functions above, e.g. `require(can_upload, actor, sheet)`. For
    `can_create` pass the extension id as the third argument.
    """
    if not check(actor, sheet):
        raise PermissionDenied("You cannot do this on this attendance sheet.")


# ----------------------------------------------------------------- page-level checks (brick 8)
def seat_extension_ids(actor):
    """Ids of the extensions where `actor` holds the attendance special role (empty if they cannot sign in)."""
    if not _signed_in(actor):
        return []
    return list(
        UserSpecialRole.objects.filter(user=actor, role_id=SPECIAL_ROLE).values_list("extension_id", flat=True)
    )


def can_use_area(actor):
    """May `actor` open the attendance screens (the sheet list and the create form)?

    Super-admin and admin: always. Coordinator: when they belong to an extension. Anyone else: only with the
    attendance seat somewhere. This is the page-level gate; each sheet is still checked through `for_user`.
    """
    if not _signed_in(actor):
        return False
    if actor.role in _ADMINS:
        return True
    if actor.role == Role.COORDINATOR:
        return actor.extension_id is not None
    return bool(seat_extension_ids(actor))


def creatable_extensions(actor):
    """Extensions `actor` may start a draft sheet for, as a queryset (the create form's choices).

    The QUERYSET form of the `can_create` rule, so the form offers exactly what the service accepts.
    Admin and super-admin: every extension that is not archived. Coordinator: their own. Special member: the
    extensions where they hold the seat. Anyone else: none.
    """
    if not _signed_in(actor):
        return Extension.objects.none()
    live = Extension.objects.filter(archived_at__isnull=True)
    if actor.role in _ADMINS:
        return live
    if actor.role == Role.COORDINATOR:
        return live.filter(pk=actor.extension_id) if actor.extension_id else Extension.objects.none()
    return live.filter(pk__in=seat_extension_ids(actor))



def _can_sign_in(actor):
    """True for a signed-in person who can sign in (suspended, archived, guest and no-account people cannot)."""
    return actor is not None and getattr(actor, "is_authenticated", False) and actor.is_active


def seat_extension_ids(actor):
    """Ids of the extensions where `actor` holds the attendance special role (empty if they cannot sign in)."""
    if not _can_sign_in(actor):
        return []
    return list(
        UserSpecialRole.objects.filter(user=actor, role_id="attendance").values_list("extension_id", flat=True)
    )


def can_use_area(actor):
    """True when `actor` may open the attendance screens (the sheet list and the create form).

    Super-admin and admin: always. Coordinator: when they belong to an extension. Anyone else: only
    with the attendance seat somewhere. This is the page-level gate; every sheet is still checked
    through `Attendance.objects.for_user` and the services.
    """
    if not _can_sign_in(actor):
        return False
    if actor.role in (Role.SUPER_ADMIN, Role.ADMIN):
        return True
    if actor.role == Role.COORDINATOR:
        return actor.extension_id is not None
    return bool(seat_extension_ids(actor))


def creatable_extensions(actor):
    """Extensions `actor` may start a draft sheet for, as a queryset (the create form's choices).

    Super-admin and admin: every extension that is not archived. Coordinator: their own. Special
    member: the extensions where they hold the seat. Anyone else: none.
    """
    live = Extension.objects.filter(archived_at__isnull=True)
    if not _can_sign_in(actor):
        return Extension.objects.none()
    if actor.role in (Role.SUPER_ADMIN, Role.ADMIN):
        return live
    if actor.role == Role.COORDINATOR:
        return live.filter(pk=actor.extension_id) if actor.extension_id else Extension.objects.none()
    return live.filter(pk__in=seat_extension_ids(actor))

"""PASTE these three functions into apps/attendance/permissions.py (this file is not imported).

Why they exist: brick 8's screens need three questions answered that `permissions.py` does not answer
yet, and AGENTS.md says that file is the only place that decides who may do what:

  * `can_use_area`        may this person open the attendance screens at all (list and create)?
  * `creatable_extensions` which extensions may they start a draft sheet for (feeds the create form)?
  * `seat_extension_ids`   where do they hold the attendance seat (helper for the two above)?

`creatable_extensions` is the QUERYSET form of the `can_create` rule, so the form can offer exactly the
extensions the service would accept. `create_sheet` still checks permission again on its own.

Imports it needs at the top of permissions.py (add only what is missing):

    from apps.accounts.models import Extension, Role, UserSpecialRole

CHECK: it assumes `Extension` has `archived_at` (it inherits `ArchivableModel`). If your Extension archives
differently, change the two `.filter(archived_at__isnull=True)` lines.
"""


def _can_sign_in(actor):
    """True for a signed-in, active person (suspended and archived people are not)."""
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

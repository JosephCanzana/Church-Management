"""Transactional business rules for managing church resources."""
from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError, RestrictedError

from apps.audit.services import log_action
from apps.core.services import ServiceError, archive, force_delete, restore

from .models import GeneralResource, MonthlyResource, MonthlyTheme, WeeklyPowerpoint
from .permissions import require_resource_force_deleter, require_resource_manager


_UNSET = object()


def _clean_title(value):
    """Trim and require a non-empty resource title."""
    title = (value or "").strip()
    if not title:
        raise ServiceError("Enter a title.")
    return title


def _clean_date(value, label, *, first_of_month=False):
    """Validate a calendar date and optionally require the first of its month."""
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ServiceError(f"Choose a valid {label}.")
    if first_of_month and value.day != 1:
        raise ServiceError("Choose the first day of the month.")
    return value


def _get_locked(model, record_id, label):
    """Return a row lock or raise a user-safe missing-record error."""
    record = model.objects.select_for_update().filter(pk=record_id).first()
    if record is None:
        raise ServiceError(f"That {label} no longer exists.")
    return record


def _validate_and_save(record, *, conflict_message="This resource conflicts with an existing record."):
    """Run model validation and save, translating validation failures to ServiceError."""
    try:
        with transaction.atomic():
            record.full_clean()
            record.save()
    except ValidationError as error:
        raise ServiceError(" ".join(error.messages)) from error
    except IntegrityError as error:
        raise ServiceError(conflict_message) from error


def _audit(action, actor, record, request=None, *, before=None, after=None):
    """Write an audit row containing record identity and non-sensitive metadata only."""
    label = getattr(record, "title", str(record))
    return log_action(
        action,
        actor=actor,
        request=request,
        entity_type=record._meta.db_table,
        entity_id=record.pk,
        entity_label=label,
        before=before,
        after=after,
    )


def _apply_changes(record, changes):
    """Set changed fields and return their names without saving the model."""
    changed = []
    for field, value in changes.items():
        if value is _UNSET:
            continue
        if getattr(record, field) != value:
            setattr(record, field, value)
            changed.append(field)
    return changed


def _create_record(actor, record, action, request, *, after=None, conflict_message=None):
    """Validate, save, and audit a newly constructed resource inside one transaction."""
    with transaction.atomic():
        _validate_and_save(record, conflict_message=conflict_message or "This resource already exists.")
        _audit(action, actor, record, request, after=after)
    return record


def create_monthly_theme(actor, *, month, title, description="", request=None):
    """Create one monthly theme; the database enforces one record per month."""
    require_resource_manager(actor)
    month = _clean_date(month, "month", first_of_month=True)
    record = MonthlyTheme(month=month, title=_clean_title(title), description=description or "", created_by=actor)
    return _create_record(
        actor,
        record,
        "resource.monthly_theme_created",
        request,
        after={"month": month, "is_published": False},
        conflict_message="A theme already exists for that month.",
    )


def update_monthly_theme(
    actor, theme_id, *, month=_UNSET, title=_UNSET, description=_UNSET, request=None,
):
    """Update a draft or published monthly theme and audit changed field names only."""
    require_resource_manager(actor)
    with transaction.atomic():
        theme = _get_locked(MonthlyTheme, theme_id, "monthly theme")
        if theme.is_archived:
            raise ServiceError("Restore this monthly theme before editing it.")
        changes = {
            "month": _clean_date(month, "month", first_of_month=True) if month is not _UNSET else _UNSET,
            "title": _clean_title(title) if title is not _UNSET else _UNSET,
            "description": (description or "") if description is not _UNSET else _UNSET,
        }
        changed = _apply_changes(theme, changes)
        if not changed:
            return False
        _validate_and_save(theme, conflict_message="A theme already exists for that month.")
        _audit(
            "resource.monthly_theme_updated", actor, theme, request,
            before={"changed_fields": changed}, after={"changed_fields": changed},
        )
    return theme


def set_monthly_theme_published(actor, theme_id, published, *, request=None):
    """Publish or unpublish a monthly theme; repeated requests are no-ops."""
    require_resource_manager(actor)
    if not isinstance(published, bool):
        raise ServiceError("Choose whether the monthly theme should be published.")
    with transaction.atomic():
        theme = _get_locked(MonthlyTheme, theme_id, "monthly theme")
        if theme.is_archived:
            raise ServiceError("Restore this monthly theme before changing publication.")
        if theme.is_published == published:
            return False
        before = theme.is_published
        theme.is_published = published
        theme.save(update_fields=["is_published", "updated_at"])
        action = "published" if published else "unpublished"
        _audit(
            f"resource.monthly_theme_{action}", actor, theme, request,
            before={"is_published": before}, after={"is_published": published},
        )
    return True


def _archive_record(actor, model, record_id, entity, request):
    """Archive one row-locked resource, returning False when it was already archived."""
    with transaction.atomic():
        record = _get_locked(model, record_id, entity.replace("_", " "))
        if record.is_archived:
            return False
        archive(record)
        _audit(f"resource.{entity}_archived", actor, record, request)
    return True


def _restore_record(actor, model, record_id, entity, request):
    """Restore one row-locked resource, returning False when it was already active."""
    with transaction.atomic():
        record = _get_locked(model, record_id, entity.replace("_", " "))
        if not record.is_archived:
            return False
        restore(record)
        _audit(f"resource.{entity}_restored", actor, record, request)
    return True


def _force_delete_record(actor, model, record_id, entity, request):
    """Audit and permanently delete an archived resource and its files."""
    with transaction.atomic():
        record = _get_locked(model, record_id, entity.replace("_", " "))
        if not record.is_archived:
            raise ServiceError("Only archived resources can be permanently deleted.")
        _audit(f"resource.{entity}_deleted", actor, record, request)
        try:
            force_delete(record)
        except (ProtectedError, RestrictedError) as error:
            raise ServiceError("This resource is still in use and cannot be deleted.") from error
    return True


def archive_monthly_theme(actor, theme_id, *, request=None):
    """Archive a monthly theme and hide it from the landing page and library."""
    require_resource_manager(actor)
    return _archive_record(actor, MonthlyTheme, theme_id, "monthly_theme", request)


def restore_monthly_theme(actor, theme_id, *, request=None):
    """Restore a previously archived monthly theme."""
    require_resource_manager(actor)
    return _restore_record(actor, MonthlyTheme, theme_id, "monthly_theme", request)


def force_delete_monthly_theme(actor, theme_id, *, request=None):
    """Permanently delete an archived theme and its monthly files."""
    require_resource_force_deleter(actor)
    return _force_delete_record(actor, MonthlyTheme, theme_id, "monthly_theme", request)


def _locked_active_theme(theme_id):
    """Return a locked, non-archived monthly theme or raise a safe service error."""
    theme = MonthlyTheme.objects.select_for_update().filter(pk=theme_id).first()
    if theme is None or theme.is_archived:
        raise ServiceError("Choose an active monthly theme.")
    return theme


def create_monthly_resource(
    actor, theme_id, *, kind, title, file, description="", request=None,
):
    """Attach a validated file to an active monthly theme."""
    require_resource_manager(actor)
    with transaction.atomic():
        theme = _locked_active_theme(theme_id)
        resource = MonthlyResource(
            monthly_theme=theme,
            kind=kind,
            title=_clean_title(title),
            description=description or "",
            file=file,
            uploaded_by=actor,
        )
        _validate_and_save(resource)
        _audit(
            "resource.monthly_resource_created", actor, resource, request,
            after={"monthly_theme_id": theme.pk, "kind": resource.kind},
        )
    return resource


def _locked_monthly_resource(resource_id):
    """Lock a monthly resource after locking its parent theme first."""
    theme_id = MonthlyResource.objects.filter(pk=resource_id).values_list("monthly_theme_id", flat=True).first()
    if theme_id is None:
        raise ServiceError("That monthly resource no longer exists.")
    theme = _locked_active_theme(theme_id)
    resource = _get_locked(MonthlyResource, resource_id, "monthly resource")
    return theme, resource


def update_monthly_resource(
    actor, resource_id, *, kind=_UNSET, title=_UNSET, description=_UNSET, file=_UNSET, request=None,
):
    """Update a monthly resource, replacing its file when a new one is supplied."""
    require_resource_manager(actor)
    with transaction.atomic():
        _theme, resource = _locked_monthly_resource(resource_id)
        changes = {
            "kind": kind,
            "title": _clean_title(title) if title is not _UNSET else _UNSET,
            "description": (description or "") if description is not _UNSET else _UNSET,
            "file": file,
        }
        changed = _apply_changes(resource, changes)
        if not changed:
            return False
        _validate_and_save(resource)
        _audit(
            "resource.monthly_resource_updated", actor, resource, request,
            before={"changed_fields": changed}, after={"changed_fields": changed},
        )
    return resource


def delete_monthly_resource(actor, resource_id, *, request=None):
    """Delete a monthly file record; its file is removed after the row commits."""
    require_resource_manager(actor)
    with transaction.atomic():
        _theme, resource = _locked_monthly_resource(resource_id)
        _audit("resource.monthly_resource_deleted", actor, resource, request)
        resource.delete()
    return True


def _locked_optional_theme(theme_id):
    """Resolve an optional theme id while preventing links to archived themes."""
    if theme_id is None:
        return None
    return _locked_active_theme(theme_id)


def create_weekly_powerpoint(
    actor, *, title, week_start, file, description="", monthly_theme_id=None, request=None,
):
    """Create a weekly presentation with its original upload name retained."""
    require_resource_manager(actor)
    week_start = _clean_date(week_start, "week")
    with transaction.atomic():
        theme = _locked_optional_theme(monthly_theme_id)
        powerpoint = WeeklyPowerpoint(
            title=_clean_title(title),
            week_start=week_start,
            file=file,
            description=description or "",
            monthly_theme=theme,
            uploaded_by=actor,
        )
        _validate_and_save(powerpoint)
        _audit(
            "resource.weekly_powerpoint_created", actor, powerpoint, request,
            after={"week_start": week_start, "monthly_theme_id": theme.pk if theme else None},
        )
    return powerpoint


def update_weekly_powerpoint(
    actor, powerpoint_id, *, title=_UNSET, week_start=_UNSET, description=_UNSET,
    file=_UNSET, monthly_theme_id=_UNSET, request=None,
):
    """Update a weekly presentation and optionally replace its file or theme link."""
    require_resource_manager(actor)
    with transaction.atomic():
        powerpoint = _get_locked(WeeklyPowerpoint, powerpoint_id, "weekly presentation")
        if powerpoint.is_archived:
            raise ServiceError("Restore this weekly presentation before editing it.")
        theme = _UNSET
        if monthly_theme_id is not _UNSET:
            theme = _locked_optional_theme(monthly_theme_id)
        changes = {
            "title": _clean_title(title) if title is not _UNSET else _UNSET,
            "week_start": _clean_date(week_start, "week") if week_start is not _UNSET else _UNSET,
            "description": (description or "") if description is not _UNSET else _UNSET,
            "file": file,
            "monthly_theme": theme,
        }
        changed = _apply_changes(powerpoint, changes)
        if not changed:
            return False
        _validate_and_save(powerpoint)
        _audit(
            "resource.weekly_powerpoint_updated", actor, powerpoint, request,
            before={"changed_fields": changed}, after={"changed_fields": changed},
        )
    return powerpoint


def archive_weekly_powerpoint(actor, powerpoint_id, *, request=None):
    """Archive a weekly presentation."""
    require_resource_manager(actor)
    return _archive_record(actor, WeeklyPowerpoint, powerpoint_id, "weekly_powerpoint", request)


def restore_weekly_powerpoint(actor, powerpoint_id, *, request=None):
    """Restore a previously archived weekly presentation."""
    require_resource_manager(actor)
    return _restore_record(actor, WeeklyPowerpoint, powerpoint_id, "weekly_powerpoint", request)


def force_delete_weekly_powerpoint(actor, powerpoint_id, *, request=None):
    """Permanently delete an archived weekly presentation and its file."""
    require_resource_force_deleter(actor)
    return _force_delete_record(actor, WeeklyPowerpoint, powerpoint_id, "weekly_powerpoint", request)


def create_general_resource(actor, *, title, category, file, description="", request=None):
    """Create a general resource file."""
    require_resource_manager(actor)
    resource = GeneralResource(
        title=_clean_title(title),
        category=category,
        file=file,
        description=description or "",
        uploaded_by=actor,
    )
    return _create_record(
        actor,
        resource,
        "resource.general_resource_created",
        request,
        after={"category": category},
    )


def update_general_resource(
    actor, resource_id, *, title=_UNSET, category=_UNSET,
    description=_UNSET, file=_UNSET, request=None,
):
    """Update a general resource, replacing its file when requested."""
    require_resource_manager(actor)
    with transaction.atomic():
        resource = _get_locked(GeneralResource, resource_id, "general resource")
        if resource.is_archived:
            raise ServiceError("Restore this general resource before editing it.")
        changes = {
            "title": _clean_title(title) if title is not _UNSET else _UNSET,
            "category": category,
            "description": (description or "") if description is not _UNSET else _UNSET,
            "file": file,
        }
        changed = _apply_changes(resource, changes)
        if not changed:
            return False
        _validate_and_save(resource)
        _audit(
            "resource.general_resource_updated", actor, resource, request,
            before={"changed_fields": changed}, after={"changed_fields": changed},
        )
    return resource


def archive_general_resource(actor, resource_id, *, request=None):
    """Archive a general resource."""
    require_resource_manager(actor)
    return _archive_record(actor, GeneralResource, resource_id, "general_resource", request)


def restore_general_resource(actor, resource_id, *, request=None):
    """Restore a previously archived general resource."""
    require_resource_manager(actor)
    return _restore_record(actor, GeneralResource, resource_id, "general_resource", request)


def force_delete_general_resource(actor, resource_id, *, request=None):
    """Permanently delete an archived general resource and its file."""
    require_resource_force_deleter(actor)
    return _force_delete_record(actor, GeneralResource, resource_id, "general_resource", request)
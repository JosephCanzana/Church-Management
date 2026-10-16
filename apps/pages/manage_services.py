"""Business rules for editing landing-page text and image content."""
from django.db import connection, transaction
from django.db import transaction

from apps.audit.services import log_action
from apps.core.services import ServiceError

from .models import ChurchContent, LandingImage
from .permissions import require_landing_manager
from .services import DEFAULT_ABOUT, DEFAULT_HERO


CONTENT_BLOCKS = {
    ChurchContent.Key.LANDING_DESCRIPTION: {
        "label": "Hero introduction",
        "title": "Jesus Is Lord Church",
        "default": DEFAULT_HERO["intro"],
    },
    ChurchContent.Key.MISSION: {
        "label": "Mission",
        **DEFAULT_ABOUT["mission"],
    },
    ChurchContent.Key.VISION: {
        "label": "Vision",
        **DEFAULT_ABOUT["vision"],
    },
    ChurchContent.Key.CORE_VALUES: {
        "label": "Core values",
        **DEFAULT_ABOUT["values"],
    },
    ChurchContent.Key.DONATION_INSTRUCTIONS: {
        "label": "Donation instructions",
        "title": "Donation instructions",
        "default": "",
    },
}
ACTIVE_IMAGE_LIMIT = 12


def landing_content_blocks(actor):
    """Return each managed block with its current text and default/custom state."""
    require_landing_manager(actor)
    rows = {
        row.key: row
        for row in ChurchContent.objects.filter(
            language=ChurchContent.Language.EN,
            key__in=CONTENT_BLOCKS,
        )
    }
    blocks = []
    for key, definition in CONTENT_BLOCKS.items():
        row = rows.get(key)
        custom = bool(row and row.body.strip())
        blocks.append({
            "key": key,
            "label": definition["label"],
            "title": row.title if custom and row.title else definition.get("title", definition.get("label")),
            "body": row.body if custom else definition.get("default", ""),
            "is_custom": custom,
            "version": row.version if custom else 0,
        })
    return blocks


def save_landing_content(actor, key, body, *, request=None):
    """Save English content for one known block, or reset it when body is blank."""
    require_landing_manager(actor)
    if key not in CONTENT_BLOCKS:
        raise ServiceError("Choose a valid landing content block.")
    cleaned_body = (body or "").strip()

    with transaction.atomic():
        row = ChurchContent.objects.select_for_update().filter(
            key=key,
            language=ChurchContent.Language.EN,
        ).first()
        if not cleaned_body:
            if row is None:
                return False
            row_id = row.pk
            old_version = row.version
            row.delete()
            log_action(
                "pages.content_reset",
                actor=actor,
                request=request,
                entity_type="church_content",
                entity_id=row_id,
                entity_label=key,
                before={"key": key, "version": old_version, "custom": True},
                after={"key": key, "custom": False},
            )
            return True

        if row is not None and row.body == cleaned_body:
            return False

        previous_version = row.version if row else 0
        if row is None:
            row = ChurchContent(key=key, language=ChurchContent.Language.EN)
        row.title = ""  # landing service uses its fixed default heading
        row.body = cleaned_body
        row.version = previous_version + 1
        row.updated_by = actor
        row.save()
        log_action(
            "pages.content_saved",
            actor=actor,
            request=request,
            entity_type="church_content",
            entity_id=row.pk,
            entity_label=key,
            before={"key": key, "version": previous_version, "custom": bool(previous_version)},
            after={"key": key, "version": row.version, "custom": True},
        )
    return True


def _lock_image_placement(placement):
    """Serialize placement mutations, including concurrent inserts into an empty pool."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", [f"landing-image:{placement}"])


def _image_order(placement):
    """Return all images in the stable order used by the management screen."""
    return list(LandingImage.objects.select_for_update().filter(placement=placement).order_by("sort_order", "pk"))


def _check_active_capacity(placement, *, excluding_id=None):
    """Refuse a placement change or activation when its pool already has 12 active photos."""
    active = LandingImage.objects.filter(placement=placement, is_active=True)
    if excluding_id is not None:
        active = active.exclude(pk=excluding_id)
    if active.count() >= ACTIVE_IMAGE_LIMIT:
        raise ServiceError(f"A placement can have at most {ACTIVE_IMAGE_LIMIT} active photos.")


def create_landing_image(actor, *, image, caption="", placement=LandingImage.Placement.HERO, request=None):
    """Upload one active public landing photo into the requested placement."""
    require_landing_manager(actor)
    if placement not in LandingImage.Placement.values:
        raise ServiceError("Choose a valid photo placement.")
    with transaction.atomic():
        _lock_image_placement(placement)
        _check_active_capacity(placement)
        images = _image_order(placement)
        record = LandingImage(
            image=image,
            caption=(caption or "").strip(),
            placement=placement,
            sort_order=(images[-1].sort_order + 1) if images else 0,
            is_active=True,
            uploaded_by=actor,
        )
        record.full_clean()
        record.save()
        log_action(
            "pages.landing_image_created",
            actor=actor,
            request=request,
            entity_type="landing_image",
            entity_id=record.pk,
            after={"placement": placement, "is_active": True, "sort_order": record.sort_order},
        )
    return record


def update_landing_image(
    actor, image_id, *, caption=None, placement=None, image=None, request=None,
):
    """Edit a photo caption, move it between pools, or replace its image file."""
    require_landing_manager(actor)
    initial_placement = LandingImage.objects.filter(pk=image_id).values_list("placement", flat=True).first()
    if initial_placement is None:
        raise ServiceError("That landing photo no longer exists.")
    target_placement = initial_placement if placement is None else placement
    if target_placement not in LandingImage.Placement.values:
        raise ServiceError("Choose a valid photo placement.")
    with transaction.atomic():
        for pool in sorted({initial_placement, target_placement}):
            _lock_image_placement(pool)
        record = LandingImage.objects.select_for_update().filter(pk=image_id).first()
        if record is None:
            raise ServiceError("That landing photo no longer exists.")
        if record.placement != initial_placement:
            raise ServiceError("This photo changed placements. Reload and try again.")
        before = {"caption": record.caption, "placement": record.placement}
        changed = []
        if caption is not None:
            cleaned_caption = caption.strip()
            if cleaned_caption != record.caption:
                record.caption = cleaned_caption
                changed.append("caption")
        if target_placement != record.placement:
            if record.is_active:
                _check_active_capacity(target_placement)
            record.placement = target_placement
            changed.append("placement")
            target_images = [item for item in _image_order(target_placement) if item.pk != record.pk]
            record.sort_order = target_images[-1].sort_order + 1 if target_images else 0
        if image is not None:
            record.image = image
            changed.append("image")
        if not changed:
            return False
        record.full_clean()
        record.save()
        after = {"changed_fields": changed, "placement": record.placement}
        if "caption" in changed:
            after["caption_changed"] = True
        log_action(
            "pages.landing_image_updated",
            actor=actor,
            request=request,
            entity_type="landing_image",
            entity_id=record.pk,
            before={"placement": before["placement"], "caption_changed": "caption" in changed},
            after=after,
        )
    return record


def set_landing_image_active(actor, image_id, active, *, request=None):
    """Activate or deactivate a photo; activation obeys the per-placement cap."""
    require_landing_manager(actor)
    if not isinstance(active, bool):
        raise ServiceError("Choose whether this photo should be active.")
    placement = LandingImage.objects.filter(pk=image_id).values_list("placement", flat=True).first()
    if placement is None:
        raise ServiceError("That landing photo no longer exists.")
    with transaction.atomic():
        _lock_image_placement(placement)
        record = LandingImage.objects.select_for_update().filter(pk=image_id).first()
        if record is None:
            raise ServiceError("That landing photo no longer exists.")
        if record.placement != placement:
            raise ServiceError("This photo changed placements. Reload and try again.")
        if record.is_active == active:
            return False
        if active:
            _check_active_capacity(record.placement, excluding_id=record.pk)
        before = record.is_active
        record.is_active = active
        record.save(update_fields=["is_active", "updated_at"])
        log_action(
            "pages.landing_image_toggled",
            actor=actor,
            request=request,
            entity_type="landing_image",
            entity_id=record.pk,
            before={"is_active": before},
            after={"is_active": active, "placement": record.placement},
        )
    return True


def move_landing_image(actor, image_id, direction, *, request=None):
    """Move one photo up or down within its placement; this action is immediate."""
    require_landing_manager(actor)
    if direction not in ("up", "down"):
        raise ServiceError("Choose up or down to reorder photos.")
    placement = LandingImage.objects.filter(pk=image_id).values_list("placement", flat=True).first()
    if placement is None:
        raise ServiceError("That landing photo no longer exists.")
    with transaction.atomic():
        _lock_image_placement(placement)
        record = LandingImage.objects.select_for_update().filter(pk=image_id).first()
        if record is None:
            raise ServiceError("That landing photo no longer exists.")
        if record.placement != placement:
            raise ServiceError("This photo changed placements. Reload and try again.")
        images = _image_order(record.placement)
        index = next(i for i, image in enumerate(images) if image.pk == record.pk)
        neighbor_index = index - 1 if direction == "up" else index + 1
        if neighbor_index < 0 or neighbor_index >= len(images):
            return False
        images[index], images[neighbor_index] = images[neighbor_index], images[index]
        for sort_order, image in enumerate(images):
            if image.sort_order != sort_order:
                image.sort_order = sort_order
                image.save(update_fields=["sort_order", "updated_at"])
        log_action(
            "pages.landing_image_reordered",
            actor=actor,
            request=request,
            entity_type="landing_image",
            entity_id=record.pk,
            after={"placement": record.placement, "direction": direction},
        )
    return True


def delete_landing_image(actor, image_id, *, request=None):
    """Delete a landing photo and its public image after the row deletion commits."""
    require_landing_manager(actor)
    placement = LandingImage.objects.filter(pk=image_id).values_list("placement", flat=True).first()
    if placement is None:
        raise ServiceError("That landing photo no longer exists.")
    with transaction.atomic():
        _lock_image_placement(placement)
        record = LandingImage.objects.select_for_update().filter(pk=image_id).first()
        if record is None:
            raise ServiceError("That landing photo no longer exists.")
        if record.placement != placement:
            raise ServiceError("This photo changed placements. Reload and try again.")
        log_action(
            "pages.landing_image_deleted",
            actor=actor,
            request=request,
            entity_type="landing_image",
            entity_id=record.pk,
            before={"placement": record.placement, "is_active": record.is_active},
        )
        record.delete()
    return True
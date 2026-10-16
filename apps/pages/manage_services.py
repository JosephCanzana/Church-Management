"""Business rules for editing the English landing-page text blocks."""
from django.db import transaction

from apps.audit.services import log_action
from apps.core.services import ServiceError

from .models import ChurchContent
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
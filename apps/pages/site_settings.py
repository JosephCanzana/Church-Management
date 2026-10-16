"""pages.site_settings: the editable "More" links in the landing page footer.

The links are one JSON list stored in a single `site_setting` row (key
`pages_footer_links`), shaped `[{"id", "label", "url"}, ...]` in display order.

Default or saved:
  - No row  -> the built-in links from `services.FOOTER["more"]` are used.
  - A row   -> exactly the saved list is used, even an empty one (an admin who
               deletes every link gets a footer with no links, on purpose).
  - Reset   -> deletes the row, so the page is back on the built-in links.

Two guards, as everywhere else: the views are wrapped in `role_required` and
each writer here starts with `require_landing_manager`. Writers are idempotent:
asking for the state that already exists changes nothing, writes no audit row
and returns False, so a double click or a second tab is harmless.

URL rule (`validate_footer_url`): http and https (with a host), mailto and tel
(with something after the colon), a path that starts with a single "/", or an
in-page anchor that starts with "#". Everything else is refused, including
`javascript:`, `data:`, protocol-relative `//host` and backslash tricks, because
these values end up inside `href` attributes on a public page.

Audit rows hold the link labels and addresses only (they are public text) and
never anything a visitor typed.
"""
import re
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.text import slugify

from apps.audit.services import log_action
from apps.core.models import SiteSetting
from apps.core.services import ServiceError

from .permissions import require_landing_manager
from .services import FOOTER

FOOTER_LINKS_KEY = "pages_footer_links"
MAX_FOOTER_LINKS = 20          # assumed limit; keeps the footer readable
LABEL_MAX_LENGTH = 100
URL_MAX_LENGTH = 500
ALLOWED_SCHEMES = ("http", "https", "mailto", "tel")

AUDIT_SAVED = "pages.footer_links_saved"
AUDIT_RESET = "pages.footer_links_reset"

# Whitespace of any kind, control characters and backslashes are never valid in
# a link address. Browsers silently strip tabs and newlines inside a scheme
# ("java\tscript:"), so they must be refused rather than trimmed.
_FORBIDDEN_URL_CHARS = re.compile(r"[\s\x00-\x1f\x7f-\x9f\\]")


def validate_footer_url(value):
    """Return `value` stripped if it is a safe footer address, else raise ValidationError."""
    url = str(value or "").strip()
    if not url:
        raise ValidationError("Enter a web address.")
    if len(url) > URL_MAX_LENGTH:
        raise ValidationError(f"The address must be {URL_MAX_LENGTH} characters or fewer.")
    if _FORBIDDEN_URL_CHARS.search(url):
        raise ValidationError("The address cannot contain spaces, control characters or backslashes.")
    if url.startswith("//"):
        raise ValidationError("Start the address with https:// or with a single /.")
    if url[0] in "/#":
        return url  # a path on this site, or an anchor on this page
    try:
        parts = urlsplit(url)
    except ValueError:
        raise ValidationError("That address is not valid.") from None
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ValidationError(
            "Use an address that starts with http://, https://, mailto: or tel:, "
            "or a path on this site that starts with /."
        )
    if scheme in ("http", "https"):
        if not parts.hostname:
            raise ValidationError("Add the website name after the https://.")
    elif not parts.path:
        raise ValidationError("Add the email address or phone number after the colon.")
    return url


def _default_links():
    """The built-in links with stable ids, as fresh dicts the caller may change."""
    links = []
    for position, link in enumerate(FOOTER["more"], start=1):
        slug = slugify(link["label"]) or str(position)
        links.append({"id": f"default-{slug}", "label": link["label"], "url": link["url"]})
    return links


def _parse(value):
    """Return a clean list of link dicts from a stored value, or None if it is malformed."""
    if not isinstance(value, list):
        return None
    links = []
    for item in value:
        if not isinstance(item, dict):
            return None
        link_id, label, url = item.get("id"), item.get("label"), item.get("url")
        if not all(isinstance(part, str) and part for part in (link_id, label, url)):
            return None
        try:
            validate_footer_url(url)
        except ValidationError:
            return None  # a tampered row must never reach an href; fall back to defaults
        links.append({"id": link_id, "label": label, "url": url})
    return links


def get_footer_links():
    """The links the footer shows now: the saved list, else the built-in defaults.

    Always returns new dicts, so callers can edit the list before saving it back.
    """
    row = SiteSetting.objects.filter(key=FOOTER_LINKS_KEY).first()
    saved = _parse(row.value) if row is not None else None
    return saved if saved is not None else _default_links()


def _normalize(links):
    """Validate a whole list and return it as `{"id", "label", "url"}` dicts, or raise ServiceError."""
    if len(links) > MAX_FOOTER_LINKS:
        raise ServiceError(f"The footer can have at most {MAX_FOOTER_LINKS} links.")
    cleaned, seen = [], set()
    for item in links:
        link_id = str(item.get("id") or "").strip()
        label = str(item.get("label") or "").strip()
        if not link_id or link_id in seen:
            raise ServiceError("Each footer link needs its own id.")
        if not label:
            raise ServiceError("Every footer link needs a label.")
        if len(label) > LABEL_MAX_LENGTH:
            raise ServiceError(f"A link label must be {LABEL_MAX_LENGTH} characters or fewer.")
        try:
            url = validate_footer_url(item.get("url"))
        except ValidationError as error:
            raise ServiceError(" ".join(error.messages)) from error
        seen.add(link_id)
        cleaned.append({"id": link_id, "label": label, "url": url})
    return cleaned


def replace_footer_links(actor, links, *, request=None):
    """Save `links` as the footer list. True if it changed, False if it already was that list."""
    require_landing_manager(actor)
    cleaned = _normalize(links)
    with transaction.atomic():
        row = SiteSetting.objects.select_for_update().filter(key=FOOTER_LINKS_KEY).first()
        saved = _parse(row.value) if row is not None else None
        current = saved if saved is not None else _default_links()
        if cleaned == current and (row is None or saved is not None):
            return False
        created = False
        if row is None:
            row, created = SiteSetting.objects.get_or_create(
                key=FOOTER_LINKS_KEY, defaults={"value": cleaned, "updated_by": actor},
            )
        if not created:
            row.value = cleaned
            row.updated_by = actor
            row.save(update_fields=["value", "updated_by", "updated_at"])
        log_action(
            AUDIT_SAVED,
            actor=actor,
            request=request,
            entity_type="site_setting",
            entity_label=FOOTER_LINKS_KEY,
            before={"custom": saved is not None, "links": current},
            after={"custom": True, "links": cleaned},
        )
    return True


def reset_footer_links(actor, *, request=None):
    """Delete the saved list so the built-in links return. True if it changed, False if already default."""
    require_landing_manager(actor)
    with transaction.atomic():
        row = SiteSetting.objects.select_for_update().filter(key=FOOTER_LINKS_KEY).first()
        if row is None:
            return False
        saved = _parse(row.value)
        row.delete()
        log_action(
            AUDIT_RESET,
            actor=actor,
            request=request,
            entity_type="site_setting",
            entity_label=FOOTER_LINKS_KEY,
            before={"custom": True, "links": saved if saved is not None else []},
            after={"custom": False, "links": _default_links()},
        )
    return True

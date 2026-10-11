"""bible.services: reading a chapter and recording that someone read it.

Views stay thin and call these functions. The functions raise ``BibleError``
(never a bare exception) for anything a person can cause, so a view can turn the
message into a toast.
"""
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from .books import BOOKS_BY_ID
from .models import BibleChapterRead, BibleVerse
from .signals import chapter_read
from .versions import DEFAULT_VERSION, SOURCE_ESV, VERSIONS, VERSIONS_BY_CODE

# Placeholder lock reason until the ESV client exists (see build step 4).
ESV_NOT_READY = "ESV is not set up yet."


class BibleError(Exception):
    """A problem a person can understand (bad reference, text not loaded...)."""


class VersionLocked(BibleError):
    """The version cannot be read right now; ``reason`` explains why."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class TextNotImported(BibleError):
    """A local version has no text yet (run ``manage.py import_bible``)."""


@dataclass(frozen=True)
class VersionStatus:
    """A version plus whether it can be read right now."""

    version: object
    available: bool
    reason: str = ""


@dataclass(frozen=True)
class ChapterText:
    """One chapter ready to show: ``verses`` is a list of (number, text)."""

    version: object
    book: object
    chapter: int
    verses: list


def lock_reason(version):
    """Return why a version is locked, or "" when it can be read.

    Local versions are never locked. ESV is always locked for now; the
    status layer (build step 3) and the ESV client (step 4) replace this.
    """
    if version.source == SOURCE_ESV:
        return ESV_NOT_READY
    return ""


def version_statuses():
    """Status of every version, in picker order."""
    statuses = []
    for version in VERSIONS:
        reason = lock_reason(version)
        statuses.append(VersionStatus(version, available=not reason, reason=reason))
    return statuses


def get_status(code):
    """Status of one version by code (the code must exist in the catalog)."""
    version = VERSIONS_BY_CODE[code]
    reason = lock_reason(version)
    return VersionStatus(version, available=not reason, reason=reason)


def resolve_version(code):
    """Return the BibleVersion for a code; an unknown or empty code gives the default."""
    return VERSIONS_BY_CODE.get(code) or VERSIONS_BY_CODE[DEFAULT_VERSION]


def validate_reference(osis_id, chapter):
    """Return the Book, or raise BibleError for an unknown book or chapter."""
    book = BOOKS_BY_ID.get(osis_id)
    if book is None:
        raise BibleError("That book does not exist.")
    if not 1 <= chapter <= book.chapters:
        raise BibleError(f"{book.name} has {book.chapters} chapters.")
    return book


def get_chapter(version_code, osis_id, chapter):
    """Return the ChapterText for a version, or raise BibleError/VersionLocked."""
    version = VERSIONS_BY_CODE.get(version_code)
    if version is None:
        raise BibleError("That version does not exist.")
    book = validate_reference(osis_id, chapter)
    reason = lock_reason(version)
    if reason:
        raise VersionLocked(reason)
    rows = BibleVerse.objects.filter(
        translation=version.code, book=osis_id, chapter=chapter
    ).order_by("verse").values_list("verse", "text")
    verses = list(rows)
    if not verses:
        raise TextNotImported(f"{version.label} has not been loaded yet.")
    return ChapterText(version=version, book=book, chapter=chapter, verses=verses)


def chapter_read_today(user, osis_id, chapter):
    """True when this person already marked this chapter as read today (Manila)."""
    return BibleChapterRead.objects.filter(
        user=user, book=osis_id, chapter=chapter, read_date=timezone.localdate()
    ).exists()


def record_chapter_read(user, version_code, osis_id, chapter):
    """Record that ``user`` read a chapter today; return (entry, created).

    One row per person, book, chapter and Manila day, whatever the version, so
    pressing the button twice (or reading it in two versions) counts once.
    The ``chapter_read`` signal is sent only when a new row is created.
    """
    version = VERSIONS_BY_CODE.get(version_code)
    if version is None:
        raise BibleError("That version does not exist.")
    validate_reference(osis_id, chapter)
    reason = lock_reason(version)
    if reason:
        raise VersionLocked(reason)
    with transaction.atomic():
        entry, created = BibleChapterRead.objects.get_or_create(
            user=user, book=osis_id, chapter=chapter, read_date=timezone.localdate(),
            defaults={"translation": version.code},
        )
        if created:
            chapter_read.send(sender=BibleChapterRead, entry=entry)
    return entry, created

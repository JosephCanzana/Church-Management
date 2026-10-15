"""bible.services: reading a chapter and recording that someone read it.

Views stay thin and call these functions. The functions raise ``BibleError``
(never a bare exception) for anything a person can cause, so a view can turn the
message into a toast.
"""
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from . import esv, switches
from .books import BOOKS_BY_ID, neighbor_chapters
from .errors import BibleError, TextNotImported, VersionLocked  # noqa: F401 (re-exported for callers)
from .models import BibleChapterRead, BibleVerse, BibleBookmark
from .signals import chapter_read
from .versions import DEFAULT_VERSION, SOURCE_ESV, VERSIONS, VERSIONS_BY_CODE


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


def lock_reason(version, switch_state=None):
    """Return why a version is locked, or "" when it can be read.

    Every version can be switched off by the super-admin (apps/bible/switches.py).
    ESV can also lock by itself: no key, a limit was reached or it had a problem
    (see apps/bible/esv.py). Pass ``switch_state`` (``switches.read_switches()``)
    to check several versions with one query.
    """
    reason = switches.switch_reason(version, switch_state)
    if reason:
        return reason
    if version.source == SOURCE_ESV:
        return esv.lock_reason()
    return ""


def version_statuses():
    """Status of every version, in picker order."""
    statuses = []
    switch_state = switches.read_switches()
    for version in VERSIONS:
        reason = lock_reason(version, switch_state)
        statuses.append(VersionStatus(version, available=not reason, reason=reason))
    return statuses


def get_status(code):
    """Status of one version by code (the code must exist in the catalog)."""
    version = VERSIONS_BY_CODE[code]
    reason = lock_reason(version)
    return VersionStatus(version, available=not reason, reason=reason)


def resolve_version(code):
    """Return the BibleVersion for a code; an unknown or empty code gives the default.

    When the default is switched off, the first version that can be read is used
    instead, so a new visitor does not land on a locked version.
    """
    version = VERSIONS_BY_CODE.get(code)
    if version:
        return version
    fallback = VERSIONS_BY_CODE[DEFAULT_VERSION]
    if switches.switch_reason(fallback):
        for status in version_statuses():
            if status.available:
                return status.version
    return fallback


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
    # A version the super-admin switched off is blocked completely: no text,
    # not even an ESV chapter that is still in the cache.
    switched_off = switches.switch_reason(version)
    if switched_off:
        raise VersionLocked(switched_off)
    if version.source == SOURCE_ESV:
        # May still serve a cached chapter while ESV is locked by itself; raises VersionLocked otherwise.
        return ChapterText(version=version, book=book, chapter=chapter, verses=esv.get_verses(osis_id, chapter))
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
    pressing the button twice (or reading it in two versions) counts once. A
    locked version is not refused here: the page only offers the button after
    showing the text, which can come from the cache while the version is locked.
    The ``chapter_read`` signal is sent only when a new row is created.
    """
    version = VERSIONS_BY_CODE.get(version_code)
    if version is None:
        raise BibleError("That version does not exist.")
    validate_reference(osis_id, chapter)
    with transaction.atomic():
        entry, created = BibleChapterRead.objects.get_or_create(
            user=user, book=osis_id, chapter=chapter, read_date=timezone.localdate(),
            defaults={"translation": version.code},
        )
        if created:
            chapter_read.send(sender=BibleChapterRead, entry=entry)
    return entry, created

def get_bookmark(user):
    """The person's bookmark (BibleBookmark) or None when they have none yet."""
    return BibleBookmark.objects.filter(user=user).first()


def save_bookmark(user, osis_id, chapter):
    """Point the person's single bookmark at a chapter; raise BibleError for a bad reference."""
    validate_reference(osis_id, chapter)
    bookmark, _ = BibleBookmark.objects.update_or_create(
        user=user, defaults={"book": osis_id, "chapter": chapter},
    )
    return bookmark


def finish_chapter(user, version_code, osis_id, chapter):
    """Next / "I'm done": count the chapter as read and move the bookmark on.

    Returns (entry, created, following) where ``following`` is (osis_id, chapter)
    or None on Revelation 22. On the last chapter the bookmark stays where it is.
    """
    with transaction.atomic():
        entry, created = record_chapter_read(user, version_code, osis_id, chapter)
        _, following = neighbor_chapters(osis_id, chapter)
        save_bookmark(user, *(following or (osis_id, chapter)))
    return entry, created, following
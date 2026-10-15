"""bible: the local Bible text, verse of the day and chapters read.

Two kinds of translation exist (see ``apps/bible/versions.py``):

* local ones (KJV, Ang Dating Biblia) are public domain, so their text is stored
  in ``bible_verse`` and loaded with ``manage.py import_bible``;
* ESV is licensed, so it is fetched from api.esv.org. It is never stored in
  ``bible_verse`` (a database constraint enforces that); only a small, size-capped
  cache lives in ``bible_esv_cache``.

``book`` is always an OSIS id (``Gen``, ``1John``; see ``apps/bible/books.py``).
"""
from django.conf import settings
from django.db import models
from django.db.models import F, Q


class Translation(models.TextChoices):
    ESV = "ESV", "ESV"
    KJV = "KJV", "KJV"
    ADB = "ADB", "Ang Dating Biblia"
    # Not offered yet: needs permission from the Philippine Bible Society. Kept
    # so any older row that mentions it stays valid.
    MBBTAG = "MBBTAG", "MBB (Tagalog)"


# Translations whose text may be stored in bible_verse (public domain only).
LOCAL_TRANSLATIONS = (Translation.KJV, Translation.ADB)


class BibleVerse(models.Model):
    """One verse of a locally stored (public domain) translation."""

    translation = models.CharField(max_length=10, choices=Translation.choices)
    book = models.CharField(max_length=10)  # OSIS id
    chapter = models.PositiveSmallIntegerField()
    verse = models.PositiveSmallIntegerField()
    text = models.TextField()

    class Meta:
        db_table = "bible_verse"
        constraints = [
            # Also serves as the index for "all verses of one chapter".
            models.UniqueConstraint(
                fields=["translation", "book", "chapter", "verse"],
                name="uq_bible_verse",
            ),
            models.CheckConstraint(
                condition=Q(chapter__gte=1) & Q(verse__gte=1),
                name="bible_verse_positive_numbers",
            ),
            # Licensing guard: ESV text must never be stored in our database.
            models.CheckConstraint(
                condition=~Q(translation="ESV") & ~Q(translation="MBBTAG"),
                name="bible_verse_local_only",
            ),
        ]

    def __str__(self):
        return f"{self.translation} {self.book} {self.chapter}:{self.verse}"


class EsvChapterCache(models.Model):
    """A few recently read ESV chapters, kept to save API calls.

    Crossway's terms allow caching at most 500 verses in total and ask for the
    cache to be cleared now and then, so apps/bible/esv.py keeps the total under
    ESV_CACHE_MAX_VERSES (least recently used chapters go first) and ignores rows
    older than ESV_CACHE_MAX_AGE. Never store ESV text anywhere else.
    """

    book = models.CharField(max_length=10)  # OSIS id
    chapter = models.PositiveSmallIntegerField()
    verses = models.JSONField()             # [[number, text], ...]
    verse_count = models.PositiveSmallIntegerField()
    fetched_at = models.DateTimeField()
    last_used_at = models.DateTimeField()

    class Meta:
        db_table = "bible_esv_cache"
        constraints = [
            models.UniqueConstraint(fields=["book", "chapter"], name="uq_bible_esv_cache"),
            models.CheckConstraint(
                condition=Q(verse_count__gte=1) & Q(verse_count__lte=500),
                name="bible_esv_cache_verse_count",
            ),
        ]

    def __str__(self):
        return f"ESV {self.book} {self.chapter}"


class DailyVerse(models.Model):
    verse_date = models.DateField(primary_key=True)
    translation = models.CharField(max_length=10, choices=Translation.choices)
    book = models.CharField(max_length=50)
    chapter = models.PositiveIntegerField()
    verse_start = models.PositiveIntegerField()
    verse_end = models.PositiveIntegerField(null=True, blank=True)
    verse_text = models.TextField(blank=True)   # optional cache; check ESV licensing

    class Meta:
        db_table = "daily_verse"
        constraints = [
            models.CheckConstraint(
                condition=Q(verse_end__isnull=True) | Q(verse_end__gte=F("verse_start")),
                name="daily_verse_end_after_start",
            ),
        ]


class BibleChapterRead(models.Model):
    """A chapter a person read on a Manila calendar day.

    Unique per person, book, chapter and day, NOT per translation: reading
    Genesis 1 in KJV and again in ESV the same day is one chapter for streaks.
    ``translation`` only records which version was open the first time.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chapters_read")
    translation = models.CharField(max_length=10, choices=Translation.choices)
    book = models.CharField(max_length=50)
    chapter = models.PositiveIntegerField()
    read_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bible_chapter_read"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "book", "chapter", "read_date"],
                name="uq_bible_chapter_read",
            ),
        ]
        
class BibleBookmark(models.Model):
    """Where a person is currently reading: one row per person, kept on the account.

    Moved forward when they press Next (or "I'm done") and set by hand with the
    Bookmark button. Not tied to a version: the version is a per-device cookie.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bible_bookmark")
    book = models.CharField(max_length=10)  # OSIS id
    chapter = models.PositiveSmallIntegerField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "bible_bookmark"
        constraints = [
            models.CheckConstraint(condition=Q(chapter__gte=1), name="bible_bookmark_positive_chapter"),
        ]

    def __str__(self):
        return f"{self.user_id} at {self.book} {self.chapter}"
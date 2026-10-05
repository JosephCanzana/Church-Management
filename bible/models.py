"""bible: verse of the day + chapters read. If you self-host scrollmapper,
load its tables in their own Postgres schema; they are not Django models."""
from django.conf import settings
from django.db import models
from django.db.models import F, Q


class Translation(models.TextChoices):
    ESV = "ESV", "ESV"
    MBBTAG = "MBBTAG", "MBB (Tagalog)"


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
                fields=["user", "translation", "book", "chapter", "read_date"],
                name="uq_bible_chapter_read",
            ),
        ]
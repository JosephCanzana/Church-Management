"""pages: public, admin-edited content (landing page, terms, donation info,
latest JIL video)."""
from django.conf import settings
from django.db import models

USER = settings.AUTH_USER_MODEL


class ChurchContent(models.Model):
    class Key(models.TextChoices):
        MISSION = "mission", "Mission"
        VISION = "vision", "Vision"
        CORE_VALUES = "core_values", "Core values"
        TERMS = "terms", "Terms"
        LANDING_DESCRIPTION = "landing_description", "Landing description"

    class Language(models.TextChoices):
        EN = "en", "English"
        FIL = "fil", "Filipino"

    key = models.CharField(max_length=30, choices=Key.choices)
    language = models.CharField(max_length=3, choices=Language.choices)
    title = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    version = models.PositiveIntegerField(default=1)     # bump on every edit
    updated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "church_content"
        constraints = [
            models.UniqueConstraint(fields=["key", "language"], name="uq_church_content_key_lang"),
        ]


class LandingImage(models.Model):
    image = models.ImageField(upload_to="landing/")
    caption = models.CharField(max_length=255, blank=True)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "landing_image"
        ordering = ["sort_order", "id"]


class DonationAccount(models.Model):
    """Giving instructions only (GCash, Maya, PNB, BPI...). No transactions."""
    payment_app = models.CharField(max_length=100)
    account_name = models.CharField(max_length=255, blank=True)
    account_number = models.CharField(max_length=100, blank=True)
    swift_code = models.CharField(max_length=50, blank=True)
    instructions = models.TextField(blank=True)
    qr_image = models.ImageField(upload_to="donation_qr/", blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    updated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "donation_account"
        ordering = ["sort_order", "id"]


class JilVideo(models.Model):
    youtube_video_id = models.CharField(max_length=32, unique=True)
    title = models.CharField(max_length=255)
    thumbnail_url = models.URLField(max_length=500, blank=True)
    published_at = models.DateTimeField()
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "jil_video"
        indexes = [models.Index(fields=["-published_at"], name="ix_jil_video_published")]
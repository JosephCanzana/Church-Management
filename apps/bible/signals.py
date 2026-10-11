"""Signals sent by the bible app (other apps listen; bible never imports them)."""
from django.dispatch import Signal

# Sent once when a person's chapter read is recorded for the first time that
# day. Receives ``entry`` (the BibleChapterRead). The faith app listens to it to
# update the Bible streak.
chapter_read = Signal()

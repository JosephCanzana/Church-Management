"""faith.listeners: reactions to signals from other apps (faith listens, bible never imports faith)."""
from . import streaks


def on_chapter_read(sender, entry, **kwargs):
    """Move the Bible reading streak when a chapter read is recorded.

    ``bible.signals.chapter_read`` is sent only for a NEW ``BibleChapterRead`` row (one per
    person, book, chapter and Manila day), so each chapter counts once a day. The date comes
    from the row, never from ``now()``. This runs inside the same transaction as the read.
    """
    streaks.record_read(entry.user, entry.read_date)

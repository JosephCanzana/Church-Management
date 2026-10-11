"""Errors raised by the bible services (people can understand and read these)."""


class BibleError(Exception):
    """A problem a person can understand (bad reference, text not loaded...)."""


class VersionLocked(BibleError):
    """The version cannot be read right now; ``reason`` explains why."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class TextNotImported(BibleError):
    """A local version has no text yet (run ``manage.py import_bible``)."""

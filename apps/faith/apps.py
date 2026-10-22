from django.apps import AppConfig


class FaithConfig(AppConfig):
    name = 'apps.faith'
    label = 'faith'
    def ready(self):
        """Connect the Bible read signal to the streak listener."""
        from apps.bible.signals import chapter_read
        from .listeners import on_chapter_read

        chapter_read.connect(on_chapter_read, dispatch_uid="faith.on_chapter_read")

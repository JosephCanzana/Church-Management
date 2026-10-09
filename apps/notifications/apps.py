from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = 'apps.notifications'
    label = 'notifications'

    def ready(self):
        from . import listeners  # noqa: F401
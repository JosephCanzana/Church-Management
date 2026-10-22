"""faith.middleware: count the first page visit of each Manila day toward the open streak.

Register it right AFTER ``AuthenticationMiddleware`` and BEFORE ``PasswordChangeRequiredMiddleware``:
a request that gets redirected to change-password would otherwise skip it. The session remembers
the last counted date, so the database is written once per person per day, not once per request.
"""
import logging

from django.utils import timezone

from .streaks import record_open

logger = logging.getLogger(__name__)

SESSION_KEY = "open_streak_date"


class DailyOpenMiddleware:
    """Call ``record_open`` on the first HTML page GET of the day for people who can sign in."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._should_count(request):
            today = timezone.localdate()
            if request.session.get(SESSION_KEY) != today.isoformat():
                try:
                    record_open(request.user, today)
                except Exception:  # a streak problem must never break a page
                    logger.exception("Could not record the daily open")
                else:
                    request.session[SESSION_KEY] = today.isoformat()
        return self.get_response(request)

    @staticmethod
    def _should_count(request):
        """True for a signed-in, can-sign-in person making a page (HTML) GET that is not AJAX."""
        user = request.user
        if request.method != "GET" or not user.is_authenticated or not user.can_sign_in:
            return False
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return False
        return "text/html" in request.headers.get("Accept", "")

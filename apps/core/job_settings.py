"""core.job_settings: the site-setting keys that tune the nightly and periodic jobs.

Read-only helpers, so any app (faith, buddy) can read a value without importing `accounts`.
Writing a value is done by `accounts.services.job_settings` (super-admin only, audited).

Keys:
  faith_goal_reminders_enabled  bool, default True   (send_goal_reminders does nothing when off)
  buddy_end_request_days        int,  default 7      (close_stale_end_requests waits this long)
"""
from apps.core.models import SiteSetting

GOAL_REMINDERS_KEY = "faith_goal_reminders_enabled"
END_REQUEST_DAYS_KEY = "buddy_end_request_days"

DEFAULT_GOAL_REMINDERS = True
DEFAULT_END_REQUEST_DAYS = 7
MIN_END_REQUEST_DAYS = 1
MAX_END_REQUEST_DAYS = 90


def _value(key, default):
    """Return the stored JSON value for `key`, or `default` when the row is missing."""
    row = SiteSetting.objects.filter(pk=key).first()
    return default if row is None else row.value


def goal_reminders_enabled():
    """True unless a super-admin switched goal reminders off."""
    value = _value(GOAL_REMINDERS_KEY, DEFAULT_GOAL_REMINDERS)
    return value if isinstance(value, bool) else DEFAULT_GOAL_REMINDERS


def end_request_days():
    """Days an unanswered buddy end request waits before it auto-closes (bad data falls back to 7)."""
    value = _value(END_REQUEST_DAYS_KEY, DEFAULT_END_REQUEST_DAYS)
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_END_REQUEST_DAYS
    if not MIN_END_REQUEST_DAYS <= value <= MAX_END_REQUEST_DAYS:
        return DEFAULT_END_REQUEST_DAYS
    return value

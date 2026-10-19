"""accounts.services.presence: record that people attended a gathering.

`User.last_attended_at` feeds the inactivity job (`archive_inactive_users`), so it must only ever
move FORWARD: uploading an old sheet, or re-uploading a reopened one, can never make someone look
less recently present than they were. Attendance calls this at upload; `accounts` never imports
`attendance` (dependency direction), so the function takes plain ids and a date.
"""
from django.db.models import Q

from ..models import AgeNetwork, User


def mark_attended(user_ids, on_date):
    """Set `last_attended_at` to `on_date` for these people, but only where that is later.

    One UPDATE for the whole list: people with no date yet, or an earlier one, are moved; people
    already at or past `on_date` are left alone. Returns how many rows changed. `updated_at` is
    deliberately not touched: attending is activity, not an edit of the person's details.
    """
    ids = list(user_ids)
    if not ids:
        return 0
    return (
        User.objects.filter(pk__in=ids)
        .filter(Q(last_attended_at__isnull=True) | Q(last_attended_at__lt=on_date))
        .update(last_attended_at=on_date)
    )


def remember_age_network(user_id, age_network):
    """Keep `age_network` as this person's own age group, but only if they have no birth date.

    The birth date always decides the age network when there is one, so a person with a birth date
    is never touched. Called by attendance when the taker picks or changes a category, so the same
    person is not asked again next Sunday. Returns True if a row changed. `updated_at` is not
    touched (this is not an edit of the person's details) and nothing is written to audit_log.
    A value that is not an `AgeNetwork` is ignored.
    """
    if age_network not in AgeNetwork.values:
        return False
    return bool(
        User.objects.filter(pk=user_id, birth_date__isnull=True)
        .exclude(age_network=age_network)
        .update(age_network=age_network)
    )

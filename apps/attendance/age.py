"""attendance.age: which age network a person falls in on a given date.

`AgeNetwork` lives here (not in models.py) so the attendance models can import it
without a circular import. The functions are pure: no database, no clock. The
sheet's date is always passed in, so a sheet is counted by the age people had ON
that date, never by their age today.
"""
from django.db import models


class AgeNetwork(models.TextChoices):
    """The four age groups shown on the attendance summary."""

    ADULT = "adult", "Adult"                    # 36 and above
    YOUNG_ADULT = "young_adult", "Young adult"  # 25 to 35
    YOUTH = "youth", "Youth"                    # 13 to 24
    CHILDREN = "children", "Children"           # 0 to 12


# (lowest age, network), checked from the oldest group down.
_LOWER_BOUNDS = (
    (36, AgeNetwork.ADULT),
    (25, AgeNetwork.YOUNG_ADULT),
    (13, AgeNetwork.YOUTH),
    (0, AgeNetwork.CHILDREN),
)


def age_on(birth_date, on_date):
    """Completed years of age on `on_date`.

    The birthday counts on its own day. A 29 February birthday falls on 1 March in
    a year without one, so tuple comparison of (month, day) is enough: (3, 1) is
    already past (2, 29) while (2, 28) is not. Meaningless when `birth_date` is
    after `on_date`; `age_network_for` guards that case.
    """
    years = on_date.year - birth_date.year
    if (on_date.month, on_date.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


def age_network_for(birth_date, on_date):
    """The `AgeNetwork` for a birth date on `on_date`, or None.

    None means "cannot tell": there is no birth date, or it lies after the date
    (a typing error). The caller then asks the person taking attendance to pick.
    """
    if birth_date is None or birth_date > on_date:
        return None
    age = age_on(birth_date, on_date)
    for lowest, network in _LOWER_BOUNDS:
        if age >= lowest:
            return network
    return None  # unreachable: age is never negative here

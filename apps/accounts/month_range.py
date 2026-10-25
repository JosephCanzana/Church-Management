"""accounts.month_range: the "from month to month" choice on the people detail page.

Pure functions, no database. The page sends `?from=2026-01&to=2026-10`; anything missing, malformed or
out of order is repaired rather than refused (a bad query string must never break the page):
  * nothing given            -> the last 12 months, ending with the current month
  * only `to`                -> the 12 months ending there
  * only `from`              -> from there to the current month
  * `from` after `to`        -> the two are swapped
  * a month in the future    -> the current month
  * a span over 120 months   -> the newest 120 months of it
The result holds real dates (`start` = first day of the first month, `end` = last day of the last month),
which are handed to the attendance and tithes overviews.
"""
import calendar
from dataclasses import dataclass
from datetime import date

DEFAULT_MONTHS = 12
MAX_MONTHS = 120
_MIN_YEAR, _MAX_YEAR = 2000, 2100


@dataclass(frozen=True)
class MonthRange:
    start: date            # first day of the first month
    end: date              # last day of the last month
    from_value: str        # "YYYY-MM", as the <select> posts it
    to_value: str
    is_default: bool       # True for the last 12 months
    months: int


def _parse(value):
    """(year, month) from "YYYY-MM", else None."""
    try:
        year_text, month_text = str(value).strip().split("-")
        year, month = int(year_text), int(month_text)
    except ValueError:
        return None
    return (year, month) if _MIN_YEAR <= year <= _MAX_YEAR and 1 <= month <= 12 else None


def _index(year, month):
    return year * 12 + (month - 1)


def _ym(index):
    year, month0 = divmod(index, 12)
    return year, month0 + 1


def parse_month_range(from_value, to_value, today, default_months=DEFAULT_MONTHS):
    """The range to show, always valid. See the module docstring for the repair rules."""
    current = _index(today.year, today.month)
    default_from = current - (default_months - 1)
    wanted_from, wanted_to = _parse(from_value), _parse(to_value)

    last = _index(*wanted_to) if wanted_to else current
    first = _index(*wanted_from) if wanted_from else last - (default_months - 1)
    if first > last:
        first, last = last, first
    last = min(last, current)
    first = min(first, last)
    first = max(first, last - (MAX_MONTHS - 1))

    (first_year, first_month), (last_year, last_month) = _ym(first), _ym(last)
    return MonthRange(
        start=date(first_year, first_month, 1),
        end=date(last_year, last_month, calendar.monthrange(last_year, last_month)[1]),
        from_value=f"{first_year:04d}-{first_month:02d}",
        to_value=f"{last_year:04d}-{last_month:02d}",
        is_default=(first, last) == (default_from, current),
        months=last - first + 1,
    )


def month_options(first_date, today, minimum_months=DEFAULT_MONTHS, limit=MAX_MONTHS):
    """The months the two <select>s offer, newest first.

    From the month of `first_date` (when the person first appears) to the current month, but always at
    least `minimum_months` back so the default range exists in the list, and never more than `limit`.
    """
    current = _index(today.year, today.month)
    oldest = current - (minimum_months - 1)
    if first_date is not None:
        oldest = min(oldest, _index(first_date.year, first_date.month))
    oldest = max(oldest, current - (limit - 1))
    options = []
    for index in range(current, oldest - 1, -1):
        year, month = _ym(index)
        options.append({"value": f"{year:04d}-{month:02d}", "date": date(year, month, 1)})
    return options

"""bible.switches: the administrator's on/off switch for each Bible version.

One ``site_setting`` row per version (on when the row is missing):

* ``bible_esv_enabled``   ESV (the key ``esv.KEY_ESV``)
* ``bible_kjv_enabled``   King James Version
* ``bible_adb_enabled``   Ang Dating Biblia
* ``bible_api_enabled``   master switch for every version fetched online (today only ESV)

A switch is a manual decision by the super-admin. It is separate from the
automatic ESV lock (missing key, call limits, a problem at api.esv.org), which
``esv.lock_reason()`` reports. A version that is switched off is blocked
completely: its text is not shown, not even from the ESV cache.

This module only READS the switches. Changing them is ``site_settings.py``.
"""
from dataclasses import dataclass

from apps.core.models import SiteSetting

from . import esv
from .versions import SOURCE_ESV, VERSIONS

KEY_MASTER = esv.KEY_MASTER


def switch_key(version):
    """The ``site_setting`` key that holds this version's switch."""
    return f"bible_{version.code.lower()}_enabled"


@dataclass(frozen=True)
class Switches:
    """Every switch at one moment: the master and one flag per version code."""

    master: bool
    versions: dict


def read_switches():
    """Read all the switches in one query."""
    keys = [KEY_MASTER, *(switch_key(version) for version in VERSIONS)]
    rows = {row.key: row.value for row in SiteSetting.objects.filter(key__in=keys)}
    return Switches(
        master=bool(rows.get(KEY_MASTER, True)),
        versions={version.code: bool(rows.get(switch_key(version), True)) for version in VERSIONS},
    )


def switch_reason(version, switches=None):
    """Why an administrator has blocked this version, or "" when it is switched on.

    Pass ``switches`` (from ``read_switches()``) to avoid another query when
    checking several versions in a row.
    """
    switches = switches or read_switches()
    if version.source == SOURCE_ESV and not switches.master:
        return esv.MSG_ALL_OFF
    if not switches.versions[version.code]:
        # Same words as before for ESV; the other versions use the same sentence shape.
        return esv.MSG_ESV_OFF if version.source == SOURCE_ESV else f"{version.label} is turned off by an administrator."
    return ""

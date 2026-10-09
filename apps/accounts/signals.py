"""accounts.signals: events other apps may react to without accounts importing them.

Dependency rule (AGENTS.md): `accounts` never imports upward into feature apps.
It announces what happened and the listening app (notifications) reacts.
"""
from django.dispatch import Signal

# Sent when an UNVERIFIED (pending) email is taken away from someone because
# another account verified the same address first.
# Keyword arguments: user -- the person who lost the pending address.
# Receivers run inside the sender's transaction, so a rollback undoes them too.
email_released = Signal()
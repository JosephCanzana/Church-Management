"""audit.signals: the hook other apps use to react to audit entries.

`audit` must never import feature apps (see AGENTS.md dependency rules), so
instead of calling `notifications` directly it announces what happened with
this signal. `notifications` connects a receiver to it and applies its
notification rules.
"""
from django.dispatch import Signal

# Sent by audit.services.log_action() after the AuditLog row is saved.
# sender: the AuditLog class
# kwargs: entry -- the AuditLog row that was just written
action_logged = Signal()
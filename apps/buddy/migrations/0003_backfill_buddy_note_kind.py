"""Brick 6, step 2 of 3: old notes with no text were nudges, so mark them as such."""
from django.db import migrations


def mark_empty_notes_as_nudges(apps, schema_editor):
    """Set kind='nudge' on every note whose body is NULL (the old way to store a nudge)."""
    BuddyNote = apps.get_model("buddy", "BuddyNote")
    BuddyNote.objects.filter(body__isnull=True).update(kind="nudge")


class Migration(migrations.Migration):
    dependencies = [("buddy", "0002_buddy_end_fields_and_note_kind")]

    operations = [
        # Going back needs no data change: the column itself is dropped by 0002's reverse.
        migrations.RunPython(mark_empty_notes_as_nudges, migrations.RunPython.noop),
    ]

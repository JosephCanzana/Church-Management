# ADD to apps/theming/context_processors.py (merge with what is there; see the notes in the chat).
# Register "apps.theming.context_processors.color_mode" in TEMPLATES[0]["OPTIONS"]["context_processors"]
# if you do not fold it into the existing processor.
from django.core.exceptions import ObjectDoesNotExist

from .models import UserSettings


def color_mode(request):
    """Add `theme_mode` ("light", "dark" or "system") for signed-in people; nothing for visitors.

    base.html writes it on <html> so a saved Light or Dark is correct in the very first byte of the
    page (no flash), and "system" is resolved by the small inline script from the device setting.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    try:
        mode = user.settings.color_mode  # cached on the user after the first read this request
    except ObjectDoesNotExist:
        mode = UserSettings.ColorMode.SYSTEM
    return {"theme_mode": mode}

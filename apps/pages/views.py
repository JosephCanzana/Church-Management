"""Views for the public pages app."""
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.pages.services import get_landing_context


@require_GET
def landing_page(request):
    """Public landing page at "/". All content and defaults come from the service."""
    return render(request, "pages/landing.html", get_landing_context())
"""Admin views for editing landing-page text content."""
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts.decorators import role_required
from apps.accounts.models import Role
from apps.core.services import ServiceError

from . import manage_services
from .forms import LandingContentForm
from .manage_services import CONTENT_BLOCKS


landing_manager_required = role_required(Role.ADMIN, Role.SUPER_ADMIN)


@landing_manager_required
@never_cache
@require_http_methods(["GET"])
def content_list(request):
    """Show each editable landing block with its current body and source status."""
    return render(request, "pages/manage/index.html", {
        "blocks": manage_services.landing_content_blocks(request.user),
    })


@landing_manager_required
@never_cache
@require_http_methods(["GET", "POST"])
def content_edit(request, key):
    """Edit one supported English text block and reset it when submitted blank."""
    definition = CONTENT_BLOCKS.get(key)
    if definition is None:
        raise Http404
    block = next(
        block for block in manage_services.landing_content_blocks(request.user)
        if block["key"] == key
    )
    form = LandingContentForm(request.POST or None, initial={"body": block["body"]})
    if request.method == "POST" and form.is_valid():
        try:
            changed = manage_services.save_landing_content(
                request.user, key, form.cleaned_data["body"], request=request,
            )
        except ServiceError as error:
            form.add_error(None, str(error))
        else:
            if changed:
                messages.success(request, f"{block['label']} was saved." if form.cleaned_data["body"].strip()
                                 else f"{block['label']} was reset to its default.")
            else:
                messages.info(request, "No changes to save.")
            return redirect("pages:pages_manage:content_edit", key=key)
    return render(request, "pages/manage/content_form.html", {
        "form": form,
        "block": block,
    })
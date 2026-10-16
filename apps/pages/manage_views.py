"""Admin views for editing landing-page text content."""
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.decorators import role_required
from apps.accounts.models import Role
from apps.core.services import ServiceError

from . import manage_services
from .forms import LandingContentForm
from .forms import LandingImageForm
from .manage_services import CONTENT_BLOCKS
from .models import LandingImage


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


@landing_manager_required
@never_cache
@require_http_methods(["GET"])
def image_list(request):
    """Show active and inactive photos in their separate placement pools."""
    images = LandingImage.objects.all().order_by("placement", "sort_order", "pk")
    hero_images = [image for image in images if image.placement == LandingImage.Placement.HERO]
    gallery_images = [image for image in images if image.placement == LandingImage.Placement.GALLERY]
    return render(request, "pages/manage/images.html", {
        "hero_images": hero_images,
        "gallery_images": gallery_images,
        "hero_active_count": sum(image.is_active for image in hero_images),
        "gallery_active_count": sum(image.is_active for image in gallery_images),
        "active_image_limit": manage_services.ACTIVE_IMAGE_LIMIT,
        "photo_groups": (
            (LandingImage.Placement.HERO, hero_images, sum(image.is_active for image in hero_images), "Hero carousel"),
            (LandingImage.Placement.GALLERY, gallery_images, sum(image.is_active for image in gallery_images), "Come as you are gallery"),
        ),
    })


@landing_manager_required
@never_cache
@require_http_methods(["GET", "POST"])
def image_create(request):
    """Upload a photo and assign it to the hero or gallery pool."""
    initial = {}
    placement = request.GET.get("placement")
    if placement in LandingImage.Placement.values:
        initial["placement"] = placement
    form = LandingImageForm(request.POST or None, request.FILES or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            image = manage_services.create_landing_image(
                request.user,
                image=form.cleaned_data["image"],
                caption=form.cleaned_data["caption"],
                placement=form.cleaned_data["placement"],
                request=request,
            )
        except ServiceError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, "Landing photo uploaded.")
            return redirect("pages:pages_manage:image_list")
    return render(request, "pages/manage/image_form.html", {
        "form": form,
        "image_record": None,
        "active_image_limit": manage_services.ACTIVE_IMAGE_LIMIT,
    })


@landing_manager_required
@never_cache
@require_http_methods(["GET", "POST"])
def image_edit(request, pk):
    """Edit a landing image caption or placement, or replace its file."""
    image_record = get_object_or_404(LandingImage, pk=pk)
    form = LandingImageForm(request.POST or None, request.FILES or None, instance=image_record)
    if request.method == "POST" and form.is_valid():
        try:
            result = manage_services.update_landing_image(
                request.user,
                image_record.pk,
                caption=form.cleaned_data["caption"],
                placement=form.cleaned_data["placement"],
                image=form.cleaned_data.get("image") or None,
                request=request,
            )
        except ServiceError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, "Landing photo updated.") if result else messages.info(request, "No changes to save.")
            return redirect("pages:pages_manage:image_list")
    return render(request, "pages/manage/image_form.html", {
        "form": form,
        "image_record": image_record,
        "active_image_limit": manage_services.ACTIVE_IMAGE_LIMIT,
    })


@landing_manager_required
@never_cache
@require_POST
def image_toggle(request, pk):
    """Activate or deactivate one photo in its placement pool."""
    value = request.POST.get("active")
    if value not in ("0", "1"):
        messages.error(request, "Choose whether this photo should be active.")
        return redirect("pages:pages_manage:image_list")
    try:
        changed = manage_services.set_landing_image_active(
            request.user, pk, value == "1", request=request,
        )
    except ServiceError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Photo activated." if value == "1" else "Photo deactivated.") if changed else messages.info(request, "Photo already has that status.")
    return redirect("pages:pages_manage:image_list")


@landing_manager_required
@never_cache
@require_POST
def image_move(request, pk, direction):
    """Move one image up or down immediately within its current placement."""
    try:
        changed = manage_services.move_landing_image(request.user, pk, direction, request=request)
    except ServiceError as error:
        messages.error(request, str(error))
    else:
        if changed:
            messages.success(request, "Photo order updated.")
    return redirect("pages:pages_manage:image_list")


@landing_manager_required
@never_cache
@require_POST
def image_delete(request, pk):
    """Delete a landing photo and its public image file."""
    try:
        manage_services.delete_landing_image(request.user, pk, request=request)
    except ServiceError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Landing photo deleted.")
    return redirect("pages:pages_manage:image_list")
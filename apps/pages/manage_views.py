"""Admin views for editing landing-page text content."""
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.decorators import role_required
from apps.accounts.models import Role
from apps.core.services import ServiceError

from . import manage_services
from .forms import DonationAccountForm, FooterLinkForm, LandingContentForm, LandingImageForm
from .manage_services import CONTENT_BLOCKS
from .models import DonationAccount, LandingImage


landing_manager_required = role_required(Role.ADMIN, Role.SUPER_ADMIN)

# The giving instructions are a text block, but they belong to the Give page tab,
# so they are left out of the Landing text list and send you back to the Give page.
GIVE_BLOCK_KEY = "donation_instructions"


@landing_manager_required
@never_cache
@require_http_methods(["GET"])
def content_list(request):
    """Show each editable landing block (not the giving instructions) with its body and source status."""
    blocks = [
        block for block in manage_services.landing_content_blocks(request.user)
        if block["key"] != GIVE_BLOCK_KEY
    ]
    return render(request, "pages/manage/index.html", {
        "blocks": blocks,
        "any_custom": any(block["is_custom"] for block in blocks),
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
            if key == GIVE_BLOCK_KEY:
                return redirect("pages:pages_manage:donation_accounts")
            return redirect("pages:pages_manage:content_edit", key=key)
    return render(request, "pages/manage/content_form.html", {
        "form": form,
        "block": block,
        "is_give_block": key == GIVE_BLOCK_KEY,
        "default_text": definition.get("default", ""),
    })


@landing_manager_required
@never_cache
@require_POST
def content_reset(request, key):
    """Put one text block back to its built-in default text."""
    definition = CONTENT_BLOCKS.get(key)
    if definition is None:
        raise Http404
    try:
        changed = manage_services.save_landing_content(request.user, key, "", request=request)
    except ServiceError as error:
        messages.error(request, str(error))
    else:
        if changed:
            messages.success(request, f"{definition['label']} was reset to its default.")
        else:
            messages.info(request, f"{definition['label']} already uses its default.")
    if key == GIVE_BLOCK_KEY:
        return redirect("pages:pages_manage:donation_accounts")
    return redirect("pages:pages_manage:content_list")


@landing_manager_required
@never_cache
@require_POST
def content_reset_all(request):
    """Put every landing text block (not the giving instructions) back to its default."""
    try:
        count = manage_services.reset_all_landing_content(request.user, request=request)
    except ServiceError as error:
        messages.error(request, str(error))
    else:
        if count:
            messages.success(request, f"{count} text block{'s' if count != 1 else ''} reset to the default.")
        else:
            messages.info(request, "Every text block already uses its default.")
    return redirect("pages:pages_manage:content_list")


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
    """Upload a photo and assign it to the hero or gallery pool (hidden when that pool is full)."""
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
            if image.is_active:
                messages.success(request, "Photo uploaded and live on the public page.")
            else:
                messages.warning(
                    request,
                    f"Photo uploaded but hidden: {manage_services.ACTIVE_IMAGE_LIMIT} photos are already live there. "
                    "Hide one to make this one live.",
                )
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


@landing_manager_required
@never_cache
@require_http_methods(["GET"])
def donation_account_list(request):
    """Show active and hidden public donation accounts in display order."""
    accounts = DonationAccount.objects.all().order_by("sort_order", "pk")
    instructions = next(
        block for block in manage_services.landing_content_blocks(request.user)
        if block["key"] == GIVE_BLOCK_KEY
    )
    return render(request, "pages/manage/donation_accounts.html", {
        "accounts": accounts,
        "instructions": instructions,
    })


@landing_manager_required
@never_cache
@require_http_methods(["GET", "POST"])
def donation_account_create(request):
    """Create a public donation account with an optional QR image."""
    form = DonationAccountForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            account = manage_services.create_donation_account(
                request.user, data=form.cleaned_data, request=request,
            )
        except ServiceError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, "Donation account created.")
            return redirect("pages:pages_manage:donation_accounts")
    return render(request, "pages/manage/donation_account_form.html", {
        "form": form, "account": None,
    })


@landing_manager_required
@never_cache
@require_http_methods(["GET", "POST"])
def donation_account_edit(request, pk):
    """Edit public donation details and optionally replace or clear the QR image."""
    account = get_object_or_404(DonationAccount, pk=pk)
    form = DonationAccountForm(request.POST or None, request.FILES or None, instance=account)
    if request.method == "POST" and form.is_valid():
        data = dict(form.cleaned_data)
        qr_image = data.get("qr_image")
        if qr_image is False:
            data["qr_image"] = ""
        elif not qr_image or getattr(qr_image, "_committed", False):
            data.pop("qr_image", None)
        try:
            result = manage_services.update_donation_account(
                request.user, account.pk, data=data, request=request,
            )
        except ServiceError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, "Donation account updated.") if result else messages.info(request, "No changes to save.")
            return redirect("pages:pages_manage:donation_accounts")
    return render(request, "pages/manage/donation_account_form.html", {
        "form": form, "account": account,
    })


@landing_manager_required
@never_cache
@require_POST
def donation_account_toggle(request, pk):
    """Show or hide a donation account on public pages."""
    value = request.POST.get("active")
    if value not in ("0", "1"):
        messages.error(request, "Choose whether this donation account should be active.")
        return redirect("pages:pages_manage:donation_accounts")
    try:
        changed = manage_services.set_donation_account_active(
            request.user, pk, value == "1", request=request,
        )
    except ServiceError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Donation account is now public." if value == "1" else "Donation account is hidden.") if changed else messages.info(request, "Donation account already has that status.")
    return redirect("pages:pages_manage:donation_accounts")


@landing_manager_required
@never_cache
@require_POST
def donation_account_move(request, pk, direction):
    """Move one donation account up or down in the public order immediately."""
    try:
        changed = manage_services.move_donation_account(request.user, pk, direction, request=request)
    except ServiceError as error:
        messages.error(request, str(error))
    else:
        if changed:
            messages.success(request, "Account order updated.")
    return redirect("pages:pages_manage:donation_accounts")


@landing_manager_required
@never_cache
@require_POST
def donation_account_delete(request, pk):
    """Delete a donation account and its optional QR image."""
    try:
        manage_services.delete_donation_account(request.user, pk, request=request)
    except ServiceError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Donation account deleted.")
    return redirect("pages:pages_manage:donation_accounts")


@landing_manager_required
@never_cache
@require_http_methods(["GET"])
def footer_link_list(request):
    """Show current footer links and whether they come from defaults or a saved override."""
    links, is_custom = manage_services.footer_links_for_manager(request.user)
    return render(request, "pages/manage/footer_links.html", {
        "links": links,
        "is_custom": is_custom,
    })


@landing_manager_required
@never_cache
@require_http_methods(["GET", "POST"])
def footer_link_create(request):
    """Add one validated footer link."""
    form = FooterLinkForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            manage_services.save_footer_link(request.user, **form.cleaned_data, request=request)
        except ServiceError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, "Footer link added.")
            return redirect("pages:pages_manage:footer_links")
    return render(request, "pages/manage/footer_link_form.html", {"form": form, "link": None})


@landing_manager_required
@never_cache
@require_http_methods(["GET", "POST"])
def footer_link_edit(request, link_id):
    """Edit one saved or default footer link."""
    links, _is_custom = manage_services.footer_links_for_manager(request.user)
    link = next((item for item in links if item["id"] == link_id), None)
    if link is None:
        raise Http404
    form = FooterLinkForm(request.POST or None, initial={"label": link["label"], "url": link["url"]})
    if request.method == "POST" and form.is_valid():
        try:
            manage_services.save_footer_link(
                request.user, link_id=link_id, **form.cleaned_data, request=request,
            )
        except ServiceError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, "Footer link updated.")
            return redirect("pages:pages_manage:footer_links")
    return render(request, "pages/manage/footer_link_form.html", {"form": form, "link": link})


@landing_manager_required
@never_cache
@require_POST
def footer_link_delete(request, link_id):
    """Remove one footer link from the current ordered list."""
    try:
        manage_services.delete_footer_link(request.user, link_id, request=request)
    except ServiceError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Footer link deleted.")
    return redirect("pages:pages_manage:footer_links")


@landing_manager_required
@never_cache
@require_POST
def footer_link_move(request, link_id, direction):
    """Move one footer link up or down immediately."""
    try:
        changed = manage_services.reorder_footer_link(request.user, link_id, direction, request=request)
    except ServiceError as error:
        messages.error(request, str(error))
    else:
        if changed:
            messages.success(request, "Footer link order updated.")
    return redirect("pages:pages_manage:footer_links")


@landing_manager_required
@never_cache
@require_POST
def footer_links_reset(request):
    """Restore the built-in footer links."""
    changed = manage_services.reset_footer_links_to_default(request.user, request=request)
    messages.success(request, "Default footer links restored.") if changed else messages.info(request, "Footer links already use their defaults.")
    return redirect("pages:pages_manage:footer_links")
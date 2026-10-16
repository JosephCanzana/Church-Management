"""Manager-only views for creating and maintaining church resources."""
from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.decorators import role_required
from apps.accounts.models import Role
from apps.core.services import ServiceError

from . import services
from .forms import (
	GeneralResourceForm,
	MonthlyResourceForm,
	MonthlyThemeForm,
	ResourceFilterForm,
	WeeklyPowerpointForm,
)
from .models import GeneralResource, MonthlyResource, MonthlyTheme, WeeklyPowerpoint
from .permissions import (
	can_view_general_resource,
	can_view_monthly_resource,
	can_view_weekly_powerpoint,
)


PAGE_SIZE = 25
manager_required = role_required(Role.ADMIN, Role.SUPER_ADMIN)
super_admin_required = role_required(Role.SUPER_ADMIN)
THEME_SORTS = (("-month", "Newest month"), ("month", "Oldest month"), ("title", "Title, A to Z"))
WEEKLY_SORTS = (("-week_start", "Newest week"), ("week_start", "Oldest week"), ("title", "Title, A to Z"))
GENERAL_SORTS = (("title", "Title, A to Z"), ("-title", "Title, Z to A"), ("-created_at", "Newest first"))
APP_ROLES = (Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR, Role.MEMBER)


@role_required(*APP_ROLES)
@never_cache
@require_http_methods(["GET"])
def library(request):
	"""Show published monthly themes and the active resources available to this role."""
	themes = MonthlyTheme.objects.filter(
		archived_at__isnull=True,
		is_published=True,
	).prefetch_related("resources")
	can_view_shared_resources = request.user.role in (
		Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR,
	)
	weekly = WeeklyPowerpoint.objects.none()
	general = GeneralResource.objects.none()
	if can_view_shared_resources:
		weekly = WeeklyPowerpoint.objects.filter(archived_at__isnull=True).select_related("monthly_theme")
		general = GeneralResource.objects.filter(archived_at__isnull=True)
	return render(request, "resources/library.html", {
		"themes": themes,
		"weekly_presentations": weekly,
		"general_resources": general,
		"can_view_shared_resources": can_view_shared_resources,
	})


def _download_private_file(request, record, can_view):
	"""Stream an authorized private file with its original upload filename."""
	if not can_view:
		raise Http404

	stored_file = record.file
	if not stored_file or not stored_file.name:
		messages.error(request, "This file is no longer available.")
		return redirect("resources:list")
	try:
		if not stored_file.storage.exists(stored_file.name):
			raise FileNotFoundError(stored_file.name)
		file_handle = stored_file.open("rb")
	except (FileNotFoundError, OSError, ValueError):
		messages.error(request, "This file is no longer available.")
		return redirect("resources:list")
	filename = record.original_filename or stored_file.name.rsplit("/", 1)[-1]
	return FileResponse(file_handle, as_attachment=True, filename=filename)


@role_required(*APP_ROLES)
@never_cache
@require_http_methods(["GET"])
def monthly_resource_download(request, pk):
	"""Download a monthly theme file after checking the parent theme's visibility."""
	resource = get_object_or_404(
		MonthlyResource.objects.select_related("monthly_theme"), pk=pk,
	)
	return _download_private_file(
		request, resource, can_view_monthly_resource(request.user, resource),
	)


@role_required(*APP_ROLES)
@never_cache
@require_http_methods(["GET"])
def weekly_powerpoint_download(request, pk):
	"""Download a weekly presentation only for roles allowed to view it."""
	item = get_object_or_404(WeeklyPowerpoint, pk=pk)
	return _download_private_file(
		request, item, can_view_weekly_powerpoint(request.user, item),
	)


@role_required(*APP_ROLES)
@never_cache
@require_http_methods(["GET"])
def general_resource_download(request, pk):
	"""Download a general resource only for roles allowed to view it."""
	item = get_object_or_404(GeneralResource, pk=pk)
	return _download_private_file(
		request, item, can_view_general_resource(request.user, item),
	)


def _list_context(request, form, queryset, status):
	"""Paginate one filtered management list and preserve its query string."""
	paginator = Paginator(queryset, PAGE_SIZE)
	page = paginator.get_page(request.GET.get("page"))
	params = request.GET.copy()
	params.pop("page", None)
	data = form.cleaned_data
	return {
		"form": form,
		"page": page,
		"total": paginator.count,
		"status": status,
		"querystring": params.urlencode(),
		"active_filters": sum((bool(data.get("q")), status != "active")),
	}


def _apply_status(queryset, status):
	"""Limit an archivable resource queryset to its requested archive state."""
	if status == "active":
		return queryset.filter(archived_at__isnull=True)
	if status == "archived":
		return queryset.filter(archived_at__isnull=False)
	return queryset


def _run_action(request, pk, service, *, done, unchanged, target, to_list=False):
	"""Run an idempotent archive/restore action and return a toast message."""
	try:
		changed = service(request.user, pk, request=request)
	except ServiceError as error:
		messages.error(request, str(error))
	else:
		messages.success(request, done) if changed else messages.info(request, unchanged)
	return redirect(target) if to_list else redirect(target, pk=pk)


@manager_required
@never_cache
@require_http_methods(["GET"])
def theme_list(request):
	"""Search, filter, sort, and page through monthly themes."""
	form = ResourceFilterForm(request.GET, sort_choices=THEME_SORTS)
	form.is_valid()
	data = form.cleaned_data
	status = data.get("status") or "active"
	queryset = MonthlyTheme.objects.all()
	if status == "active":
		queryset = queryset.filter(archived_at__isnull=True)
	elif status == "archived":
		queryset = queryset.filter(archived_at__isnull=False)
	if data.get("q"):
		queryset = queryset.filter(Q(title__icontains=data["q"]) | Q(description__icontains=data["q"]))
	sort = data.get("sort") or THEME_SORTS[0][0]
	queryset = queryset.order_by(sort, "pk")
	return render(request, "resources/manage/theme_list.html", _list_context(request, form, queryset, status))


@manager_required
@never_cache
@require_http_methods(["GET", "POST"])
def theme_create(request):
	"""Show and process a new monthly theme form."""
	form = MonthlyThemeForm(request.POST or None)
	if request.method == "POST" and form.is_valid():
		try:
			theme = services.create_monthly_theme(request.user, **form.cleaned_data, request=request)
		except ServiceError as error:
			form.add_error(None, str(error))
		else:
			messages.success(request, "Monthly theme created as a draft.")
			return redirect("resources:theme_detail", pk=theme.pk)
	return render(request, "resources/manage/theme_form.html", {"form": form, "theme": None})


@manager_required
@never_cache
@require_http_methods(["GET", "POST"])
def theme_edit(request, pk):
	"""Show and process edits for an active monthly theme."""
	theme = get_object_or_404(MonthlyTheme, pk=pk)
	if theme.is_archived:
		messages.error(request, "Restore this monthly theme before editing it.")
		return redirect("resources:theme_detail", pk=theme.pk)
	form = MonthlyThemeForm(request.POST or None, instance=theme)
	if request.method == "POST" and form.is_valid():
		try:
			updated = services.update_monthly_theme(request.user, theme.pk, **form.cleaned_data, request=request)
		except ServiceError as error:
			form.add_error(None, str(error))
		else:
			messages.success(request, "Monthly theme updated.") if updated else messages.info(request, "No changes to save.")
			return redirect("resources:theme_detail", pk=theme.pk)
	return render(request, "resources/manage/theme_form.html", {"form": form, "theme": theme})


@manager_required
@never_cache
@require_http_methods(["GET"])
def theme_detail(request, pk):
	"""Show theme state, attached files, and permitted management actions."""
	theme = get_object_or_404(MonthlyTheme, pk=pk)
	return render(request, "resources/manage/theme_detail.html", {
		"theme": theme,
		"resources": theme.resources.order_by("kind", "title", "pk"),
		"can_force_delete": request.user.role == Role.SUPER_ADMIN,
	})


@manager_required
@never_cache
@require_POST
def theme_publish(request, pk):
	"""Publish or unpublish a theme using the state submitted by the form."""
	value = request.POST.get("published")
	if value not in ("0", "1"):
		messages.error(request, "Choose whether to publish or unpublish this theme.")
		return redirect("resources:theme_detail", pk=pk)
	try:
		changed = services.set_monthly_theme_published(request.user, pk, value == "1", request=request)
	except ServiceError as error:
		messages.error(request, str(error))
	else:
		if changed:
			messages.success(request, "Monthly theme published." if value == "1" else "Monthly theme unpublished.")
		else:
			messages.info(request, "The monthly theme already has that publication state.")
	return redirect("resources:theme_detail", pk=pk)


@manager_required
@never_cache
@require_POST
def theme_archive(request, pk):
	"""Archive a monthly theme."""
	return _run_action(
		request, pk, services.archive_monthly_theme,
		done="Monthly theme archived.", unchanged="Monthly theme is already archived.",
		target="resources:theme_detail",
	)


@manager_required
@never_cache
@require_POST
def theme_restore(request, pk):
	"""Restore a monthly theme."""
	return _run_action(
		request, pk, services.restore_monthly_theme,
		done="Monthly theme restored.", unchanged="Monthly theme is already active.",
		target="resources:theme_detail",
	)


@super_admin_required
@never_cache
@require_POST
def theme_delete(request, pk):
	"""Permanently delete an archived theme; only the super-admin may do this."""
	try:
		services.force_delete_monthly_theme(request.user, pk, request=request)
	except ServiceError as error:
		messages.error(request, str(error))
		return redirect("resources:theme_detail", pk=pk)
	messages.success(request, "Monthly theme and its files were permanently deleted.")
	return redirect("resources:theme_list")


@manager_required
@never_cache
@require_http_methods(["GET", "POST"])
def monthly_resource_create(request, theme_pk):
	"""Attach an optional file to one active monthly theme."""
	theme = get_object_or_404(MonthlyTheme, pk=theme_pk)
	if theme.is_archived:
		messages.error(request, "Restore this monthly theme before adding files.")
		return redirect("resources:theme_detail", pk=theme.pk)
	instance = MonthlyResource(monthly_theme=theme)
	form = MonthlyResourceForm(request.POST or None, request.FILES or None, instance=instance)
	if request.method == "POST" and form.is_valid():
		data = form.cleaned_data
		try:
			resource = services.create_monthly_resource(
				request.user, theme.pk, kind=data["kind"], title=data["title"],
				description=data["description"], file=data["file"], request=request,
			)
		except ServiceError as error:
			form.add_error(None, str(error))
		else:
			messages.success(request, "File added to the monthly theme.")
			return redirect("resources:theme_detail", pk=resource.monthly_theme_id)
	return render(request, "resources/manage/resource_form.html", {
		"form": form, "resource_type": "monthly", "theme": theme, "resource": None,
		"resource_limit_mb": settings.RESOURCE_UPLOAD_MAX_MB,
	})


@manager_required
@never_cache
@require_http_methods(["GET", "POST"])
def monthly_resource_edit(request, pk):
	"""Edit the details or replace a file attached to a monthly theme."""
	resource = get_object_or_404(MonthlyResource.objects.select_related("monthly_theme"), pk=pk)
	if resource.monthly_theme.is_archived:
		messages.error(request, "Restore the monthly theme before editing its files.")
		return redirect("resources:theme_detail", pk=resource.monthly_theme_id)
	form = MonthlyResourceForm(request.POST or None, request.FILES or None, instance=resource)
	if request.method == "POST" and form.is_valid():
		data = form.cleaned_data
		changes = {key: data[key] for key in ("kind", "title", "description")}
		if data.get("file"):
			changes["file"] = data["file"]
		try:
			services.update_monthly_resource(request.user, resource.pk, request=request, **changes)
		except ServiceError as error:
			form.add_error(None, str(error))
		else:
			messages.success(request, "Monthly resource updated.")
			return redirect("resources:theme_detail", pk=resource.monthly_theme_id)
	return render(request, "resources/manage/resource_form.html", {
		"form": form, "resource_type": "monthly", "theme": resource.monthly_theme,
		"resource": resource, "resource_limit_mb": settings.RESOURCE_UPLOAD_MAX_MB,
	})


@manager_required
@never_cache
@require_POST
def monthly_resource_delete(request, pk):
	"""Delete one monthly resource file record."""
	resource = get_object_or_404(MonthlyResource.objects.select_related("monthly_theme"), pk=pk)
	theme_id = resource.monthly_theme_id
	try:
		services.delete_monthly_resource(request.user, resource.pk, request=request)
	except ServiceError as error:
		messages.error(request, str(error))
	else:
		messages.success(request, "Monthly resource deleted.")
	return redirect("resources:theme_detail", pk=theme_id)


@manager_required
@never_cache
@require_http_methods(["GET"])
def weekly_list(request):
	"""Search, filter, sort, and page through weekly presentations."""
	form = ResourceFilterForm(request.GET, sort_choices=WEEKLY_SORTS)
	form.is_valid()
	data = form.cleaned_data
	status = data.get("status") or "active"
	queryset = _apply_status(WeeklyPowerpoint.objects.select_related("monthly_theme"), status)
	if data.get("q"):
		queryset = queryset.filter(Q(title__icontains=data["q"]) | Q(description__icontains=data["q"]))
	queryset = queryset.order_by(data.get("sort") or WEEKLY_SORTS[0][0], "pk")
	return render(request, "resources/manage/weekly_list.html", _list_context(request, form, queryset, status))


@manager_required
@never_cache
@require_http_methods(["GET", "POST"])
def weekly_create(request):
	"""Create a weekly presentation."""
	form = WeeklyPowerpointForm(request.POST or None, request.FILES or None)
	if request.method == "POST" and form.is_valid():
		data = form.cleaned_data
		theme = data["monthly_theme"]
		try:
			item = services.create_weekly_powerpoint(
				request.user, title=data["title"], description=data["description"],
				week_start=data["week_start"], monthly_theme_id=theme.pk if theme else None,
				file=data["file"], request=request,
			)
		except ServiceError as error:
			form.add_error(None, str(error))
		else:
			messages.success(request, "Weekly presentation created.")
			return redirect("resources:weekly_list")
	return render(request, "resources/manage/resource_form.html", {
		"form": form, "resource_type": "weekly", "resource": None,
		"resource_limit_mb": settings.RESOURCE_UPLOAD_MAX_MB,
	})


@manager_required
@never_cache
@require_http_methods(["GET", "POST"])
def weekly_edit(request, pk):
	"""Edit a weekly presentation and optionally replace its file."""
	item = get_object_or_404(WeeklyPowerpoint, pk=pk)
	if item.is_archived:
		messages.error(request, "Restore this weekly presentation before editing it.")
		return redirect("resources:weekly_list")
	form = WeeklyPowerpointForm(request.POST or None, request.FILES or None, instance=item)
	if request.method == "POST" and form.is_valid():
		data = form.cleaned_data
		changes = {key: data[key] for key in ("title", "description", "week_start")}
		theme = data["monthly_theme"]
		changes["monthly_theme_id"] = theme.pk if theme else None
		if data.get("file"):
			changes["file"] = data["file"]
		try:
			services.update_weekly_powerpoint(request.user, item.pk, request=request, **changes)
		except ServiceError as error:
			form.add_error(None, str(error))
		else:
			messages.success(request, "Weekly presentation updated.")
			return redirect("resources:weekly_list")
	return render(request, "resources/manage/resource_form.html", {
		"form": form, "resource_type": "weekly", "resource": item,
		"resource_limit_mb": settings.RESOURCE_UPLOAD_MAX_MB,
	})


@manager_required
@never_cache
@require_http_methods(["GET"])
def general_list(request):
	"""Search, filter, sort, and page through general resources."""
	form = ResourceFilterForm(request.GET, sort_choices=GENERAL_SORTS)
	form.is_valid()
	data = form.cleaned_data
	status = data.get("status") or "active"
	queryset = _apply_status(GeneralResource.objects.all(), status)
	if data.get("q"):
		queryset = queryset.filter(Q(title__icontains=data["q"]) | Q(description__icontains=data["q"]))
	queryset = queryset.order_by(data.get("sort") or GENERAL_SORTS[0][0], "pk")
	return render(request, "resources/manage/general_list.html", _list_context(request, form, queryset, status))


@manager_required
@never_cache
@require_http_methods(["GET", "POST"])
def general_create(request):
	"""Create a general resource document."""
	form = GeneralResourceForm(request.POST or None, request.FILES or None)
	if request.method == "POST" and form.is_valid():
		data = form.cleaned_data
		try:
			services.create_general_resource(request.user, **data, request=request)
		except ServiceError as error:
			form.add_error(None, str(error))
		else:
			messages.success(request, "General resource created.")
			return redirect("resources:general_list")
	return render(request, "resources/manage/resource_form.html", {
		"form": form, "resource_type": "general", "resource": None,
		"resource_limit_mb": settings.RESOURCE_UPLOAD_MAX_MB,
	})


@manager_required
@never_cache
@require_http_methods(["GET", "POST"])
def general_edit(request, pk):
	"""Edit a general resource and optionally replace its file."""
	item = get_object_or_404(GeneralResource, pk=pk)
	if item.is_archived:
		messages.error(request, "Restore this general resource before editing it.")
		return redirect("resources:general_list")
	form = GeneralResourceForm(request.POST or None, request.FILES or None, instance=item)
	if request.method == "POST" and form.is_valid():
		data = form.cleaned_data
		changes = {key: data[key] for key in ("title", "description", "category")}
		if data.get("file"):
			changes["file"] = data["file"]
		try:
			services.update_general_resource(request.user, item.pk, request=request, **changes)
		except ServiceError as error:
			form.add_error(None, str(error))
		else:
			messages.success(request, "General resource updated.")
			return redirect("resources:general_list")
	return render(request, "resources/manage/resource_form.html", {
		"form": form, "resource_type": "general", "resource": item,
		"resource_limit_mb": settings.RESOURCE_UPLOAD_MAX_MB,
	})


def _resource_action(request, pk, service, entity, *, done, unchanged):
	"""Run a resource archive/restore/delete action and choose its return page."""
	try:
		changed = service(request.user, pk, request=request)
	except ServiceError as error:
		messages.error(request, str(error))
	else:
		messages.success(request, done) if changed else messages.info(request, unchanged)
	return redirect(f"resources:{entity}_list")


@manager_required
@never_cache
@require_POST
def weekly_archive(request, pk):
	"""Archive a weekly presentation."""
	return _resource_action(request, pk, services.archive_weekly_powerpoint, "weekly",
							done="Weekly presentation archived.", unchanged="Weekly presentation is already archived.")


@manager_required
@never_cache
@require_POST
def weekly_restore(request, pk):
	"""Restore a weekly presentation."""
	return _resource_action(request, pk, services.restore_weekly_powerpoint, "weekly",
							done="Weekly presentation restored.", unchanged="Weekly presentation is already active.")


@super_admin_required
@never_cache
@require_POST
def weekly_delete(request, pk):
	"""Permanently delete an archived weekly presentation."""
	return _resource_action(request, pk, services.force_delete_weekly_powerpoint, "weekly",
							done="Weekly presentation deleted.", unchanged="Weekly presentation was not deleted.")


@manager_required
@never_cache
@require_POST
def general_archive(request, pk):
	"""Archive a general resource."""
	return _resource_action(request, pk, services.archive_general_resource, "general",
							done="General resource archived.", unchanged="General resource is already archived.")


@manager_required
@never_cache
@require_POST
def general_restore(request, pk):
	"""Restore a general resource."""
	return _resource_action(request, pk, services.restore_general_resource, "general",
							done="General resource restored.", unchanged="General resource is already active.")


@super_admin_required
@never_cache
@require_POST
def general_delete(request, pk):
	"""Permanently delete an archived general resource."""
	return _resource_action(request, pk, services.force_delete_general_resource, "general",
							done="General resource deleted.", unchanged="General resource was not deleted.")

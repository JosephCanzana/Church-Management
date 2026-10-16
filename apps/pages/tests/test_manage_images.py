"""Tests for the separated landing photo pools and their management workflows."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.accounts.models import Extension, Role, Status, User
from apps.audit.models import AuditLog
from apps.core.services import ServiceError
from apps.pages import manage_services
from apps.pages.models import LandingImage
from apps.pages.services import DEFAULT_GALLERY_IMAGES, DEFAULT_HERO_IMAGES, get_landing_context


def image_upload(name="church.png", color=(40, 110, 80)):
    """Create a small valid PNG upload for image validation and preview tests."""
    buffer = BytesIO()
    Image.new("RGB", (12, 8), color).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def make_user(role, extension=None):
    """Create an activated account for image management tests."""
    return User.objects.create_user(
        password="image-test-password",
        first_name="Photo",
        last_name=role,
        role=role,
        extension=extension,
        status=Status.ACTIVE,
        must_change_password=False,
    )


class LandingImageServiceTests(TestCase):
    """Check placement separation, limits, ordering, audit privacy, and file cleanup."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=Path(self.temp_dir.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.temp_dir.cleanup)
        self.extension = Extension.objects.create(name="image-test")
        self.admin = make_user(Role.ADMIN)
        self.super_admin = make_user(Role.SUPER_ADMIN)
        self.coordinator = make_user(Role.COORDINATOR, self.extension)

    def create_image(self, placement=LandingImage.Placement.HERO, caption="Photo", name="photo.png"):
        """Create a photo through the service using an image with a valid basename."""
        return manage_services.create_landing_image(
            self.admin,
            image=image_upload(name),
            caption=caption,
            placement=placement,
        )

    def test_each_pool_uses_its_own_defaults_until_it_has_active_images(self):
        self.assertEqual(len(get_landing_context()["hero_images"]), len(DEFAULT_HERO_IMAGES))
        self.assertEqual(len(get_landing_context()["gallery_images"]), len(DEFAULT_GALLERY_IMAGES))

        hero = self.create_image(caption="Hero only", name="hero.png")
        context = get_landing_context()
        self.assertEqual([photo["caption"] for photo in context["hero_images"]], ["Hero only"])
        self.assertEqual(len(context["gallery_images"]), len(DEFAULT_GALLERY_IMAGES))

        gallery = self.create_image(LandingImage.Placement.GALLERY, "Gallery only", "gallery.png")
        context = get_landing_context()
        self.assertEqual([photo["caption"] for photo in context["hero_images"]], ["Hero only"])
        self.assertEqual([photo["caption"] for photo in context["gallery_images"]], ["Gallery only"])

        manage_services.set_landing_image_active(self.admin, hero.pk, False)
        context = get_landing_context()
        self.assertEqual(len(context["hero_images"]), len(DEFAULT_HERO_IMAGES))
        self.assertEqual([photo["caption"] for photo in context["gallery_images"]], ["Gallery only"])
        self.assertTrue(gallery.image.storage.exists(gallery.image.name))

    def test_active_photo_limit_is_enforced_independently_per_pool(self):
        for index in range(manage_services.ACTIVE_IMAGE_LIMIT):
            self.create_image(caption=f"Hero {index}", name=f"hero-{index}.png")
        with self.assertRaises(ServiceError):
            self.create_image(caption="Too many", name="too-many.png")

        gallery = self.create_image(LandingImage.Placement.GALLERY, "Gallery", "gallery.png")
        self.assertEqual(gallery.placement, LandingImage.Placement.GALLERY)

    def test_activation_and_moving_photos_respect_target_pool_limit(self):
        hero_photos = [
            self.create_image(caption=f"Hero {index}", name=f"hero-{index}.png")
            for index in range(manage_services.ACTIVE_IMAGE_LIMIT)
        ]
        inactive = LandingImage.objects.create(
            image=image_upload("inactive.png"), caption="Inactive", is_active=False,
            placement=LandingImage.Placement.HERO,
        )
        with self.assertRaises(ServiceError):
            manage_services.set_landing_image_active(self.admin, inactive.pk, True)

        gallery_photos = [
            self.create_image(LandingImage.Placement.GALLERY, f"Gallery {index}", f"gallery-{index}.png")
            for index in range(manage_services.ACTIVE_IMAGE_LIMIT)
        ]
        with self.assertRaises(ServiceError):
            manage_services.update_landing_image(
                self.admin, hero_photos[0].pk, placement=LandingImage.Placement.GALLERY,
            )
        self.assertEqual(len(gallery_photos), manage_services.ACTIVE_IMAGE_LIMIT)

    def test_reorder_is_scoped_to_placement_and_audited_without_file_names(self):
        first = self.create_image(caption="First", name="first.png")
        second = self.create_image(caption="Second", name="second.png")
        other = self.create_image(LandingImage.Placement.GALLERY, "Other", "other.png")

        self.assertFalse(manage_services.move_landing_image(self.admin, first.pk, "up"))
        self.assertTrue(manage_services.move_landing_image(self.admin, second.pk, "up"))
        ordered = list(LandingImage.objects.filter(placement=LandingImage.Placement.HERO).order_by("sort_order", "pk"))
        self.assertEqual([item.pk for item in ordered], [second.pk, first.pk])
        self.assertEqual(other.sort_order, 0)
        log = AuditLog.objects.get(action="pages.landing_image_reordered")
        self.assertEqual(log.entity_id, second.pk)
        self.assertNotIn("second.png", str(log.after))

    def test_move_edit_toggle_and_delete_clean_up_files(self):
        photo = self.create_image(caption="Move", name="move.png")
        stored_name = photo.image.name
        updated = manage_services.update_landing_image(
            self.admin,
            photo.pk,
            caption="Moved photo",
            placement=LandingImage.Placement.GALLERY,
        )
        self.assertEqual(updated.caption, "Moved photo")
        self.assertEqual(updated.placement, LandingImage.Placement.GALLERY)
        self.assertTrue(updated.image.storage.exists(stored_name))
        self.assertTrue(manage_services.set_landing_image_active(self.admin, photo.pk, False))
        self.assertFalse(LandingImage.objects.get(pk=photo.pk).is_active)

        with self.captureOnCommitCallbacks(execute=True):
            manage_services.delete_landing_image(self.super_admin, photo.pk)
        self.assertFalse(updated.image.storage.exists(stored_name))
        self.assertFalse(LandingImage.objects.filter(pk=photo.pk).exists())

    def test_mutating_services_refuse_non_managers(self):
        with self.assertRaises(PermissionDenied):
            manage_services.create_landing_image(
                self.coordinator, image=image_upload(), placement=LandingImage.Placement.HERO,
            )
        self.assertFalse(LandingImage.objects.exists())


class LandingImageViewTests(TestCase):
    """Exercise photo manager pages, previews, confirmations, and upload validation."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=Path(self.temp_dir.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.temp_dir.cleanup)
        extension = Extension.objects.create(name="image-view-test")
        self.admin = make_user(Role.ADMIN)
        self.coordinator = make_user(Role.COORDINATOR, extension)

    def test_admin_can_open_photo_manager_and_other_roles_are_redirected(self):
        url = reverse("pages:pages_manage:image_list")
        self.client.force_login(self.admin)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hero carousel")
        self.assertContains(response, "Come as you are gallery")

        self.client.force_login(self.coordinator)
        self.assertRedirects(
            self.client.get(url), reverse("dashboards:coordinator"), fetch_redirect_response=False,
        )

    def test_upload_form_previews_photo_and_confirms_submission(self):
        self.client.force_login(self.admin)
        url = reverse("pages:pages_manage:image_create")
        response = self.client.get(url)
        self.assertContains(response, "image-preview")
        self.assertContains(response, "data-confirm=\"Upload this photo")

        response = self.client.post(url, {
            "caption": "Welcome",
            "placement": LandingImage.Placement.GALLERY,
            "image": image_upload("welcome.png"),
        })
        self.assertRedirects(response, reverse("pages:pages_manage:image_list"), fetch_redirect_response=False)
        record = LandingImage.objects.get()
        self.assertEqual(record.placement, LandingImage.Placement.GALLERY)
        self.assertEqual(record.original_filename, "welcome.png")

    def test_invalid_photo_extension_is_reported_inline(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("pages:pages_manage:image_create"), {
            "caption": "Bad",
            "placement": LandingImage.Placement.HERO,
            "image": image_upload("bad.gif"),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a photo file")
        self.assertFalse(LandingImage.objects.exists())

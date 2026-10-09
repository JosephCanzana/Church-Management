"""accounts: extension, custom User, special roles, default passwords,
email tokens, extension history.

Set  AUTH_USER_MODEL = "accounts.User"  BEFORE the first migrate.
"""
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import connection, models
from django.db.models import F, Q, Value
from django.db.models.functions import Coalesce, Lower

from apps.core.models import ArchivableModel, TimestampedModel, archived_matches_status
from apps.core.text import person_name
from django.contrib.auth.hashers import make_password
from .crypto import decrypt_secret, encrypt_secret


class Role(models.TextChoices):
    SUPER_ADMIN = "super_admin", "Super admin"
    ADMIN = "admin", "Admin"
    COORDINATOR = "coordinator", "Coordinator"
    MEMBER = "member", "Member"


class Status(models.TextChoices):
    NOT_ACTIVATED = "not_activated", "Not activated"
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    ARCHIVED = "archived", "Archived"


class ArchiveReason(models.TextChoices):
    MANUAL = "manual", "Manual"
    INACTIVE = "inactive", "Inactive"


def generate_account_id():
    """<account_number_seq counter><year in Asia/Manila>, e.g. 10002026.
    The sequence is created in core migration 0002."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT nextval('account_number_seq')::text "
            "|| to_char(now() AT TIME ZONE 'Asia/Manila', 'YYYY')"
        )
        return cur.fetchone()[0]


# ---------------------------------------------------------------- extension
class Extension(TimestampedModel, ArchivableModel):
    name = models.CharField(max_length=150, unique=True)
    coordinator = models.ForeignKey(
        "User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="coordinated_extensions",
    )  # app must check role == coordinator
    building_number = models.CharField(max_length=50, blank=True)
    street = models.CharField(max_length=150, blank=True)
    barangay = models.CharField(max_length=150, blank=True)
    municipality = models.CharField(max_length=150, default="Santa Rosa")
    province = models.CharField(max_length=150, default="Nueva Ecija")
    country = models.CharField(max_length=150, default="Philippines")
    postal_code = models.CharField(max_length=10, default="3101")

    class Meta:
        db_table = "extension"

    def __str__(self):
        return self.name


# --------------------------------------------------------------------- user
class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, account_id=None, password=None, **extra_fields):
        # account_id left blank -> User.save() generates it
        user = self.model(account_id=account_id or "", **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, account_id=None, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.SUPER_ADMIN)
        extra_fields.setdefault("status", Status.ACTIVE)
        extra_fields.setdefault("must_change_password", False)
        return self.create_user(account_id, password, **extra_fields)


class User(AbstractBaseUser, TimestampedModel, ArchivableModel):
    # AbstractBaseUser supplies `password` (the hash) and `last_login`.
    account_id = models.CharField(max_length=20, unique=True, blank=True, editable=False)
    first_name = models.CharField(max_length=150)
    middle_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150)
    email = models.EmailField(null=True, blank=True)            # VERIFIED only
    email_verified_at = models.DateTimeField(null=True, blank=True)
    pending_email = models.EmailField(null=True, blank=True)    # waiting for link click
    birth_date = models.DateField(null=True, blank=True)
    profile_image = models.ImageField(upload_to="profile_images/", blank=True)  # needs Pillow
    role = models.CharField(max_length=20, choices=Role.choices)
    extension = models.ForeignKey(
        Extension, null=True, blank=True, on_delete=models.RESTRICT, related_name="members",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOT_ACTIVATED)
    must_change_password = models.BooleanField(default=True)
    last_attended_at = models.DateField(null=True, blank=True, db_index=True)
    archive_reason = models.CharField(
        max_length=20, choices=ArchiveReason.choices, null=True, blank=True,
    )
    created_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    objects = UserManager()

    USERNAME_FIELD = "account_id"
    REQUIRED_FIELDS = ["first_name", "last_name"]   # press Enter at the Account id prompt

    class Meta:
        db_table = "app_user"
        constraints = [
            models.CheckConstraint(
                condition=Q(account_id__regex=r"^[1-9][0-9]{7,}$"),
                name="app_user_account_id_format",
            ),
            models.CheckConstraint(
                condition=Q(role__in=["super_admin", "admin"]) | Q(extension__isnull=False),
                name="app_user_extension_required",
            ),
            models.CheckConstraint(
                condition=(
                    Q(email__isnull=True, email_verified_at__isnull=True)
                    | Q(email__isnull=False, email_verified_at__isnull=False)
                ),
                name="app_user_email_verified_pair",
            ),
            archived_matches_status("app_user_archived_matches_status"),
            models.UniqueConstraint(
                Lower("email"), condition=Q(email__isnull=False), name="uq_app_user_email",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.account_id:
            self.account_id = generate_account_id()
        super().save(*args, **kwargs)

    # --- Django auth/admin hooks -------------------------------------------
    @property
    def is_active(self):
        # not_activated users must still be able to log in to set a password
        # suspended people stay blocked until a super-admin lifts the suspension
        return self.status not in (Status.ARCHIVED, Status.SUSPENDED)

    @property
    def is_staff(self):
        return self.is_active and self.role == Role.SUPER_ADMIN

    def has_perm(self, perm, obj=None):
        return self.is_staff

    def has_module_perms(self, app_label):
        return self.is_staff

    @property
    def full_name(self):
        """First, middle and last name for display (stored lowercase, shown in name case)."""
        return person_name(self.first_name, self.middle_name, self.last_name)

    def __str__(self):
        return f"{self.full_name} ({self.account_id})"


# ------------------------------------------------------------ special roles
class SpecialRole(models.Model):
    code = models.CharField(max_length=50, primary_key=True)   # 'tithes_offering', 'attendance'
    name = models.CharField(max_length=100)

    class Meta:
        db_table = "special_role"

    def __str__(self):
        return self.name


class UserSpecialRole(models.Model):
    # schema PK is (user, role); Django needs a single-column PK, so a
    # surrogate id + unique constraint is used instead.
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="special_roles")
    role = models.ForeignKey(
        SpecialRole, to_field="code", on_delete=models.PROTECT, related_name="holders",
    )
    extension = models.ForeignKey(Extension, on_delete=models.CASCADE, related_name="special_role_holders")
    granted_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_special_role"
        constraints = [
            models.UniqueConstraint(fields=["user", "role"], name="uq_user_special_role"),
        ]


class ExtensionSpecialRoleLimit(models.Model):
    extension = models.ForeignKey(Extension, on_delete=models.CASCADE, related_name="special_role_limits")
    role = models.ForeignKey(SpecialRole, to_field="code", on_delete=models.PROTECT, related_name="+")
    max_count = models.PositiveIntegerField()

    class Meta:
        db_table = "extension_special_role_limit"
        constraints = [
            models.UniqueConstraint(fields=["extension", "role"], name="uq_extension_role_limit"),
            models.CheckConstraint(condition=Q(max_count__gt=0), name="special_limit_positive"),
        ]


# -------------------------------------------------------- default passwords
class DefaultPassword(models.Model):
    """A default password kept by one person for the roles beneath them.

    Personal: coordinator A may keep one password for their members and
    coordinator B another; t Stored twice: a hash (what is copied onto new accounts) and an encrypted
    copy (so the owner can see it again). The key is DEFAULT_PASSWORD_KEY in .env.
    """

    class AppliesTo(models.TextChoices):
        ADMIN = "admin", "Admin"
        COORDINATOR = "coordinator", "Coordinator"
        MEMBER = "member", "Member"

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="default_passwords")
    applies_to_role = models.CharField(max_length=20, choices=AppliesTo.choices)
    password_hash = models.CharField(max_length=255)
    password_encrypted = models.TextField(blank=True, default="")   # Fernet token; "" = set before encryption existed
    updated_at = models.DateTimeField(auto_now=True)

    def set_plain(self, plain):
        """Store `plain` as both the hash and the encrypted copy. Call save() after."""
        self.password_hash = make_password(plain)
        self.password_encrypted = encrypt_secret(plain)

    @property
    def plain(self):
        """The readable password, or None (old row, or the key changed)."""
        return decrypt_secret(self.password_encrypted) if self.password_encrypted else None

    class Meta:
        db_table = "default_password"
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "applies_to_role"], name="uq_default_password",
            ),
        ]


# ------------------------------------------------------------- email tokens
class EmailToken(models.Model):
    class Purpose(models.TextChoices):
        VERIFY_EMAIL = "verify_email", "Verify email"
        RESET_PASSWORD = "reset_password", "Reset password"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="email_tokens")
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    email = models.EmailField()                          # address the token was sent to
    token_hash = models.CharField(max_length=128, unique=True)   # never store the raw token
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "email_token"
        indexes = [models.Index(fields=["user", "purpose"], name="ix_email_token_user")]


# ------------------------------------------------------ extension history
class UserExtensionHistory(models.Model):
    class Reason(models.TextChoices):
        INITIAL = "initial", "Initial"
        MANUAL = "manual", "Manual"
        AUTO_TRANSFER = "auto_transfer", "Auto transfer"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="extension_history")
    extension = models.ForeignKey(
        Extension, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    from_date = models.DateField()
    to_date = models.DateField(null=True, blank=True)    # NULL = current
    reason = models.CharField(max_length=20, choices=Reason.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_extension_history"
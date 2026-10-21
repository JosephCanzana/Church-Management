"""Public registration matching and leader decisions. Never log request personal data."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import Http404
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from apps.accounts.address import ADDRESS_FIELDS, check_address
from apps.accounts.contact import clean_phone, is_valid_phone, PHONE_MESSAGE
from apps.accounts.models import Extension, Gender, PersonType, RegistrationRequest, Role, Status, User
from apps.accounts.services.users import create_user, grant_account, promote_guest, update_user, default_password_row
from apps.accounts.validators import generate_strong_password
from apps.audit.services import log_action
from apps.core.models import SiteSetting
from apps.core.services import ServiceError, consume_submission_token, new_submission_token
from apps.core.text import clean_suffix, clean_text
from apps.notifications.services import notify_many
import logging
logger = logging.getLogger(__name__)

GENERIC_REFUSAL = "We could not accept this request. Please contact your church office for help."
MAX_PENDING_PER_IDENTITY = 3
IP_WINDOW_SECONDS = settings.REGISTRATION_IP_WINDOW_SECONDS
IP_MAX_SUBMISSIONS = settings.REGISTRATION_IP_MAX_SUBMISSIONS
RETENTION_DAYS = settings.REGISTRATION_REQUEST_RETENTION_DAYS
MAX_AGE_YEARS = 200
MAX_REJECTION_REASON = 1000

def _clean_birth_date(value):
    """Return a real, plausible date or None."""
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value.strip())
        except ValueError:
            return None
    if not isinstance(value, date):
        return None
    today = timezone.localdate()
    if value > today or value.year < today.year - MAX_AGE_YEARS:
        return None
    return value

def registration_auto_approve():
    row = SiteSetting.objects.filter(pk="registration_auto_approve").first()
    return bool(row.value) if row else False


def _approval_mail_is_configured():
    return settings.MAILERS.get("default", {}).get("BACKEND") != "django.core.mail.backends.console.EmailBackend"


def set_registration_auto_approve(actor, request, enabled):
    from apps.accounts.permissions import require_site_settings_manager

    require_site_settings_manager(actor)
    key = "registration_auto_approve"
    with transaction.atomic():
        row, _ = SiteSetting.objects.select_for_update().get_or_create(key=key, defaults={"value": False})
        old = bool(row.value)
        if old == bool(enabled):
            return False
        row.value, row.updated_by = bool(enabled), actor
        row.save(update_fields=["value", "updated_by", "updated_at"])
        log_action("site_setting.registration_auto_approve_toggled", actor=actor, request=request,
                   entity_type="site_setting", entity_label=key,
                   before={"enabled": old}, after={"enabled": bool(enabled)})
    return True


def registration_requests_for(actor):
    """The sole approval queryset scope: admins see all; coordinators only their extension."""
    if not actor or not getattr(actor, "is_authenticated", False):
        return RegistrationRequest.objects.none()
    if actor.role in (Role.SUPER_ADMIN, Role.ADMIN):
        return RegistrationRequest.objects.all()
    if actor.role == Role.COORDINATOR and actor.extension_id:
        return RegistrationRequest.objects.filter(extension_id=actor.extension_id)
    return RegistrationRequest.objects.none()


def _identity(data):
    return {key: clean_text(data.get(key, "")) for key in ("first_name", "middle_name", "last_name")} | {
        "suffix": clean_suffix(data.get("suffix", "")), "birth_date": data["birth_date"]
    }


def _pending_group(identity):
    return RegistrationRequest.objects.filter(
        status=RegistrationRequest.Status.PENDING,
        first_name=identity["first_name"], middle_name=identity["middle_name"],
        last_name=identity["last_name"], birth_date=identity["birth_date"],
    )


def _throttle(ip):
    if not ip:
        return
    key = f"registration:ip:{ip}"
    count = cache.get(key, 0)
    if count >= IP_MAX_SUBMISSIONS:
        raise ServiceError("Please wait before submitting another request.")
    if not cache.add(key, 1, timeout=IP_WINDOW_SECONDS):
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=IP_WINDOW_SECONDS)


def throttle_registration_ip(ip_address):
    """Apply the public POST throttle before form validation as well as before service work."""
    _throttle(ip_address)


@dataclass
class SubmissionResult:
    request: object = None
    duplicate: bool = False


def submit_registration(data, *, token):
    """Match, deduplicate and create one public request in one transaction."""
    data = dict(data)
    identity = _identity(data)
    identity["birth_date"] = _clean_birth_date(identity["birth_date"])
    if not identity["first_name"] or not identity["last_name"]:
        raise ServiceError("Enter your first name and last name.")
    if identity["birth_date"] is None:
        raise ServiceError("Enter a valid birth date.")
    if data.get("gender") not in Gender.values:
        raise ServiceError("Choose a valid gender.")
    try:
        validate_email(data.get("email", ""))
    except ValidationError:
        raise ServiceError("Enter a valid email address.")
    data["email"] = data["email"].strip().lower()
    data["phone_number"] = clean_phone(data.get("phone_number", ""))
    if not data["phone_number"] or not is_valid_phone(data["phone_number"]):
        raise ServiceError(PHONE_MESSAGE)
    optional_address = {"street", "building_number"}
    for field in ADDRESS_FIELDS:
        if field == "postal_code":
            data[field] = " ".join(str(data.get(field, "")).split())
        else:
            data[field] = clean_text(data.get(field, ""))
    missing = [
        field.replace("_", " ") for field in ADDRESS_FIELDS
        if field not in optional_address and not data.get(field)
    ]
    if missing:
        raise ServiceError("Please complete the address: " + ", ".join(missing) + ".")
    address_errors = check_address({field: data[field] for field in ADDRESS_FIELDS})
    if address_errors:
        raise ServiceError(next(iter(address_errors.values())))
    with transaction.atomic():
        if not consume_submission_token(token):
            return SubmissionResult(duplicate=True)
        # Serialize equivalent identities to enforce the three-request cap under concurrent POSTs.
        with transaction.get_connection().cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [
                "|".join([identity["first_name"], identity["middle_name"], identity["last_name"], identity["birth_date"].isoformat()])
            ])
        group = _pending_group(identity)
        email = data["email"]
        if group.filter(email__iexact=email).exists():
            return SubmissionResult(duplicate=True)
        if group.count() >= MAX_PENDING_PER_IDENTITY:
            raise ServiceError("We cannot accept another request for this name and birth date right now.")

        identity_name = {key: identity[key] for key in ("first_name", "middle_name", "last_name", "birth_date")}
        people = User.objects.filter(**identity_name)
        suspended = people.filter(status=Status.SUSPENDED, suffix=identity["suffix"]).first()
        if suspended:
            raise ServiceError(GENERIC_REFUSAL)
        member_accounts = people.filter(person_type=PersonType.MEMBER_ACCOUNT).first()
        if member_accounts:
            raise ServiceError(GENERIC_REFUSAL)
        matched = (
            people.filter(person_type=PersonType.MEMBER_RECORD).order_by("id").first()
            or people.filter(person_type=PersonType.GUEST).order_by("id").first()
        )
        # A member record may be approved without review only when the explicit site switch is on.
        auto = bool(
            matched and matched.person_type == PersonType.MEMBER_RECORD
            and registration_auto_approve() and _approval_mail_is_configured()
        )
        extension = Extension.objects.select_for_update().filter(
            pk=data["extension"].pk, archived_at__isnull=True,
        ).first()
        if extension is None:
            raise ServiceError("Choose an extension that is open for registration.")
        req = RegistrationRequest.objects.create(
            **identity,
            gender=data["gender"], email=email, extension=extension,
            phone_number=data["phone_number"],
            **{name: data.get(name, "") for name in ADDRESS_FIELDS},
            matched_user=matched,
            status=RegistrationRequest.Status.PENDING,
        )
        recipients = set(User.objects.filter(
            role__in=(Role.ADMIN, Role.SUPER_ADMIN), status=Status.ACTIVE,
            person_type=PersonType.MEMBER_ACCOUNT,
        ).values_list("id", flat=True))
        coordinator_id = extension.coordinator_id
        if coordinator_id and User.objects.filter(
            pk=coordinator_id, role=Role.COORDINATOR, status=Status.ACTIVE,
            person_type=PersonType.MEMBER_ACCOUNT,
        ).exists():
            recipients.add(coordinator_id)
        log_action(
            "registration.submit", entity_type="registration_request", entity_id=req.pk,
            after={"request_id": req.pk, "possible_duplicates": max(group.count() - 1, 0)},
        )
        if auto:
            approver = User.objects.filter(
                pk=coordinator_id, role=Role.COORDINATOR, status=Status.ACTIVE,
                person_type=PersonType.MEMBER_ACCOUNT,
            ).first() if coordinator_id else None
            approver = approver or User.objects.filter(
                role__in=(Role.SUPER_ADMIN, Role.ADMIN), status=Status.ACTIVE,
                person_type=PersonType.MEMBER_ACCOUNT,
            ).order_by("role", "id").first()
            if approver:
                result = approve_registration(approver, None, req.pk, token=new_submission_token())
                if result:
                    _approved_req, user, password = result
                    def deliver():
                        try:
                            send_approval_email(user, password)
                        except Exception:
                            notify_many(
                                recipients, "registration_email_failed", entity_type="app_user",
                                entity_id=user.pk, data={},
                            )
                    transaction.on_commit(deliver)
            else:
                notify_many(recipients, "registration_request", entity_type="registration_request", entity_id=req.pk, data={})
        else:
            notify_many(
                recipients, "registration_request", entity_type="registration_request", entity_id=req.pk,
                data={},
            )
    return SubmissionResult(request=req)


def approve_registration(actor, request, request_id, *, token):
    """Approve a scoped request; create or grant its account and close same-identity requests."""
    with transaction.atomic():
        req = registration_requests_for(actor).select_for_update().filter(pk=request_id).first()
        if req is None:
            raise Http404("That request is no longer available.")
        if req.status != RegistrationRequest.Status.PENDING:
            raise ServiceError("That request has already been decided.")
        approved_email = req.email.strip().lower()
        try:
            validate_email(approved_email)
        except ValidationError:
            raise ServiceError("This request has an invalid email address and cannot be approved.")
        email_owner = User.objects.filter(email__iexact=approved_email).exclude(pk=req.matched_user_id).first()
        if email_owner:
            raise ServiceError("That email address is already used by another account.")

        user = req.matched_user
        password_info = None
        if user and user.person_type in (PersonType.MEMBER_RECORD, PersonType.GUEST):
            if user.person_type == PersonType.GUEST:
                promote_guest(actor, request, user)
            update_user(actor, request, user, {
                "first_name": req.first_name, "middle_name": req.middle_name,
                "last_name": req.last_name, "suffix": req.suffix, "birth_date": req.birth_date,
                "gender": req.gender, "age_network": user.age_network,
                "phone_number": req.phone_number,
                **{field: getattr(req, field) for field in ADDRESS_FIELDS},
                "role": Role.MEMBER, "extension": req.extension,
            })
            user.refresh_from_db()
            result = grant_account(actor, request, user, token=token)
            if result.duplicate:
                return None
            user, password_info = result.user, result.password
            user.email, user.email_verified_at = req.email, timezone.now()
            user.save()
        else:
            data = {
                "first_name": req.first_name, "middle_name": req.middle_name,
                "last_name": req.last_name, "suffix": req.suffix, "birth_date": req.birth_date,
                "gender": req.gender, "age_network": "", "phone_number": req.phone_number,
                **{field: getattr(req, field) for field in ADDRESS_FIELDS},
                "role": Role.MEMBER, "extension": req.extension, "person_type": PersonType.MEMBER_ACCOUNT,
                "email": "", "password": "",
            }
            result = create_user(actor, request, data, token=token)
            if result.duplicate:
                return None
            user, password_info = result.user, result.password
            user.email, user.email_verified_at = req.email, timezone.now()
            user.save(update_fields=["email", "email_verified_at", "updated_at"])
        req.status, req.decided_by, req.decided_at = RegistrationRequest.Status.APPROVED, actor, timezone.now()
        req.save(update_fields=["status", "decided_by", "decided_at"])
        group = _pending_group({"first_name": req.first_name, "middle_name": req.middle_name,
                                "last_name": req.last_name, "birth_date": req.birth_date})
        duplicate_ids = list(group.exclude(pk=req.pk).values_list("pk", flat=True))
        group.exclude(pk=req.pk).update(status=RegistrationRequest.Status.DUPLICATE,
                                        decided_by=actor, decided_at=timezone.now())
        log_action("registration.approve", actor=actor, request=request,
                   entity_type="registration_request", entity_id=req.pk,
                   after={"request_id": req.pk, "user_id": user.pk, "closed_duplicates": len(duplicate_ids)})
        password = password_info.value
        if password_info.kind == "default":
            row = default_password_row(actor, Role.MEMBER)
            password = row.plain if row else None
            if not password:
                # An old default row may predate encrypted copies or its key may have changed.
                # Never send an empty or unknowable password; replace it with a fresh one-time value.
                password = generate_strong_password()
                user.set_password(password)
                user.must_change_password = True
                user.save(update_fields=["password", "must_change_password", "updated_at"])
    return req, user, password


def reject_registration(actor, request, request_id, *, token, reason):
    reason = " ".join((reason or "").split())
    if not reason:
        raise ServiceError("Write a message explaining why the request was rejected.")
    if len(reason) > MAX_REJECTION_REASON:
        raise ServiceError("Keep the message under 1000 characters.")
    if not _approval_mail_is_configured():
        raise ServiceError("Email is not configured, so the rejection message cannot be sent.")

    with transaction.atomic():
        if not consume_submission_token(token):
            return False
        req = registration_requests_for(actor).select_for_update().filter(pk=request_id).first()
        if req is None:
            raise Http404("That request is no longer available.")
        if req.status != RegistrationRequest.Status.PENDING:
            raise ServiceError("That request has already been decided.")
        req.status, req.decided_by, req.decided_at = RegistrationRequest.Status.REJECTED, actor, timezone.now()
        req.save(update_fields=["status", "decided_by", "decided_at"])
        log_action("registration.reject", actor=actor, request=request,
                   entity_type="registration_request", entity_id=req.pk,
                   after={"request_id": req.pk})

        email, name, req_pk = req.email, req.first_name.title(), req.pk
        recipients = {actor.pk}

        def deliver():
            try:
                send_rejection_email(email, name, reason)
            except Exception:
                logger.exception("Rejection email failed for request %s", req_pk)
                notify_many(
                    recipients, "registration_email_failed", entity_type="registration_request",
                    entity_id=req_pk, data={},
                )

        transaction.on_commit(deliver)
    return True

def send_rejection_email(email, name, reason):
    if not _approval_mail_is_configured():
        raise RuntimeError("Email is not configured.")
    message = EmailMultiAlternatives(
        subject="Your registration request",
        body=(
            f"Hello {name},\n\n"
            "We could not approve your registration request.\n\n"
            f"Reason: {reason}\n\n"
            "You are welcome to submit a new request after correcting this."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )
    if message.send(fail_silently=False) != 1:
        raise RuntimeError("The rejection email was not accepted by the mail backend.")
        
def send_credentials_email(user, password, *, subject, intro):
    if not _approval_mail_is_configured():
        raise RuntimeError("Credential email is not configured; use the show-once credential page.")
    if not user.email:
        raise RuntimeError("This person has no email address.")
    sign_in = settings.SITE_URL.rstrip("/") + "/accounts/login/"
    message = EmailMultiAlternatives(
        subject=subject,
        body=(f"{intro}\n\nAccount ID: {user.account_id}\n"
              f"Sign in: {sign_in}\nTemporary password: {password}\n\n"
              "You must change this password when you first sign in."),
        from_email=settings.DEFAULT_FROM_EMAIL, to=[user.email],
    )
    if message.send(fail_silently=False) != 1:
        raise RuntimeError("The credential email was not accepted by the mail backend.")


def send_approval_email(user, password):
    send_credentials_email(
        user, password,
        subject="Your church account is ready",
        intro="Your account has been approved.",
    )

def cleanup_decided_requests(*, retention_days=RETENTION_DAYS):
    cutoff = timezone.now() - timedelta(days=retention_days)
    deleted, _ = RegistrationRequest.objects.filter(
        status__in=(RegistrationRequest.Status.APPROVED, RegistrationRequest.Status.REJECTED,
                    RegistrationRequest.Status.DUPLICATE), decided_at__lt=cutoff,
    ).delete()
    return deleted

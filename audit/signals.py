from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import AuditEvent
from .recording import client_ip, current_request, end_session, record, start_session

WATCHED_FIELDS = ("is_active", "is_staff", "is_superuser", "password")


@receiver(user_logged_in)
def _signed_in(sender, request, user, **kwargs):
    if request is None:
        return
    start_session(request, user)
    ip = client_ip(request)
    known_ip = AuditEvent.objects.filter(user=user, kind=AuditEvent.Kind.SIGN_IN, ip_address=ip).exists()
    first = not AuditEvent.objects.filter(user=user, kind=AuditEvent.Kind.SIGN_IN).exists()
    record(AuditEvent.Kind.SIGN_IN, request=request, user=user,
           detail={"new_address": not known_ip and not first, "first_sign_in": first})


@receiver(user_logged_out)
def _signed_out(sender, request, user, **kwargs):
    if request is None:
        return
    record(AuditEvent.Kind.SIGN_OUT, request=request, user=user)
    end_session(request)


@receiver(user_login_failed)
def _sign_in_failed(sender, credentials, request=None, **kwargs):
    username = credentials.get("username", "") if isinstance(credentials, dict) else ""
    known = get_user_model().objects.filter(username=username).first() if username else None
    record(AuditEvent.Kind.SIGN_IN_FAILED, request=request, user=known, username=username,
           detail={"unknown_account": known is None})


@receiver(pre_save, sender=get_user_model())
def _remember_access(sender, instance, **kwargs):
    if instance.pk:
        before = sender.objects.filter(pk=instance.pk).values(*WATCHED_FIELDS).first()
        instance._audit_before = before


@receiver(post_save, sender=get_user_model())
def _account_saved(sender, instance, created, **kwargs):
    request = current_request.get()
    by = getattr(request, "user", None) if request else None
    by_name = by.get_username() if by is not None and by.is_authenticated else "system"
    if created:
        record(AuditEvent.Kind.ACCOUNT_CREATED, request=request, user=instance,
               detail={"by": by_name, "is_staff": instance.is_staff, "is_superuser": instance.is_superuser})
        return
    before = getattr(instance, "_audit_before", None)
    if not before:
        return
    changed = [field for field in WATCHED_FIELDS if before[field] != getattr(instance, field)]
    if changed:
        # Never store password hashes; only say that the password changed.
        detail = {"by": by_name, "changed": changed}
        detail.update({field: getattr(instance, field) for field in changed if field != "password"})
        record(AuditEvent.Kind.ACCOUNT_CHANGED, request=request, user=instance, detail=detail)

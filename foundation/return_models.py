"""Non-posting synthetic return requests; never financial/stock documents."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class SalesReturnPolicy(models.Model):
    business_unit = models.OneToOneField("BusinessUnit", on_delete=models.PROTECT)
    provisional_calendar_days = models.PositiveSmallIntegerField(default=14)
    # Owner-confirmed company rule; legal validation remains separate.
    date_basis = models.CharField(max_length=24, default="delivery", editable=False)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(provisional_calendar_days__gt=0), name="return_days_positive"),
            models.CheckConstraint(condition=Q(date_basis="delivery"), name="return_basis_delivery"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="return_policy_demo_only"),
        ]


class SalesReturnIntake(models.Model):
    quote = models.ForeignKey("SalesQuote", on_delete=models.PROTECT, related_name="return_intakes")
    idempotency = models.OneToOneField("IdempotencyRecord", on_delete=models.PROTECT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    request_date = models.DateField()
    receiving_mode = models.CharField(max_length=24, choices=[
        ("showroom", "Received at Tivoli showroom"),
        ("warehouse_collection", "Warehouse collection for large item"),
    ])
    location = models.ForeignKey("Location", on_delete=models.PROTECT)
    receipt_collection_details = models.CharField(max_length=1000)
    proposed_refund_amount = models.DecimalField(max_digits=16, decimal_places=2)
    original_payment_method = models.CharField(max_length=8, choices=[("cash", "Cash"), ("card", "Card")])
    requested_refund_method = models.CharField(max_length=8, choices=[("cash", "Cash"), ("card", "Card")])
    refund_preference = models.CharField(max_length=24, default="usually_original")
    # Explicit source-line links and quantities, plus later inspection facts.
    lines = models.JSONField()
    status = models.CharField(max_length=24, default="awaiting_inspection")
    policy_snapshot = models.JSONField()
    delivery_window = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="return_intake_demo_only"),
            models.CheckConstraint(condition=Q(proposed_refund_amount__gte=0), name="return_proposed_refund_nonnegative"),
            models.CheckConstraint(condition=Q(status__in=["awaiting_inspection", "finance_pending"]), name="return_status_nonposting"),
            models.CheckConstraint(condition=Q(receiving_mode__in=["showroom", "warehouse_collection"]), name="return_receiving_mode_allowed"),
            models.CheckConstraint(condition=Q(original_payment_method__in=["cash", "card"]), name="return_original_method_allowed"),
            models.CheckConstraint(condition=Q(requested_refund_method__in=["cash", "card"]), name="return_requested_method_allowed"),
            models.CheckConstraint(condition=Q(refund_preference__in=["usually_original", "cash", "card"]), name="return_preference_allowed"),
        ]


class SalesReturnEvent(models.Model):
    intake = models.ForeignKey(SalesReturnIntake, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    kind = models.CharField(max_length=24)
    details = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["occurred_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="return_event_demo_only"),
            models.CheckConstraint(condition=Q(kind__in=["intake_recorded", "inspection_recorded"]), name="return_event_kind_allowed"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Return intake audit facts are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Return intake audit facts are append-only.")

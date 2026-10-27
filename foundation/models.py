import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.utils import timezone
from .return_models import SalesReturnPolicy, SalesReturnIntake, SalesReturnEvent
from .navigation_destinations import FUNCTION_DESTINATIONS


class LegalEntity(models.Model):
    name = models.CharField(max_length=120)
    code = models.SlugField(max_length=40, unique=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="legal_entity_demo_only"),
        ]

    def __str__(self):
        return self.name


class BusinessUnitPalette(models.TextChoices):
    TIVOLI = "tivoli", "Tivoli orange"
    ROCHE = "roche", "Roche Bobois light blue"
    NATUZZI = "natuzzi", "Natuzzi light brown"
    SLATE = "slate", "Slate"


class CustomBusinessUnitPalette(models.Model):
    name = models.CharField(max_length=80)
    accent_color = models.CharField(max_length=7)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="custom_palette_name_ci_unique"),
            models.CheckConstraint(condition=~Q(name=""), name="custom_palette_name_nonempty"),
            models.CheckConstraint(
                condition=Q(accent_color__regex=r"^#[0-9A-Fa-f]{6}$"),
                name="custom_palette_accent_hex",
            ),
        ]

    def __str__(self):
        return self.name


class BusinessUnit(models.Model):
    Palette = BusinessUnitPalette

    legal_entity = models.ForeignKey(LegalEntity, on_delete=models.PROTECT, related_name="business_units")
    name = models.CharField(max_length=120)
    code = models.SlugField(max_length=20, unique=True)
    is_active = models.BooleanField(default=True)
    theme_palette = models.CharField(max_length=16, choices=Palette.choices, default=Palette.SLATE)
    custom_theme_palette = models.ForeignKey(
        CustomBusinessUnitPalette,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="business_units",
    )
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="business_unit_demo_only"),
            models.CheckConstraint(
                condition=Q(theme_palette__in=BusinessUnitPalette.values),
                name="business_unit_palette_allowed",
            ),
        ]

    def __str__(self):
        return self.name


class ProductBrand(models.Model):
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=8, unique=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="product_brand_demo_only"),
        ]

    def __str__(self):
        return self.name


LOCATION_CODE_VALIDATOR = RegexValidator(
    r"^[A-Za-z0-9][A-Za-z0-9.-]*$",
    "Location codes use letters, digits, dots and dashes, for example W41-03.1.",
)


class Location(models.Model):
    class Kind(models.TextChoices):
        SHOWROOM = "showroom", "Showroom"
        WAREHOUSE = "warehouse", "Warehouse"
        FACTORY = "factory", "Factory and workshop"
        VIRTUAL = "virtual", "Virtual"

    legal_entity = models.ForeignKey(LegalEntity, on_delete=models.PROTECT, related_name="locations")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    code = models.CharField(max_length=80, unique=True, validators=[LOCATION_CODE_VALIDATOR])
    name = models.CharField(max_length=160)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.WAREHOUSE)
    area = models.CharField(max_length=80, blank=True)
    street = models.CharField(max_length=80, blank=True)
    sub_location = models.CharField(max_length=120, blank=True)
    business_units = models.ManyToManyField(BusinessUnit, related_name="locations", blank=True)
    # A retired location takes no new stock; whatever it still holds can only be transferred out.
    is_active = models.BooleanField(default=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="location_demo_only"),
        ]

    def clean(self):
        super().clean()
        if self.parent_id:
            if self.parent_id == self.pk:
                raise ValidationError({"parent": "A location cannot be its own parent."})
            if self.parent.legal_entity_id != self.legal_entity_id:
                raise ValidationError({"parent": "A location parent must belong to the same legal entity."})
            seen = {self.pk} if self.pk else set()
            ancestor = self.parent
            while ancestor is not None:
                if ancestor.pk in seen:
                    raise ValidationError({"parent": "Location hierarchy cannot contain a cycle."})
                seen.add(ancestor.pk)
                ancestor = ancestor.parent

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class LocationCapacity(models.Model):
    """Explicitly configured physical-location capacity; no m²/m³ conversion."""

    location = models.OneToOneField(
        Location,
        on_delete=models.PROTECT,
        related_name="capacity_profile",
    )
    total_floor_area_m2 = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    allocated_storage_floor_area_m2 = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    received_goods_volume_m3 = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="location_capacities_updated",
    )
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["location__code"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="location_capacity_demo_only"),
            models.CheckConstraint(
                condition=(
                    Q(
                        total_floor_area_m2__isnull=True,
                        allocated_storage_floor_area_m2__isnull=True,
                        received_goods_volume_m3__isnull=True,
                    )
                    | Q(
                        total_floor_area_m2__isnull=False,
                        allocated_storage_floor_area_m2__isnull=False,
                        received_goods_volume_m3__isnull=False,
                    )
                ),
                name="location_capacity_values_all_or_none",
            ),
            models.CheckConstraint(
                condition=(
                    Q(total_floor_area_m2__isnull=True)
                    | (
                        Q(total_floor_area_m2__gte=0)
                        & Q(allocated_storage_floor_area_m2__gte=0)
                        & Q(received_goods_volume_m3__gte=0)
                        & Q(allocated_storage_floor_area_m2__lte=F("total_floor_area_m2"))
                    )
                ),
                name="location_capacity_nonnegative_and_bounded",
            ),
        ]


WAREHOUSE_CAPACITY_CAPABILITY_VALUES = ("view_capacity", "edit_capacity")
WAREHOUSE_CAPACITY_AUDIT_KIND_VALUES = (
    "location_configured",
    "capacity_edited",
    "assigned",
    "revoked",
)


class LocationCapacityGrant(models.Model):
    class Capability(models.TextChoices):
        VIEW = "view_capacity", "View warehouse capacity"
        EDIT = "edit_capacity", "Edit warehouse capacity"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="location_capacity_grants",
    )
    location = models.ForeignKey(
        LocationCapacity,
        on_delete=models.PROTECT,
        related_name="grants",
    )
    capability = models.CharField(max_length=24, choices=Capability.choices)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="location_capacity_grants_made",
    )
    created_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["location__location__code", "user__username", "capability"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "location", "capability"],
                condition=Q(revoked_at__isnull=True),
                name="active_location_capacity_grant_unique",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="location_capacity_grant_demo_only"),
            models.CheckConstraint(
                condition=Q(capability__in=WAREHOUSE_CAPACITY_CAPABILITY_VALUES),
                name="location_capacity_grant_capability_valid",
            ),
        ]


class LocationCapacityAudit(models.Model):
    class Kind(models.TextChoices):
        LOCATION_CONFIGURED = "location_configured", "Physical capacity location configured"
        CAPACITY_EDITED = "capacity_edited", "Warehouse capacity edited"
        ASSIGNED = "assigned", "Capacity permission assigned"
        REVOKED = "revoked", "Capacity permission revoked"

    kind = models.CharField(max_length=24, choices=Kind.choices)
    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="capacity_audit_events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="location_capacity_audit_events",
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="location_capacity_audit_targets",
    )
    capability = models.CharField(max_length=24, blank=True)
    grant = models.ForeignKey(
        LocationCapacityGrant,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="audit_events",
    )
    before_values = models.JSONField(default=dict)
    after_values = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="location_capacity_audit_demo_only"),
            models.CheckConstraint(
                condition=Q(kind__in=WAREHOUSE_CAPACITY_AUDIT_KIND_VALUES),
                name="location_capacity_audit_kind_valid",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Warehouse capacity audit records are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Warehouse capacity audit records are append-only.")


class Item(models.Model):
    brand = models.ForeignKey(ProductBrand, on_delete=models.PROTECT, related_name="items")
    item_code = models.CharField(max_length=40, unique=True)
    description = models.CharField(max_length=240)
    category_label = models.CharField(max_length=80, blank=True)
    name_en = models.CharField(max_length=240, blank=True)
    name_ar = models.CharField(max_length=240, blank=True)
    description_ar = models.CharField(max_length=240, blank=True)
    category_ar = models.CharField(max_length=80, blank=True)
    image_path = models.CharField(max_length=300, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["item_code"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="item_demo_only"),
        ]

    def __str__(self):
        return f"{self.item_code} — {self.description}"


class Variant(models.Model):
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="variants")
    sku = models.CharField(max_length=48, unique=True)
    description = models.CharField(max_length=160)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["sku"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="variant_demo_only"),
        ]

    def __str__(self):
        return self.sku


class UserActionGrant(models.Model):
    class Action(models.TextChoices):
        READ_STOCK = "read_stock", "Read stock"
        CREATE_ORDER = "create_order", "Create order"
        REQUEST_TRANSFER = "request_transfer", "Request stock transfer"
        APPROVE_TRANSFER = "approve_transfer", "Approve stock transfer"
        EXECUTE_TRANSFER = "execute_transfer", "Schedule and carry out stock transfer"
        OPEN_SUPPLIER = "open_supplier", "Open new suppliers (KYC)"
        APPROVE_SUPPLIER = "approve_supplier", "Approve new suppliers (KYC)"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="unit_action_grants")
    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="user_action_grants")
    action = models.CharField(max_length=32, choices=Action.choices)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["business_unit__code", "action"]
        constraints = [
            models.UniqueConstraint(fields=["user", "business_unit", "action"], name="unique_user_unit_action_grant"),
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True) | Q(ends_at__gt=models.F("starts_at")),
                name="grant_end_after_start",
            ),
        ]


class DemoUserMarker(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="demo_marker")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="demo_user_marker_only"),
        ]


class StockExample(models.Model):
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="stock_examples")
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, related_name="stock_examples")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="stock_examples")
    responsible_business_unit = models.ForeignKey(
        BusinessUnit, on_delete=models.PROTECT, related_name="stock_examples"
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["item__item_code", "variant__sku", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gte=0), name="stock_example_nonnegative"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="stock_example_demo_only"),
            models.UniqueConstraint(
                fields=["variant", "location", "responsible_business_unit"],
                name="unique_stock_example_bucket",
            ),
        ]

    def clean(self):
        super().clean()
        if self.variant_id and self.item_id and self.variant.item_id != self.item_id:
            raise ValidationError({"variant": "The selected variant must belong to this item."})
        if self.location_id and self.responsible_business_unit_id:
            if self.location.legal_entity_id != self.responsible_business_unit.legal_entity_id:
                raise ValidationError({"location": "Stock location and responsible unit must share a company."})
            if not self.location.business_units.filter(pk=self.responsible_business_unit_id).exists():
                raise ValidationError({"location": "The responsible unit must have access to the stock location."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.variant.sku} ({self.quantity})"


class IdempotencyRecord(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETE = "complete", "Complete"

    key = models.CharField(max_length=128, unique=True)
    operation = models.CharField(max_length=32)
    payload_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PROCESSING)
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="idempotency_demo_only"),
            models.CheckConstraint(
                condition=(
                    Q(status="processing", completed_at__isnull=True)
                    | Q(status="complete", completed_at__isnull=False)
                ),
                name="idempotency_status_timestamp",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk, status=self.Status.COMPLETE).exists():
            raise ValidationError("Completed idempotency records are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk, status=self.Status.COMPLETE).exists():
            raise ValidationError("Completed idempotency records are immutable.")
        return super().delete(*args, **kwargs)


class LedgerAccount(models.Model):
    class AccountClass(models.TextChoices):
        ASSET = "asset", "Asset"
        LIABILITY = "liability", "Liability"
        REVENUE = "revenue", "Revenue"
        EXPENSE = "expense", "Expense"

    legal_entity = models.ForeignKey(LegalEntity, on_delete=models.PROTECT, related_name="ledger_accounts")
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=120)
    account_class = models.CharField(max_length=16, choices=AccountClass.choices)
    currency = models.CharField(max_length=3, default="QAR")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["legal_entity__code", "code"]
        constraints = [
            models.UniqueConstraint(fields=["legal_entity", "code"], name="unique_ledger_account_code"),
            models.CheckConstraint(condition=Q(currency="QAR"), name="ledger_account_qar_only"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="ledger_account_demo_only"),
        ]


class StockBalance(models.Model):
    legal_entity = models.ForeignKey(LegalEntity, on_delete=models.PROTECT, related_name="stock_balances")
    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="stock_balances")
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, related_name="stock_balances")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="stock_balances")
    on_hand_quantity = models.DecimalField(max_digits=14, decimal_places=2, default="0.00")
    reserved_quantity = models.DecimalField(max_digits=14, decimal_places=2, default="0.00")
    on_hand_value = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    currency = models.CharField(max_length=3, default="QAR")
    is_synthetic = models.BooleanField(default=True, editable=False)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["business_unit__code", "location__code", "variant__sku"]
        constraints = [
            models.UniqueConstraint(
                fields=["legal_entity", "business_unit", "variant", "location"],
                name="unique_stock_balance_bucket",
            ),
            models.CheckConstraint(condition=Q(on_hand_quantity__gte=0), name="stock_balance_onhand_nonnegative"),
            models.CheckConstraint(condition=Q(reserved_quantity__gte=0), name="stock_balance_reserved_nonnegative"),
            models.CheckConstraint(
                condition=Q(reserved_quantity__lte=F("on_hand_quantity")),
                name="stock_balance_reserved_lte_onhand",
            ),
            models.CheckConstraint(condition=Q(on_hand_value__gte=0), name="stock_balance_value_nonnegative"),
            models.CheckConstraint(condition=Q(currency="QAR"), name="stock_balance_qar_only"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="stock_balance_demo_only"),
        ]

    @property
    def available_quantity(self):
        return self.on_hand_quantity - self.reserved_quantity


class StockTransfer(models.Model):
    idempotency = models.OneToOneField(
        IdempotencyRecord, on_delete=models.PROTECT, related_name="stock_transfer"
    )
    source_balance = models.ForeignKey(
        StockBalance, on_delete=models.PROTECT, related_name="outgoing_transfers"
    )
    destination_balance = models.ForeignKey(
        StockBalance, on_delete=models.PROTECT, related_name="incoming_transfers"
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    transferred_value = models.DecimalField(max_digits=16, decimal_places=2)
    currency = models.CharField(max_length=3, default="QAR")
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=~Q(source_balance=F("destination_balance")), name="transfer_distinct_buckets"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="transfer_quantity_positive"),
            models.CheckConstraint(condition=Q(transferred_value__gte=0), name="transfer_value_nonnegative"),
            models.CheckConstraint(condition=Q(currency="QAR"), name="transfer_qar_only"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="transfer_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Posted stock transfers are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Posted stock transfers are immutable.")


class StockMovement(models.Model):
    class Kind(models.TextChoices):
        RECEIPT = "receipt", "Receipt"
        LANDED_COST = "landed_cost", "Landed cost"
        TRANSFER_IN = "transfer_in", "Transfer in"
        TRANSFER_OUT = "transfer_out", "Transfer out"
        DELIVERY = "delivery", "Delivery"

    balance = models.ForeignKey(StockBalance, on_delete=models.PROTECT, related_name="movements")
    idempotency = models.ForeignKey(IdempotencyRecord, on_delete=models.PROTECT, related_name="stock_movements")
    transfer = models.ForeignKey(
        StockTransfer, null=True, blank=True, on_delete=models.PROTECT, related_name="movements"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    quantity_delta = models.DecimalField(max_digits=14, decimal_places=2)
    value_delta = models.DecimalField(max_digits=16, decimal_places=2)
    currency = models.CharField(max_length=3, default="QAR")
    memo = models.CharField(max_length=240, default="TEST ONLY — not Tivoli-approved policy")
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(kind="receipt", quantity_delta__gt=0, value_delta__gte=0, transfer__isnull=True)
                    | Q(kind="landed_cost", quantity_delta=0, value_delta__gt=0, transfer__isnull=True)
                    | Q(kind="transfer_in", quantity_delta__gt=0, value_delta__gte=0, transfer__isnull=False)
                    | Q(kind="transfer_out", quantity_delta__lt=0, value_delta__lte=0, transfer__isnull=False)
                    | Q(kind="delivery", quantity_delta__lt=0, value_delta__lte=0, transfer__isnull=True)
                ),
                name="stock_movement_kind_signs",
            ),
            models.CheckConstraint(
                condition=~(Q(quantity_delta=0) & Q(value_delta=0)),
                name="stock_movement_nonzero",
            ),
            models.CheckConstraint(condition=Q(currency="QAR"), name="stock_movement_qar_only"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="stock_movement_demo_only"),
        ]
        indexes = [models.Index(fields=["balance", "created_at"], name="stock_move_bucket_time_idx")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Stock movements are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Stock movements are append-only.")


class StockReservation(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RELEASED = "released", "Released"
        CONSUMED = "consumed", "Consumed"

    idempotency = models.OneToOneField(
        IdempotencyRecord, on_delete=models.PROTECT, related_name="stock_reservation"
    )
    balance = models.ForeignKey(StockBalance, on_delete=models.PROTECT, related_name="reservations")
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="reservation_quantity_positive"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="reservation_demo_only"),
        ]
        indexes = [models.Index(fields=["balance", "status"], name="reservation_bucket_status_idx")]


class StockTransferRequest(models.Model):
    """Owner decision I1: showroom request, manager approval, warehouse scheduling and move."""

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Waiting for showroom manager"
        REJECTED = "rejected", "Rejected"
        APPROVED = "approved", "Waiting for warehouse"
        SCHEDULED = "scheduled", "Scheduled"
        IN_TRANSIT = "in_transit", "In transit"
        RECEIVED = "received", "Received"

    idempotency = models.OneToOneField(
        IdempotencyRecord, on_delete=models.PROTECT, related_name="stock_transfer_request"
    )
    business_unit = models.ForeignKey(
        BusinessUnit, on_delete=models.PROTECT, related_name="stock_transfer_requests"
    )
    source_location = models.ForeignKey(
        Location, on_delete=models.PROTECT, related_name="outgoing_transfer_requests"
    )
    destination_location = models.ForeignKey(
        Location, on_delete=models.PROTECT, related_name="incoming_transfer_requests"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SUBMITTED)
    note = models.CharField(max_length=240, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_transfer_requests"
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="stock_transfer_decisions",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=240, blank=True)
    scheduled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="stock_transfers_scheduled",
    )
    scheduled_at = models.DateTimeField(null=True, blank=True)
    transfer_date = models.DateField(null=True, blank=True)
    dispatched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="stock_transfers_dispatched",
    )
    dispatched_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="stock_transfers_received",
    )
    received_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(source_location=F("destination_location")),
                name="transfer_request_distinct_locations",
            ),
            models.CheckConstraint(
                condition=~Q(status="rejected") | ~Q(rejection_reason=""),
                name="transfer_request_rejection_reason",
            ),
            models.CheckConstraint(
                condition=~Q(status__in=["scheduled", "in_transit", "received"]) | Q(transfer_date__isnull=False),
                name="transfer_request_scheduled_date",
            ),
            models.CheckConstraint(
                condition=~Q(decided_by=F("requested_by")),
                name="transfer_request_decider_not_requester",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="transfer_request_demo_only"),
        ]
        indexes = [models.Index(fields=["business_unit", "status"], name="transfer_request_unit_idx")]


class StockTransferRequestLine(models.Model):
    request = models.ForeignKey(StockTransferRequest, on_delete=models.PROTECT, related_name="lines")
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, related_name="transfer_request_lines")
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    # Set at dispatch: keeps the goods off sale at the source while in transit.
    reservation = models.OneToOneField(
        StockReservation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="transfer_request_line",
    )
    # Set at receipt: the posted ledger transfer.
    transfer = models.OneToOneField(
        StockTransfer,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="transfer_request_line",
    )
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(fields=["request", "variant"], name="transfer_request_line_unique_variant"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="transfer_request_line_qty_positive"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="transfer_request_line_demo_only"),
        ]


class StockTransferRequestEvent(models.Model):
    request = models.ForeignKey(StockTransferRequest, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_transfer_request_events"
    )
    from_status = models.CharField(max_length=16, blank=True)
    to_status = models.CharField(max_length=16, choices=StockTransferRequest.Status.choices)
    note = models.CharField(max_length=240, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["occurred_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="transfer_request_event_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Transfer request events are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Transfer request events are append-only.")


class TransferPermissionAudit(models.Model):
    """Append-only record of who granted or revoked a stock transfer permission."""

    class Change(models.TextChoices):
        GRANTED = "granted", "Granted"
        REVOKED = "revoked", "Revoked"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="transfer_permission_changes_made"
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="transfer_permission_changes"
    )
    business_unit = models.ForeignKey(
        BusinessUnit, on_delete=models.PROTECT, related_name="transfer_permission_changes"
    )
    action = models.CharField(max_length=32, choices=UserActionGrant.Action.choices)
    change = models.CharField(max_length=8, choices=Change.choices)
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(action__in=[
                    "request_transfer", "approve_transfer", "execute_transfer", "open_supplier", "approve_supplier",
                ]),
                name="transfer_permission_audit_action",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="transfer_permission_audit_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Transfer permission audit events are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Transfer permission audit events are append-only.")


class JournalEntry(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"

    class Kind(models.TextChoices):
        RECEIPT = "receipt", "Synthetic receipt"
        DELIVERY = "delivery", "Synthetic delivery"
        PAYMENT = "payment", "Synthetic customer payment"

    idempotency = models.OneToOneField(
        IdempotencyRecord, on_delete=models.PROTECT, related_name="journal_entry"
    )
    legal_entity = models.ForeignKey(LegalEntity, on_delete=models.PROTECT, related_name="journal_entries")
    business_unit = models.ForeignKey(
        BusinessUnit,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="report_journal_entries",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    currency = models.CharField(max_length=3, default="QAR")
    policy_label = models.CharField(max_length=64, default="TEST_ONLY_NOT_TIVOLI_APPROVED")
    description = models.CharField(max_length=240)
    created_at = models.DateTimeField(default=timezone.now)
    posted_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(currency="QAR"), name="journal_qar_only"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="journal_demo_only"),
            models.CheckConstraint(
                condition=(
                    Q(status="draft", posted_at__isnull=True)
                    | Q(status="posted", posted_at__isnull=False)
                ),
                name="journal_status_timestamp",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            current = type(self).objects.get(pk=self.pk)
            if current.status == self.Status.POSTED:
                raise ValidationError("Posted journal entries are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk, status=self.Status.POSTED).exists():
            raise ValidationError("Posted journal entries are immutable.")
        return super().delete(*args, **kwargs)


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="lines")
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name="journal_lines")
    debit = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    credit = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    memo = models.CharField(max_length=240)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(debit__gt=0, credit=0)
                    | Q(credit__gt=0, debit=0)
                ),
                name="journal_line_single_positive_side",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="journal_line_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.entry_id and JournalEntry.objects.filter(
            pk=self.entry_id, status=JournalEntry.Status.POSTED
        ).exists():
            raise ValidationError("Lines in posted journal entries are immutable.")
        if self.pk:
            raise ValidationError("Journal lines are immutable once created.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.entry_id and JournalEntry.objects.filter(
            pk=self.entry_id, status=JournalEntry.Status.POSTED
        ).exists():
            raise ValidationError("Lines in posted journal entries are immutable.")
        return super().delete(*args, **kwargs)


class FinanceDraftAccount(models.Model):
    """Isolated synthetic account proposal; never an operational ledger account."""

    legal_entity = models.ForeignKey(
        LegalEntity, on_delete=models.PROTECT, related_name="finance_draft_accounts"
    )
    code = models.CharField(max_length=40)
    name_en = models.CharField(max_length=160)
    name_ar = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        default_permissions = ()
        ordering = ["legal_entity_id", "code", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["legal_entity", "code"], name="finance_draft_account_code_uq"),
            models.CheckConstraint(condition=Q(code__startswith="TEST-DRAFT-ACCT-"), name="finance_draft_account_code_demo"),
            models.CheckConstraint(condition=Q(name_en__gt=""), name="finance_draft_account_name_en_required"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="finance_draft_account_demo_only"),
        ]


class FinanceDraftDimension(models.Model):
    """User-proposed dimension label, not an activated accounting mapping."""

    legal_entity = models.ForeignKey(
        LegalEntity, on_delete=models.PROTECT, related_name="finance_draft_dimensions"
    )
    code = models.CharField(max_length=40)
    label_en = models.CharField(max_length=160)
    label_ar = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        default_permissions = ()
        ordering = ["legal_entity_id", "code", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["legal_entity", "code"], name="finance_draft_dimension_code_uq"),
            models.CheckConstraint(condition=Q(code__startswith="TEST-DRAFT-DIM-"), name="finance_draft_dimension_code_demo"),
            models.CheckConstraint(condition=Q(label_en__gt=""), name="finance_draft_dimension_label_en_required"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="finance_draft_dimension_demo_only"),
        ]


class FinanceDraftDimensionValue(models.Model):
    dimension = models.ForeignKey(
        FinanceDraftDimension, on_delete=models.PROTECT, related_name="values"
    )
    code = models.CharField(max_length=40)
    label_en = models.CharField(max_length=160)
    label_ar = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        default_permissions = ()
        ordering = ["dimension_id", "code", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["dimension", "code"], name="finance_draft_dimension_value_code_uq"),
            models.CheckConstraint(condition=Q(code__startswith="TEST-DRAFT-VAL-"), name="finance_draft_dimension_value_code_demo"),
            models.CheckConstraint(condition=Q(label_en__gt=""), name="finance_draft_dimension_value_label_en_required"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="finance_draft_dimension_value_demo_only"),
        ]


class FinanceDraftJournal(models.Model):
    """Non-posting journal draft. It has no link to JournalEntry or posting service."""

    class State(models.TextChoices):
        DRAFT = "draft", "Draft"

    legal_entity = models.ForeignKey(
        LegalEntity, on_delete=models.PROTECT, related_name="finance_draft_journals"
    )
    title_en = models.CharField(max_length=200)
    title_ar = models.CharField(max_length=200, blank=True)
    currency = models.CharField(max_length=3, default="QAR")
    state = models.CharField(max_length=12, choices=State.choices, default=State.DRAFT)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    revision = models.PositiveIntegerField(default=0)
    kind = models.CharField(max_length=24, choices=[("journal", "Journal draft"), ("recurring_template", "Recurring template proposal"), ("opening_balance", "Opening-balance worksheet proposal"), ("accrual_adjustment", "Accrual adjustment proposal"), ("prepayment_adjustment", "Prepayment adjustment proposal")], default="journal")
    adjustment_proposal = models.JSONField(default=dict, blank=True)
    as_of_date = models.DateField(null=True, blank=True)
    recurrence_proposal = models.JSONField(default=dict, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        default_permissions = ()
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(currency="QAR"), name="finance_draft_journal_qar_test_only"),
            models.CheckConstraint(condition=Q(state="draft"), name="finance_draft_journal_never_posts"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="finance_draft_journal_demo_only"),
            models.CheckConstraint(condition=Q(title_en__gt=""), name="finance_draft_journal_title_en_required"),
            models.CheckConstraint(
                condition=(Q(kind__in=["journal", "opening_balance", "accrual_adjustment", "prepayment_adjustment"], recurrence_proposal={}) | (Q(kind="recurring_template") & ~Q(recurrence_proposal={}))),
                name="finance_draft_proposal_kind",
            ),
            models.CheckConstraint(
                condition=(Q(kind="opening_balance", as_of_date__isnull=False) | (~Q(kind="opening_balance") & Q(as_of_date__isnull=True))),
                name="finance_draft_opening_date",
            ),
            models.CheckConstraint(
                condition=((Q(kind__in=["accrual_adjustment", "prepayment_adjustment"]) & ~Q(adjustment_proposal={}))
                           | (~Q(kind__in=["accrual_adjustment", "prepayment_adjustment"]) & Q(adjustment_proposal={}))),
                name="finance_draft_adjustment_metadata",
            ),
        ]


class FinanceDraftJournalLine(models.Model):
    journal = models.ForeignKey(
        FinanceDraftJournal, on_delete=models.CASCADE, related_name="lines"
    )
    account = models.ForeignKey(
        FinanceDraftAccount, on_delete=models.PROTECT, related_name="journal_lines"
    )
    business_unit = models.ForeignKey(
        BusinessUnit, null=True, blank=True, on_delete=models.PROTECT,
        related_name="finance_draft_journal_lines",
    )
    dimensions = models.ManyToManyField(
        FinanceDraftDimensionValue, through="FinanceDraftJournalLineDimension",
        related_name="journal_lines",
    )
    debit = models.DecimalField(max_digits=14, decimal_places=2, default="0.00")
    credit = models.DecimalField(max_digits=14, decimal_places=2, default="0.00")
    memo_en = models.CharField(max_length=240, blank=True)
    memo_ar = models.CharField(max_length=240, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        default_permissions = ()
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(debit__gte=0), name="finance_draft_line_debit_nonnegative"),
            models.CheckConstraint(condition=Q(credit__gte=0), name="finance_draft_line_credit_nonnegative"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="finance_draft_line_demo_only"),
        ]


class FinanceDraftJournalLineDimension(models.Model):
    line = models.ForeignKey(FinanceDraftJournalLine, on_delete=models.CASCADE)
    value = models.ForeignKey(FinanceDraftDimensionValue, on_delete=models.PROTECT)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["line", "value"], name="finance_draft_line_dimension_uq"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="finance_draft_line_dimension_demo_only"),
        ]


class FinanceDraftJournalRevision(models.Model):
    journal = models.ForeignKey(FinanceDraftJournal, on_delete=models.PROTECT, related_name="revisions")
    revision = models.PositiveIntegerField()
    snapshot = models.JSONField()
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        default_permissions = ()
        ordering = ["revision"]
        constraints = [
            models.UniqueConstraint(fields=["journal", "revision"], name="finance_draft_revision_uq"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="finance_draft_revision_demo_only"),
        ]


class BusinessUnitApprovalRole(models.TextChoices):
    SALESPERSON = "salesperson", "Salesperson"
    MANAGER = "manager", "Manager"
    COMMERCIAL_GM = "commercial_gm", "Commercial GM"
    BU_GM = "bu_gm", "Business-unit GM"
    CEO = "ceo", "CEO"


class BusinessUnitRoleAssignment(models.Model):
    class Role(models.TextChoices):
        SALESPERSON = BusinessUnitApprovalRole.SALESPERSON, "Salesperson"
        MANAGER = BusinessUnitApprovalRole.MANAGER, "Manager"
        COMMERCIAL_GM = BusinessUnitApprovalRole.COMMERCIAL_GM, "Commercial GM"
        BU_GM = BusinessUnitApprovalRole.BU_GM, "Business-unit GM"
        CEO = BusinessUnitApprovalRole.CEO, "CEO"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="unit_workflow_roles")
    business_unit = models.ForeignKey(
        BusinessUnit, on_delete=models.PROTECT, related_name="workflow_role_assignments"
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="workflow_role_assignments_made",
    )
    created_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["business_unit__code", "role", "user__username"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "business_unit", "role"],
                condition=Q(revoked_at__isnull=True),
                name="active_unit_role_unique",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="unit_role_demo_only"),
        ]


class BusinessUnitWorkCalendar(models.Model):
    business_unit = models.OneToOneField(
        BusinessUnit, on_delete=models.PROTECT, related_name="workflow_calendar"
    )
    closed_weekdays = models.JSONField(default=list)
    closed_dates = models.JSONField(default=list)
    is_configured = models.BooleanField(default=False)
    updated_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="work_calendar_demo_only"),
        ]


class WorkflowPolicyConfig(models.Model):
    business_unit = models.OneToOneField(
        BusinessUnit, on_delete=models.PROTECT, related_name="workflow_policy_config"
    )
    storage_exception_approver_roles = models.JSONField(default=list)
    updated_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="workflow_policy_demo_only"),
        ]


class ApprovedSellingPrice(models.Model):
    business_unit = models.ForeignKey(
        BusinessUnit, on_delete=models.PROTECT, related_name="approved_selling_prices"
    )
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, related_name="approved_selling_prices")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="QAR")
    revision = models.PositiveIntegerField()
    is_current = models.BooleanField(default=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="selling_prices_approved"
    )
    approved_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["business_unit__code", "variant__sku", "-revision"]
        constraints = [
            models.UniqueConstraint(
                fields=["business_unit", "variant", "revision"],
                name="selling_price_revision_unique",
            ),
            models.UniqueConstraint(
                fields=["business_unit", "variant"],
                condition=Q(is_current=True),
                name="selling_price_current_unique",
            ),
            models.CheckConstraint(condition=Q(amount__gt=0), name="selling_price_positive"),
            models.CheckConstraint(condition=Q(currency="QAR"), name="selling_price_qar_only"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="selling_price_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.get(pk=self.pk)
            immutable_fields = (
                "business_unit_id",
                "variant_id",
                "amount",
                "currency",
                "revision",
                "approved_by_id",
                "approved_at",
            )
            if any(getattr(original, field) != getattr(self, field) for field in immutable_fields):
                raise ValidationError("Approved selling-price snapshots are immutable.")
            if not original.is_current and self.is_current:
                raise ValidationError("A superseded selling-price revision cannot be reactivated.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Approved selling-price snapshots cannot be deleted.")


class SyntheticCounterparty(models.Model):
    class Type(models.TextChoices):
        SUPPLIER = "supplier", "Supplier"
        CUSTOMER = "customer", "Customer"

    party_type = models.CharField(max_length=12, choices=Type.choices)
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    name_ar = models.CharField(max_length=120, blank=True)
    contact_en = models.CharField(max_length=500, blank=True)
    contact_ar = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["party_type", "code"]
        constraints = [
            models.UniqueConstraint(fields=["party_type", "code"], name="synthetic_party_code_unique"),
            models.CheckConstraint(
                condition=(
                    Q(party_type="supplier", code__regex=r"^TEST-SUP-[A-Z0-9-]+$")
                    | Q(party_type="customer", code__regex=r"^TEST-CUST-[A-Z0-9-]+$")
                ),
                name="synthetic_party_code_prefix",
            ),
            models.CheckConstraint(
                condition=Q(name__startswith="TEST ONLY"),
                name="synthetic_party_name_prefix",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="counterparty_demo_only"),
        ]


class SpecialOrderValueBand(models.Model):
    business_unit = models.ForeignKey(
        BusinessUnit, on_delete=models.PROTECT, related_name="special_order_value_bands"
    )
    minimum_value = models.DecimalField(max_digits=14, decimal_places=2)
    maximum_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="special_order_bands_created"
    )
    created_at = models.DateTimeField(default=timezone.now)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="special_order_bands_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_reason = models.CharField(max_length=240, blank=True)
    is_active = models.BooleanField(default=False)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["business_unit__code", "minimum_value"]
        constraints = [
            models.CheckConstraint(condition=Q(minimum_value__gte=0), name="special_band_min_nonnegative"),
            models.CheckConstraint(
                condition=Q(maximum_value__isnull=True) | Q(maximum_value__gt=F("minimum_value")),
                name="special_band_max_above_min",
            ),
            models.CheckConstraint(
                condition=(
                    Q(is_active=False, approved_by__isnull=True, approved_at__isnull=True)
                    | Q(is_active=True, approved_by__isnull=False, approved_at__isnull=False)
                ),
                name="special_band_approval_state",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="special_band_demo_only"),
        ]


class SalesQuote(models.Model):
    class Kind(models.TextChoices):
        STOCK = "stock", "In-stock sale"
        SPECIAL_ORDER = "special_order", "Special order"

    class Status(models.TextChoices):
        PENDING_APPROVAL = "pending_approval", "Pending approval"
        APPROVED = "approved", "Approved"
        HELD = "held", "Temporarily held"
        CONFIRMED = "confirmed", "Confirmed"
        READY = "ready", "Ready for handover"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    idempotency = models.OneToOneField(
        IdempotencyRecord, on_delete=models.PROTECT, related_name="sales_quote"
    )
    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="sales_quotes")
    customer = models.ForeignKey(SyntheticCounterparty, on_delete=models.PROTECT, related_name="sales_quotes")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    status = models.CharField(max_length=20, choices=Status.choices)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales_quotes_created"
    )
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default="0.00")
    total_list_value = models.DecimalField(max_digits=16, decimal_places=2)
    total_discount = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    total_quote_value = models.DecimalField(max_digits=16, decimal_places=2)
    payment_plan = models.CharField(max_length=16, default="full")
    downpayment_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="QAR")
    presentation_document = models.JSONField(default=dict, editable=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    ready_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(discount_percent__gte=0, discount_percent__lte=50), name="quote_discount_range"),
            models.CheckConstraint(condition=Q(total_list_value__gt=0), name="quote_list_total_positive"),
            models.CheckConstraint(condition=Q(total_quote_value__gt=0), name="quote_net_total_positive"),
            models.CheckConstraint(condition=Q(currency="QAR"), name="quote_qar_only"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="sales_quote_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.get(pk=self.pk)
            immutable_fields = (
                "idempotency_id",
                "business_unit_id",
                "customer_id",
                "kind",
                "created_by_id",
                "discount_percent",
                "total_list_value",
                "total_discount",
                "total_quote_value",
                "payment_plan",
                "downpayment_percent",
                "currency",
                "presentation_document",
                "created_at",
            )
            if any(getattr(original, field) != getattr(self, field) for field in immutable_fields):
                raise ValidationError("Sales quote commercial terms and price snapshot are immutable.")
        return super().save(*args, **kwargs)


class SalesQuoteLine(models.Model):
    quote = models.ForeignKey(SalesQuote, on_delete=models.PROTECT, related_name="lines")
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, related_name="sales_quote_lines")
    location = models.ForeignKey(
        Location, null=True, blank=True, on_delete=models.PROTECT, related_name="sales_quote_lines"
    )
    approved_price = models.ForeignKey(ApprovedSellingPrice, on_delete=models.PROTECT, related_name="quote_lines")
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    specifications = models.JSONField(default=dict)
    selling_price_snapshot = models.DecimalField(max_digits=14, decimal_places=2)
    price_revision_snapshot = models.PositiveIntegerField()
    net_unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default="0.00")
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default="0.00")
    net_total = models.DecimalField(max_digits=16, decimal_places=2)
    received_quantity = models.DecimalField(max_digits=12, decimal_places=2, default="0.00")
    reserved_quantity = models.DecimalField(max_digits=12, decimal_places=2, default="0.00")
    delivered_quantity = models.DecimalField(max_digits=12, decimal_places=2, default="0.00")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="quote_line_quantity_positive"),
            models.CheckConstraint(condition=Q(selling_price_snapshot__gt=0), name="quote_line_price_positive"),
            models.CheckConstraint(condition=Q(net_unit_price__gte=0), name="quote_line_net_unit_nonnegative"),
            models.CheckConstraint(condition=Q(discount_percent__gte=0, discount_percent__lte=50), name="quote_line_discount_range"),
            models.CheckConstraint(condition=Q(discount_amount__gte=0), name="quote_line_discount_nonnegative"),
            models.CheckConstraint(condition=Q(net_total__gte=0), name="quote_line_net_nonnegative"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="quote_line_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.get(pk=self.pk)
            immutable_fields = (
                "quote_id",
                "variant_id",
                "location_id",
                "approved_price_id",
                "quantity",
                "specifications",
                "selling_price_snapshot",
                "price_revision_snapshot",
                "net_unit_price",
                "discount_percent",
                "discount_amount",
                "net_total",
            )
            if any(getattr(original, field) != getattr(self, field) for field in immutable_fields):
                raise ValidationError("Quote line specifications and price snapshots are immutable.")
            if (
                self.received_quantity < original.received_quantity
                or self.reserved_quantity < original.reserved_quantity
                or self.delivered_quantity < original.delivered_quantity
                or self.received_quantity > self.quantity
                or self.reserved_quantity > self.quantity
                or self.delivered_quantity > self.reserved_quantity
            ):
                raise ValidationError("Quote-line workflow quantities must be monotonic and within the order.")
        return super().save(*args, **kwargs)


class QuoteApproval(models.Model):
    class Kind(models.TextChoices):
        DISCOUNT = "discount", "Discount"
        PAYMENT_TERMS = "payment_terms", "Payment terms"
        POST_HANDOVER = "post_handover", "Post-handover balance"

    quote = models.ForeignKey(SalesQuote, on_delete=models.PROTECT, related_name="approvals")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales_quote_approvals"
    )
    role = models.CharField(max_length=20, choices=BusinessUnitRoleAssignment.Role.choices)
    reason = models.CharField(max_length=240)
    details = models.JSONField(default=dict)
    approved_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["approved_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=~Q(reason=""), name="quote_approval_reason_required"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="quote_approval_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Quote approvals are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Quote approvals are append-only.")


class QuoteHold(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        RELEASED = "released", "Released"
        CONVERTED = "converted", "Converted to reservation"

    idempotency = models.ForeignKey(
        IdempotencyRecord, on_delete=models.PROTECT, related_name="quote_holds"
    )
    quote = models.ForeignKey(SalesQuote, on_delete=models.PROTECT, related_name="holds")
    quote_line = models.ForeignKey(SalesQuoteLine, on_delete=models.PROTECT, related_name="holds")
    balance = models.ForeignKey(
        StockBalance, null=True, blank=True, on_delete=models.PROTECT, related_name="quote_holds"
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    working_days = models.PositiveSmallIntegerField(default=1)
    expires_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="quote_holds_created"
    )
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["expires_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="quote_hold_qty_positive"),
            models.CheckConstraint(condition=Q(working_days__gte=1, working_days__lte=3), name="quote_hold_days_lte_three"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="quote_hold_demo_only"),
        ]


class QuoteReservation(models.Model):
    quote_line = models.ForeignKey(SalesQuoteLine, on_delete=models.PROTECT, related_name="reservations")
    stock_reservation = models.OneToOneField(
        StockReservation, on_delete=models.PROTECT, related_name="quote_reservation"
    )
    purchase_receipt_line = models.ForeignKey(
        "PurchaseReceiptLine",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="quote_reservations",
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="quote_reservation_qty_positive"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="quote_reservation_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Quote reservations are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Quote reservations are append-only.")


class QuotePayment(models.Model):
    class Kind(models.TextChoices):
        DEPOSIT = "deposit", "Non-returnable deposit"
        BALANCE = "balance", "Balance"
        STORAGE = "storage", "Storage charge"

    class Method(models.TextChoices):
        SIMULATED_CASH = "simulated_cash", "Simulated cash"
        SIMULATED_CARD = "simulated_card", "Simulated card"
        SIMULATED_BANK = "simulated_bank", "Simulated bank transfer"

    quote = models.ForeignKey(SalesQuote, on_delete=models.PROTECT, related_name="payments")
    idempotency = models.OneToOneField(
        IdempotencyRecord, on_delete=models.PROTECT, related_name="quote_payment"
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    non_returnable_amount = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    method = models.CharField(max_length=20, choices=Method.choices)
    simulated_reference = models.CharField(max_length=80)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="simulated_payments_recorded"
    )
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="quote_payment_positive"),
            models.CheckConstraint(
                condition=Q(non_returnable_amount__gte=0)
                & Q(non_returnable_amount__lte=F("amount")),
                name="payment_nonreturnable_lte_amount",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="quote_payment_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Simulated payment records are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Simulated payment records are append-only.")

class Currency(models.Model):
    """Currencies a purchase may be priced in (owner decision P1). QAR is the company currency."""

    code = models.CharField(max_length=3, primary_key=True)
    name = models.CharField(max_length=60)
    is_base = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-is_base", "code"]
        constraints = [
            models.UniqueConstraint(fields=["is_base"], condition=Q(is_base=True), name="single_base_currency"),
            models.CheckConstraint(condition=Q(code__regex=r"^[A-Z]{3}$"), name="currency_code_iso_format"),
        ]

    def __str__(self):
        return self.code


BASE_CURRENCY = "QAR"
# Owner decision P1 (21 Oct 2026): QAR plus USD, EUR and GBP on purchases.
SUPPORTED_CURRENCIES = [
    ("QAR", "Qatari riyal", True),
    ("USD", "US dollar", False),
    ("EUR", "Euro", False),
    ("GBP", "Pound sterling", False),
]


def ensure_currencies():
    for code, name, is_base in SUPPORTED_CURRENCIES:
        Currency.objects.get_or_create(code=code, defaults={"name": name, "is_base": is_base})


def _exchange_rate_constraints(prefix):
    """QAR documents carry rate 1; foreign ones need a positive rate, its source and its date."""
    return [
        models.CheckConstraint(
            condition=(
                Q(currency=BASE_CURRENCY, exchange_rate=1)
                | (~Q(currency=BASE_CURRENCY) & Q(exchange_rate__gt=0) & ~Q(rate_source="") & Q(rate_date__isnull=False))
            ),
            name=f"{prefix}_exchange_rate_recorded",
        ),
    ]


class PurchaseCommitment(models.Model):
    """Synthetic purchase commitment priced in its own currency; receipts are tracked by linked lines."""

    idempotency = models.OneToOneField(
        IdempotencyRecord, on_delete=models.PROTECT, related_name="purchase_commitment"
    )
    business_unit = models.ForeignKey(
        BusinessUnit, on_delete=models.PROTECT, related_name="purchase_commitments"
    )
    supplier = models.ForeignKey(
        SyntheticCounterparty, on_delete=models.PROTECT, related_name="purchase_commitments"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchase_commitments_created"
    )
    # Line unit costs are in this currency. The rate is QAR per one unit, as quoted when ordering.
    currency = models.CharField(max_length=3, default="QAR")
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=8, default=1)
    rate_source = models.CharField(max_length=120, blank=True)
    rate_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            *_exchange_rate_constraints("purchase_commitment"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="purchase_commitment_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Purchase commitments are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Purchase commitments are append-only.")


class PurchaseCommitmentLine(models.Model):
    commitment = models.ForeignKey(
        PurchaseCommitment, on_delete=models.PROTECT, related_name="lines"
    )
    variant = models.ForeignKey(
        Variant, on_delete=models.PROTECT, related_name="purchase_commitment_lines"
    )
    ordered_quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2)
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(ordered_quantity__gt=0), name="commitment_line_quantity_positive"
            ),
            models.CheckConstraint(condition=Q(unit_cost__gt=0), name="commitment_line_cost_positive"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="commitment_line_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Purchase commitment lines are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Purchase commitment lines are append-only.")


class PurchaseReceipt(models.Model):
    idempotency = models.OneToOneField(
        IdempotencyRecord, on_delete=models.PROTECT, related_name="purchase_receipt"
    )
    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="purchase_receipts")
    supplier = models.ForeignKey(
        SyntheticCounterparty, on_delete=models.PROTECT, related_name="purchase_receipts"
    )
    purchase_commitment = models.ForeignKey(
        PurchaseCommitment,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="receipts",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchase_receipts_created"
    )
    # Values below are in QAR; supplier prices in another currency are converted at exchange_rate.
    purchase_value = models.DecimalField(max_digits=16, decimal_places=2)
    eligible_landed_charges = models.DecimalField(max_digits=16, decimal_places=2)
    landed_cost_rate_percent = models.DecimalField(max_digits=9, decimal_places=4)
    inventory_value = models.DecimalField(max_digits=16, decimal_places=2)
    stock_movement_ids = models.JSONField(default=list)
    currency = models.CharField(max_length=3, default="QAR")
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=8, default=1)
    rate_source = models.CharField(max_length=120, blank=True)
    rate_date = models.DateField(null=True, blank=True)
    foreign_purchase_value = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(purchase_value__gt=0), name="purchase_value_positive"),
            models.CheckConstraint(condition=Q(eligible_landed_charges__gte=0), name="landed_charges_nonnegative"),
            *_exchange_rate_constraints("purchase_receipt"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="purchase_receipt_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Purchase receipts are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Purchase receipts are append-only.")


class PurchaseReceiptCharge(models.Model):
    class Category(models.TextChoices):
        SHIPPING = "shipping", "Shipping"
        CLEARANCE = "clearance", "Clearance"
        OTHER = "other", "Other eligible landed charge"

    receipt = models.ForeignKey(PurchaseReceipt, on_delete=models.PROTECT, related_name="charges")
    category = models.CharField(max_length=16, choices=Category.choices)
    # amount is in QAR. A charge billed in another currency keeps its own amount and rate
    # (owner decision P1: shipping may be EUR or USD, customs clearance is always QAR).
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    is_eligible = models.BooleanField(default=True)
    currency = models.CharField(max_length=3, default="QAR")
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=8, default=1)
    rate_source = models.CharField(max_length=120, blank=True)
    rate_date = models.DateField(null=True, blank=True)
    foreign_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(amount__gte=0), name="purchase_charge_nonnegative"),
            *_exchange_rate_constraints("purchase_charge"),
            models.CheckConstraint(
                condition=~Q(category="clearance") | Q(currency="QAR"),
                name="purchase_charge_clearance_qar",
            ),
            models.CheckConstraint(
                condition=(Q(currency="QAR", foreign_amount__isnull=True) | (~Q(currency="QAR") & Q(foreign_amount__isnull=False, foreign_amount__gte=0))),
                name="purchase_charge_foreign_amount_recorded",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="purchase_charge_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Purchase receipt charges are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Purchase receipt charges are append-only.")


class PurchaseReceiptLine(models.Model):
    receipt = models.ForeignKey(PurchaseReceipt, on_delete=models.PROTECT, related_name="lines")
    commitment_line = models.ForeignKey(
        PurchaseCommitmentLine,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="receipt_lines",
    )
    quote_line = models.ForeignKey(
        SalesQuoteLine, null=True, blank=True, on_delete=models.PROTECT, related_name="purchase_receipt_lines"
    )
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, related_name="purchase_receipt_lines")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="purchase_receipt_lines")
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    # QAR, after conversion; the supplier's own price is kept in the foreign_* fields.
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2)
    purchase_value = models.DecimalField(max_digits=16, decimal_places=2)
    foreign_unit_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    foreign_purchase_value = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    landed_cost_allocation = models.DecimalField(max_digits=14, decimal_places=2, default="0.00")
    stock_movement_ids = models.JSONField(default=list)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="receipt_line_qty_positive"),
            models.CheckConstraint(condition=Q(unit_cost__gte=0), name="receipt_line_cost_nonnegative"),
            models.CheckConstraint(condition=Q(purchase_value__gte=0), name="receipt_line_value_nonnegative"),
            models.CheckConstraint(condition=Q(landed_cost_allocation__gte=0), name="receipt_line_landed_nonnegative"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="receipt_line_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Purchase receipt lines are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Purchase receipt lines are append-only.")


class WorkflowEvent(models.Model):
    class Kind(models.TextChoices):
        READINESS_NOTIFICATION = "readiness_notification", "Readiness notification"
        STORAGE_CHARGE_NOTIFICATION = "storage_charge_notification", "Storage charge notification"
        STORAGE_EXCEPTION = "storage_exception", "Storage exception"

    quote = models.ForeignKey(SalesQuote, on_delete=models.PROTECT, related_name="workflow_events")
    kind = models.CharField(max_length=32, choices=Kind.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="workflow_events"
    )
    message = models.CharField(max_length=240)
    details = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["occurred_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=~Q(message=""), name="workflow_event_message_required"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="workflow_event_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Workflow audit events are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Workflow audit events are append-only.")


class NotificationOutboxIntent(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending dry-run"
        RETRY = "retry", "Retry pending"
        DRY_RUN_RECORDED = "dry_run_recorded", "Dry-run intent recorded"
        DRY_RUN_FAILED = "dry_run_failed", "Dry-run attempts exhausted"

    workflow_event = models.OneToOneField(
        WorkflowEvent,
        on_delete=models.PROTECT,
        related_name="notification_outbox_intent",
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=["pending", "retry", "dry_run_recorded", "dry_run_failed"]),
                name="notification_outbox_status_allowed",
            ),
            models.CheckConstraint(
                condition=Q(attempt_count__lte=3),
                name="notification_outbox_attempts_max3",
            ),
            models.CheckConstraint(
                condition=Q(is_synthetic=True),
                name="notification_outbox_demo_only",
            ),
        ]


class StorageCharge(models.Model):
    quote = models.ForeignKey(SalesQuote, on_delete=models.PROTECT, related_name="storage_charges")
    idempotency = models.ForeignKey(
        IdempotencyRecord, on_delete=models.PROTECT, related_name="storage_charges"
    )
    period_start = models.DateField()
    period_end = models.DateField()
    base_quote_value = models.DecimalField(max_digits=16, decimal_places=2)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["period_start", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["quote", "period_start"], name="unique_quote_storage_period"),
            models.CheckConstraint(condition=Q(period_end__gt=F("period_start")), name="storage_period_valid"),
            models.CheckConstraint(condition=Q(base_quote_value__gt=0), name="storage_base_positive"),
            models.CheckConstraint(condition=Q(amount__gte=0), name="storage_amount_nonnegative"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="storage_charge_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Storage charges are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Storage charges are append-only.")


class StorageChargeException(models.Model):
    charge = models.OneToOneField(StorageCharge, on_delete=models.PROTECT, related_name="exception")
    amount_waived = models.DecimalField(max_digits=14, decimal_places=2)
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="storage_exceptions_approved"
    )
    approver_role = models.CharField(max_length=20, choices=BusinessUnitRoleAssignment.Role.choices)
    reason = models.CharField(max_length=240)
    approved_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(amount_waived__gt=0), name="storage_waiver_positive"),
            models.CheckConstraint(condition=~Q(reason=""), name="storage_waiver_reason_required"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="storage_waiver_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Approved storage exceptions are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Approved storage exceptions are append-only.")


class UserFavorite(models.Model):
    class Destination(models.TextChoices):
        DASHBOARD = "dashboard", "Overview"
        RETAIL = "retail", "Retail & Sales"
        PROCUREMENT = "procurement", "Procurement & Imports"
        INVENTORY = "inventory", "Inventory & Warehouses"
        DELIVERY = "delivery", "Delivery & After-Sales"
        FINANCE = "finance", "Finance & Accounting"
        HR = "hr", "HR & Payroll"
        GOVERNANCE = "governance", "Governance & Administration"
        REPORTS = "reports", "Reporting & Dashboards"
        PURCHASING = "purchasing", "Purchasing Workbench"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="favorite_destinations",
    )
    # Function favorites store a navigation key only, never report data or grants.
    destination = models.CharField(
        max_length=32, choices=[*Destination.choices, *FUNCTION_DESTINATIONS],
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["user", "destination"], name="unique_user_favorite_destination"),
        ]


class ReportingPanel(models.TextChoices):
    SALES = "sales", "Sales"
    BEST_SELLERS = "best_sellers", "Best sellers"
    COSTS_EXPENSES = "costs_expenses", "Costs and expenses"
    GROSS_MARGIN = "gross_margin", "Gross margin"
    BUDGETS_ACTUALS = "budgets_actuals", "Budgets versus actuals"
    INVENTORY = "inventory", "Inventory"
    PURCHASE_HISTORY = "purchase_history", "Purchase history"
    TRANSFER_HISTORY = "transfer_history", "Transfer history"


class ReportDataCapability(models.TextChoices):
    SALES = "sales", "Selling-price sales data"
    CATEGORY_SALES = "category_sales", "Sales category labels"
    SUPPLIER_COST = "supplier_cost", "Supplier purchase cost"
    LANDED_COST = "landed_cost", "Landed cost"
    PURCHASE_HISTORY = "purchase_history", "Purchase history"
    INVENTORY = "inventory", "Inventory balances and history"
    TRANSFER_HISTORY = "transfer_history", "Warehouse transfer history"
    EXPENSES = "expenses", "Posted expense-account data"
    GROSS_MARGIN = "gross_margin", "Synthetic gross margin"
    BUDGETS = "budgets", "Configured synthetic budgets"


class ReportingRole(models.Model):
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=100)
    can_manage_report_panels = models.BooleanField(default=False)
    demo_fixture_key = models.CharField(max_length=100, blank=True, default="", db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reporting_roles_created",
    )
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["name", "code"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="reporting_role_demo_only"),
            models.UniqueConstraint(
                fields=["demo_fixture_key"],
                condition=~Q(demo_fixture_key=""),
                name="report_role_fixture_key_unique",
            ),
        ]


class ReportingRoleMembership(models.Model):
    role = models.ForeignKey(ReportingRole, on_delete=models.PROTECT, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reporting_roles")
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reporting_role_memberships_assigned",
    )
    created_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    demo_fixture_key = models.CharField(max_length=100, blank=True, default="", db_index=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["role__code", "user__username"]
        constraints = [
            models.UniqueConstraint(
                fields=["role", "user"],
                condition=Q(revoked_at__isnull=True),
                name="active_reporting_role_member_unique",
            ),
            models.CheckConstraint(
                condition=Q(is_synthetic=True),
                name="reporting_role_member_demo_only",
            ),
            models.UniqueConstraint(
                fields=["demo_fixture_key"],
                condition=~Q(demo_fixture_key=""),
                name="report_membership_fixture_unique",
            ),
        ]


class ReportPanelGrant(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="report_panel_grants",
    )
    role = models.ForeignKey(
        ReportingRole,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="panel_grants",
    )
    panel = models.CharField(max_length=32, choices=ReportingPanel.choices)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="report_panel_grants_made",
    )
    created_at = models.DateTimeField(default=timezone.now)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    demo_fixture_key = models.CharField(max_length=100, blank=True, default="", db_index=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["panel", "user__username", "role__code"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(user__isnull=False, role__isnull=True) | Q(user__isnull=True, role__isnull=False)),
                name="report_panel_exactly_one_principal",
            ),
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True) | Q(ends_at__gt=models.F("starts_at")),
                name="report_panel_end_after_start",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="report_panel_demo_only"),
            models.UniqueConstraint(
                fields=["user", "panel"],
                condition=Q(user__isnull=False, role__isnull=True, revoked_at__isnull=True),
                name="active_report_panel_user_unique",
            ),
            models.UniqueConstraint(
                fields=["role", "panel"],
                condition=Q(user__isnull=True, role__isnull=False, revoked_at__isnull=True),
                name="active_report_panel_role_unique",
            ),
            models.UniqueConstraint(
                fields=["demo_fixture_key"],
                condition=~Q(demo_fixture_key=""),
                name="report_panel_fixture_unique",
            ),
        ]


class ReportDataGrant(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="report_data_grants",
    )
    role = models.ForeignKey(
        ReportingRole,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="data_grants",
    )
    capability = models.CharField(max_length=32, choices=ReportDataCapability.choices)
    business_unit = models.ForeignKey(
        BusinessUnit,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="report_data_grants",
    )
    legal_entity = models.ForeignKey(
        LegalEntity,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="report_data_grants",
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="report_data_grants_made",
    )
    created_at = models.DateTimeField(default=timezone.now)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    demo_fixture_key = models.CharField(max_length=100, blank=True, default="", db_index=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["capability", "business_unit__code", "user__username", "role__code"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(user__isnull=False, role__isnull=True) | Q(user__isnull=True, role__isnull=False)),
                name="report_data_exactly_one_principal",
            ),
            models.CheckConstraint(
                condition=(
                    Q(business_unit__isnull=False, legal_entity__isnull=True)
                    | Q(business_unit__isnull=True, legal_entity__isnull=False)
                ),
                name="report_data_exactly_one_scope",
            ),
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True) | Q(ends_at__gt=models.F("starts_at")),
                name="report_data_end_after_start",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="report_data_demo_only"),
            models.UniqueConstraint(
                fields=["user", "capability", "business_unit"],
                condition=Q(user__isnull=False, role__isnull=True, business_unit__isnull=False, revoked_at__isnull=True),
                name="active_report_data_user_unit_unique",
            ),
            models.UniqueConstraint(
                fields=["role", "capability", "business_unit"],
                condition=Q(user__isnull=True, role__isnull=False, business_unit__isnull=False, revoked_at__isnull=True),
                name="active_report_data_role_unit_unique",
            ),
            models.UniqueConstraint(
                fields=["user", "capability", "legal_entity"],
                condition=Q(user__isnull=False, role__isnull=True, legal_entity__isnull=False, revoked_at__isnull=True),
                name="active_report_data_user_company_unique",
            ),
            models.UniqueConstraint(
                fields=["role", "capability", "legal_entity"],
                condition=Q(user__isnull=True, role__isnull=False, legal_entity__isnull=False, revoked_at__isnull=True),
                name="active_report_data_role_company_unique",
            ),
            models.UniqueConstraint(
                fields=["demo_fixture_key"],
                condition=~Q(demo_fixture_key=""),
                name="report_data_fixture_unique",
            ),
        ]


class SalesReportLine(models.Model):
    delivery_operation = models.ForeignKey(
        IdempotencyRecord,
        on_delete=models.PROTECT,
        related_name="sales_report_lines",
    )
    quote_line = models.ForeignKey(
        SalesQuoteLine,
        on_delete=models.PROTECT,
        related_name="report_facts",
    )
    quote_reservation = models.OneToOneField(
        QuoteReservation,
        on_delete=models.PROTECT,
        related_name="sales_report_fact",
    )
    business_unit = models.ForeignKey(
        BusinessUnit,
        on_delete=models.PROTECT,
        related_name="sales_report_lines",
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="sales_report_lines",
    )
    item_code = models.CharField(max_length=40)
    description = models.CharField(max_length=240)
    brand_name = models.CharField(max_length=120)
    category_label = models.CharField(max_length=80, blank=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    selling_price = models.DecimalField(max_digits=14, decimal_places=2)
    revenue = models.DecimalField(max_digits=16, decimal_places=2)
    cost_of_goods_sold = models.DecimalField(max_digits=16, decimal_places=2)
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="sales_report_quantity_positive"),
            models.CheckConstraint(condition=Q(selling_price__gte=0), name="sales_report_price_nonnegative"),
            models.CheckConstraint(condition=Q(revenue__gte=0), name="sales_report_revenue_nonnegative"),
            models.CheckConstraint(condition=Q(cost_of_goods_sold__gte=0), name="sales_report_cogs_nonnegative"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="sales_report_line_demo_only"),
        ]
        indexes = [models.Index(fields=["business_unit", "created_at"], name="sales_report_unit_time_idx")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Delivered sales report facts are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Delivered sales report facts are append-only.")


class CustomerInvoiceSequence(models.Model):
    """Gap-free invoice numbering per business unit and calendar year."""

    business_unit = models.ForeignKey(
        BusinessUnit, on_delete=models.PROTECT, related_name="invoice_sequences"
    )
    year = models.PositiveIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business_unit", "year"], name="unique_invoice_sequence_year"),
        ]


class CustomerInvoice(models.Model):
    """Provisional customer invoice issued when a quote is delivered (F3, F4, F8 defaults)."""

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="customer_invoices")
    quote = models.OneToOneField(SalesQuote, on_delete=models.PROTECT, related_name="invoice")
    delivery_operation = models.OneToOneField(
        IdempotencyRecord, on_delete=models.PROTECT, related_name="customer_invoice"
    )
    customer = models.ForeignKey(SyntheticCounterparty, on_delete=models.PROTECT, related_name="customer_invoices")
    number = models.CharField(max_length=40, unique=True)
    issued_at = models.DateTimeField(default=timezone.now)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="customer_invoices_issued"
    )
    currency = models.CharField(max_length=3, default="QAR")
    list_total = models.DecimalField(max_digits=16, decimal_places=2)
    discount_total = models.DecimalField(max_digits=16, decimal_places=2)
    net_total = models.DecimalField(max_digits=16, decimal_places=2)
    tax_rate_percent = models.DecimalField(max_digits=5, decimal_places=2, default="0.00")
    tax_total = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    invoice_total = models.DecimalField(max_digits=16, decimal_places=2)
    paid_before_delivery = models.DecimalField(max_digits=16, decimal_places=2)
    balance_due = models.DecimalField(max_digits=16, decimal_places=2)
    policy_label = models.CharField(max_length=240)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-issued_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(currency="QAR"), name="customer_invoice_qar_only"),
            models.CheckConstraint(condition=Q(net_total__gt=0), name="customer_invoice_net_positive"),
            models.CheckConstraint(
                condition=Q(net_total=F("list_total") - F("discount_total")),
                name="customer_invoice_net_matches_discount",
            ),
            models.CheckConstraint(
                condition=Q(invoice_total=F("net_total") + F("tax_total")),
                name="customer_invoice_total_includes_tax",
            ),
            models.CheckConstraint(
                condition=Q(tax_rate_percent__gte=0, tax_total__gte=0), name="customer_invoice_tax_nonnegative"
            ),
            models.CheckConstraint(
                condition=Q(paid_before_delivery__gte=0, balance_due__gte=0)
                & Q(balance_due=F("invoice_total") - F("paid_before_delivery")),
                name="customer_invoice_balance_matches_payments",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="customer_invoice_demo_only"),
        ]
        indexes = [models.Index(fields=["business_unit", "issued_at"], name="customer_invoice_unit_time_idx")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Customer invoices are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Customer invoices are append-only.")


class CustomerInvoiceLine(models.Model):
    invoice = models.ForeignKey(CustomerInvoice, on_delete=models.PROTECT, related_name="lines")
    quote_line = models.OneToOneField(SalesQuoteLine, on_delete=models.PROTECT, related_name="invoice_line")
    item_code = models.CharField(max_length=40)
    description = models.CharField(max_length=240)
    brand_name = models.CharField(max_length=120)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    list_unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2)
    net_unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    line_total = models.DecimalField(max_digits=16, decimal_places=2)
    journal_entries = models.ManyToManyField(JournalEntry, related_name="customer_invoice_lines")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="customer_invoice_line_quantity_positive"),
            models.CheckConstraint(condition=Q(line_total__gte=0), name="customer_invoice_line_total_nonnegative"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="customer_invoice_line_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Customer invoice lines are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Customer invoice lines are append-only.")


class DemoReportSalesFact(models.Model):
    """Fictional report-only sales examples, separate from posted transactions."""

    demo_fixture_key = models.CharField(max_length=100, unique=True)
    business_unit = models.ForeignKey(
        BusinessUnit,
        on_delete=models.PROTECT,
        related_name="demo_report_sales_facts",
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="demo_report_sales_facts",
    )
    item = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
        related_name="demo_report_sales_facts",
    )
    category_label = models.CharField(max_length=80, blank=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    selling_price = models.DecimalField(max_digits=14, decimal_places=2)
    revenue = models.DecimalField(max_digits=16, decimal_places=2)
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="demo_report_sales_demo_only"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="demo_report_sales_qty_positive"),
            models.CheckConstraint(condition=Q(selling_price__gte=0), name="demo_report_sales_price_nonnegative"),
            models.CheckConstraint(condition=Q(revenue__gte=0), name="demo_report_sales_revenue_nonnegative"),
        ]
        indexes = [models.Index(fields=["business_unit", "created_at"], name="demo_sales_unit_time_idx")]

    @property
    def item_code(self):
        return self.item.item_code

    @property
    def description(self):
        return self.item.description

    @property
    def brand_name(self):
        return self.item.brand.name

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Demo report sales facts are append-only; reset the owned fixture to replace them.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Demo report sales facts may be reset only by the demo fixture command.")


class ReportBudget(models.Model):
    class Metric(models.TextChoices):
        SALES = "sales", "Sales"
        SUPPLIER_PURCHASE = "supplier_purchase", "Supplier purchase value"
        LANDED_COST = "landed_cost", "Landed charges"
        EXPENSES = "expenses", "Posted expense accounts"
        GROSS_MARGIN = "gross_margin", "Synthetic gross margin"

    class Granularity(models.TextChoices):
        DAILY = "daily", "Daily"
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Quarterly"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="report_budgets")
    location = models.ForeignKey(
        Location,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="report_budgets",
    )
    metric = models.CharField(max_length=24, choices=Metric.choices)
    granularity = models.CharField(max_length=12, choices=Granularity.choices)
    period_start = models.DateField()
    period_end = models.DateField()
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="report_budgets_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="report_budgets_updated",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    demo_fixture_key = models.CharField(max_length=100, blank=True, default="", db_index=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["business_unit__code", "period_start", "metric"]
        constraints = [
            models.CheckConstraint(condition=Q(period_end__gt=models.F("period_start")), name="report_budget_period_valid"),
            models.CheckConstraint(condition=Q(amount__gte=0), name="report_budget_nonnegative"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="report_budget_demo_only"),
            models.UniqueConstraint(
                fields=["business_unit", "metric", "granularity", "period_start", "period_end"],
                condition=Q(location__isnull=True),
                name="report_budget_unit_period_unique",
            ),
            models.UniqueConstraint(
                fields=["business_unit", "location", "metric", "granularity", "period_start", "period_end"],
                condition=Q(location__isnull=False),
                name="report_budget_location_period_unique",
            ),
            models.UniqueConstraint(
                fields=["demo_fixture_key"],
                condition=~Q(demo_fixture_key=""),
                name="report_budget_fixture_unique",
            ),
        ]


class ReportExportAudit(models.Model):
    class Format(models.TextChoices):
        EXCEL = "xlsx", "Excel workbook"
        WORD = "docx", "Word document"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="report_exports",
    )
    panel = models.CharField(max_length=32, choices=ReportingPanel.choices)
    file_format = models.CharField(max_length=8, choices=Format.choices)
    business_unit_ids = models.JSONField(default=list)
    legal_entity_id = models.PositiveIntegerField(null=True, blank=True)
    location_id = models.PositiveIntegerField(null=True, blank=True)
    filters = models.JSONField(default=dict)
    fields = models.JSONField(default=list)
    row_count = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="report_export_demo_only"),
            models.CheckConstraint(condition=Q(row_count__gte=0), name="report_export_row_count_nonnegative"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Report export audit records are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Report export audit records are append-only.")


class PersonalReportView(models.Model):
    """Presentation preferences only: never report results or authorization."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="personal_report_views"
    )
    name = models.CharField(max_length=100)
    panels = models.JSONField(default=list)
    filters = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["user", "name"], name="personal_report_view_user_name"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="personal_report_view_demo_only"),
        ]


def _kyc_upload_path(instance, filename):
    # Stored under a random name; the original name is kept on the row for display.
    suffix = Path(filename).suffix.lower()
    return f"supplier-kyc/{uuid.uuid4().hex}{suffix}"


class SupplierKyc(models.Model):
    """Know-your-supplier file that a new local supplier needs before it can be used."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Waiting for approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    supplier = models.OneToOneField(SyntheticCounterparty, on_delete=models.PROTECT, related_name="kyc")
    legal_name = models.CharField(max_length=160)
    address = models.CharField(max_length=300, blank=True)
    cr_number = models.CharField(max_length=40)
    cr_expiry = models.DateField(null=True, blank=True)
    vat_registered = models.BooleanField(default=False)
    vat_number = models.CharField(max_length=40, blank=True)
    signatory_name = models.CharField(max_length=120)
    signatory_title = models.CharField(max_length=120, blank=True)
    contact_email = models.CharField(max_length=160, blank=True)
    contact_phone = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="supplier_kyc_opened")
    opened_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="supplier_kyc_decided",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=500, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-opened_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["cr_number"], name="supplier_kyc_cr_unique"),
            models.CheckConstraint(
                condition=Q(vat_registered=False, vat_number="") | Q(vat_registered=True) & ~Q(vat_number=""),
                name="supplier_kyc_vat_number_when_registered",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["draft", "submitted"], decided_by__isnull=True, decided_at__isnull=True)
                | Q(status__in=["approved", "rejected"], decided_by__isnull=False, decided_at__isnull=False),
                name="supplier_kyc_decision_matches_status",
            ),
            models.CheckConstraint(
                condition=~Q(decided_by=models.F("opened_by")),
                name="supplier_kyc_decider_not_opener",
            ),
            models.CheckConstraint(
                condition=~Q(status="rejected") | ~Q(decision_note=""),
                name="supplier_kyc_rejection_reason",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="supplier_kyc_demo_only"),
        ]


class SupplierKycDocument(models.Model):
    class Kind(models.TextChoices):
        CR_CERTIFICATE = "cr_certificate", "Commercial registration (CR)"
        VAT_CERTIFICATE = "vat_certificate", "VAT certificate"
        SIGNATORY_ID = "signatory_id", "Signatory ID"
        SIGNATORY_AUTHORITY = "signatory_authority", "Signatory authorization"
        BANK_LETTER = "bank_letter", "Bank letter"
        OTHER = "other", "Other"

    kyc = models.ForeignKey(SupplierKyc, on_delete=models.PROTECT, related_name="documents")
    kind = models.CharField(max_length=24, choices=Kind.choices)
    file = models.FileField(upload_to=_kyc_upload_path, max_length=200)
    original_name = models.CharField(max_length=200)
    content_type = models.CharField(max_length=60)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="supplier_kyc_documents")
    uploaded_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["uploaded_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="supplier_kyc_document_demo_only"),
        ]

    def delete(self, *args, **kwargs):
        raise ValidationError("KYC documents are kept; upload a newer one instead.")


class SupplierKycEvent(models.Model):
    """Append-only history of a KYC file: opened, documents added, submitted, approved, rejected."""

    kyc = models.ForeignKey(SupplierKyc, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="supplier_kyc_events")
    event = models.CharField(max_length=24)
    note = models.CharField(max_length=500, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["occurred_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="supplier_kyc_event_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("KYC history is append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("KYC history is append-only.")

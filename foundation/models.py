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
    ROCHE = "roche", "Roche Bobois blue"
    NATUZZI = "natuzzi", "Natuzzi green"
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
    # The companies we buy this brand from; one company can sell us several brands.
    suppliers = models.ManyToManyField("SyntheticCounterparty", related_name="brands", blank=True)
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


def _item_photo_path(instance, filename):
    # Stored under a random name; the item and photo hash identify it in URLs.
    suffix = Path(filename).suffix.lower()
    return f"item-photos/{uuid.uuid4().hex}{suffix}"


class Item(models.Model):
    class Unit(models.TextChoices):
        PIECE = "piece", "Piece"
        SET = "set", "Set"
        PAIR = "pair", "Pair"
        METRE = "metre", "Metre"
        SQUARE_METRE = "square_metre", "Square metre"

    brand = models.ForeignKey(ProductBrand, on_delete=models.PROTECT, related_name="items")
    # The company we buy it from (the supplier), separate from the brand that makes it.
    supplier = models.ForeignKey("SyntheticCounterparty", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="supplied_items")
    supplier_item_code = models.CharField(max_length=60, blank=True)
    barcode = models.CharField(max_length=40, blank=True)
    unit_of_measure = models.CharField(max_length=16, choices=Unit.choices, default=Unit.PIECE)
    width_cm = models.DecimalField(max_digits=8, decimal_places=1, null=True, blank=True)
    depth_cm = models.DecimalField(max_digits=8, decimal_places=1, null=True, blank=True)
    height_cm = models.DecimalField(max_digits=8, decimal_places=1, null=True, blank=True)
    material = models.CharField(max_length=120, blank=True)
    colour = models.CharField(max_length=80, blank=True)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    volume_m3 = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    hs_code = models.CharField(max_length=12, blank=True)
    lead_time_days = models.PositiveSmallIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    photo = models.FileField(upload_to=_item_photo_path, max_length=200, blank=True)
    photo_content_type = models.CharField(max_length=20, blank=True)
    photo_sha256 = models.CharField(max_length=64, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="items_updated")
    updated_at = models.DateTimeField(null=True, blank=True)
    # The supplier's price-list cost, as read from a catalogue or price list (private: 'see item costs').
    supplier_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    supplier_price_currency = models.CharField(max_length=3, blank=True)
    supplier_price_date = models.DateField(null=True, blank=True)
    # The unit that added the item (for example from a catalogue) before it is stocked or priced anywhere.
    added_in = models.ForeignKey("BusinessUnit", null=True, blank=True, on_delete=models.PROTECT, related_name="items_added")
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


class Team(models.Model):
    """A team or group of people who share permissions (for example the sales team).

    Each member gets every permission the team has, on top of what was given to them personally.
    A team also has its own chat, whose members follow the team.
    """

    name = models.CharField(max_length=80)
    description = models.CharField(max_length=300, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="unique_team_name_ci"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="team_demo_only"),
        ]

    def __str__(self):
        return self.name


class TeamMember(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="team_memberships")
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    added_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["team__name"]
        constraints = [models.UniqueConstraint(fields=["team", "user"], name="unique_team_member")]


class UserArea(models.Model):
    """An area of the ERP a person is allocated to (Sam, 5 Nov 2026).

    Areas decide which sections a person sees on the home page and in the
    menu. They are not a permission: what a person may do stays with their
    action grants. "all" means every area.
    """

    class Area(models.TextChoices):
        ALL = "all", "All areas"
        FURNITURE = "furniture", "Tivoli Furniture"
        ROCHE_BOBOIS = "roche-bobois", "Roche Bobois"
        NATUZZI = "natuzzi", "Natuzzi"
        FACTORY = "factory", "Factory"
        PROJECTS = "projects", "Projects & Fit-Out"
        SMART = "smart", "Smart"
        FINANCE = "finance", "Finance"
        WAREHOUSE = "warehouse", "Warehouse"
        LOGISTICS = "logistics", "Logistics & Imports"
        PURCHASING = "purchasing", "Purchasing"
        HR = "hr", "HR & Admin"
        SETUP = "setup", "ERP setup"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="erp_areas")
    area = models.CharField(max_length=16, choices=Area.choices)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    granted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["user_id", "area"]
        constraints = [models.UniqueConstraint(fields=["user", "area"], name="unique_user_area")]

    def __str__(self):
        return f"{self.user} · {self.get_area_display()}"


class TeamEvent(models.Model):
    """What changed on a team and who changed it."""

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    # The person a membership change was about, so it also shows in that person's history.
    person = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    at = models.DateTimeField(default=timezone.now)
    change = models.CharField(max_length=16)
    what = models.CharField(max_length=300)

    class Meta:
        ordering = ["-at", "-id"]


class UserActionGrant(models.Model):
    class Action(models.TextChoices):
        READ_STOCK = "read_stock", "Read stock"
        CREATE_ORDER = "create_order", "Create order"
        REQUEST_TRANSFER = "request_transfer", "Request stock transfer"
        APPROVE_TRANSFER = "approve_transfer", "Approve stock transfer"
        EXECUTE_TRANSFER = "execute_transfer", "Schedule and carry out stock transfer"
        OPEN_SUPPLIER = "open_supplier", "Open new suppliers (KYC)"
        APPROVE_SUPPLIER = "approve_supplier", "Approve new suppliers (KYC)"
        PAY_SUPPLIER = "pay_supplier", "Prepare supplier payments"
        APPROVE_PAYMENT = "approve_payment", "Approve supplier payments"
        VIEW_LEDGER = "view_ledger", "View the general ledger"
        COUNT_STOCK = "count_stock", "Count stock"
        APPROVE_COUNT = "approve_count", "Approve stock counts"
        APPROVE_WRITE_OFF = "approve_write_off", "Approve stock write-offs and count adjustments"
        APPROVE_REFUND = "approve_refund", "Approve customer refunds"
        CLOSE_MONTH = "close_month", "Close and reopen accounting months"
        MANAGE_CASH = "manage_cash", "Record bank and cash"
        APPROVE_BANK_REC = "approve_bank_rec", "Approve bank reconciliations"
        MANAGE_ITEMS = "manage_items", "Edit item details and photos"
        VIEW_COSTS = "view_costs", "See item costs"
        MANAGE_REORDER = "manage_reorder", "Set low-stock levels"
        RECORD_EXPENSE = "record_expense", "Enter expenses"
        APPROVE_EXPENSE = "approve_expense", "Approve expenses"
        PREPARE_BUDGET = "prepare_budget", "Prepare budgets"
        APPROVE_BUDGET = "approve_budget", "Approve budgets"
        MANAGE_ASSETS = "manage_assets", "Register fixed assets and run depreciation"
        APPROVE_ASSET = "approve_asset", "Approve fixed asset purchases and disposals"
        PREPARE_JOURNAL = "prepare_journal", "Prepare manual journal entries"
        APPROVE_JOURNAL = "approve_journal", "Approve and post manual journal entries"
        PREPARE_NOTE = "prepare_note", "Prepare credit and debit notes"
        APPROVE_NOTE = "approve_note", "Approve credit and debit notes"
        VIEW_STAFF = "view_staff", "See staff records"
        MANAGE_STAFF = "manage_staff", "Add and change staff records"
        VIEW_PAY = "view_pay", "See salaries and payroll"
        MANAGE_PAY = "manage_pay", "Set salaries and prepare payroll"
        APPROVE_PAY = "approve_pay", "Approve and post payroll"
        IMPORT_DATA = "import_data", "Prepare go-live imports"
        APPROVE_IMPORT = "approve_import", "Approve go-live imports"
        PREPARE_INCENTIVES = "prepare_incentives", "Prepare commission and rewards"
        CHECK_INCENTIVES = "check_incentives", "Check commission and rewards"
        APPROVE_INCENTIVES = "approve_incentives", "Approve commission and rewards"
        RUN_FACTORY = "run_factory", "Run factory jobs (open jobs, take materials, record hours)"
        APPROVE_FACTORY = "approve_factory", "Approve factory jobs, extra materials and product recipes"
        APPROVE_FACTORY_COST = "approve_factory_cost", "Approve factory jobs that cost more than estimated"
        BUY_FACTORY = "buy_factory", "Buy special items for factory jobs (cash or card)"
        RUN_PROJECTS = "run_projects", "Run projects and tenders (BOQ, orders, schedule, variations, claims)"
        APPROVE_PROJECTS = "approve_projects", "Construction Manager: approve budgets, over-budget orders, variations, claims"
        INVOICE_PROJECTS = "invoice_projects", "Project money for Finance (client receipts, payments, invoices, over-budget)"
        POST_PORTAL = "post_portal", "Post on the HR Portal"

    # A grant belongs either to one person or to a team; a team's grants apply to all its members.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="unit_action_grants",
                             null=True, blank=True)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="grants", null=True, blank=True)
    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="user_action_grants")
    action = models.CharField(max_length=32, choices=Action.choices)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["business_unit__code", "action"]
        constraints = [
            models.UniqueConstraint(fields=["user", "business_unit", "action"], name="unique_user_unit_action_grant"),
            models.UniqueConstraint(fields=["team", "business_unit", "action"], name="unique_team_unit_action_grant"),
            models.CheckConstraint(
                condition=Q(user__isnull=False, team__isnull=True) | Q(user__isnull=True, team__isnull=False),
                name="grant_person_or_team",
            ),
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True) | Q(ends_at__gt=models.F("starts_at")),
                name="grant_end_after_start",
            ),
        ]


class AccessLevelAssignment(models.Model):
    """The preset access level a person is on (Master Admin down to Sales), or Custom.

    Choosing a level sets the person's own permissions, approval roles and chat review
    roles to the level's preset. Head, Manager and Sales apply in the business units
    chosen; the admin levels apply in every unit. Custom means a Master Admin has set
    the permissions one by one; 'based_on' remembers the level it started from.
    """

    class Level(models.TextChoices):
        MASTER_ADMIN = "master_admin", "Master Admin"
        GENERAL_ADMIN = "general_admin", "General Admin"
        FINANCE_ADMIN = "finance_admin", "Finance Admin"
        HR_ADMIN = "hr_admin", "HR Admin"
        HEAD = "head", "Head"
        MANAGER = "manager", "Manager"
        SALES = "sales", "Sales"
        CUSTOM = "custom", "Custom"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="access_level")
    level = models.CharField(max_length=20, choices=Level.choices)
    based_on = models.CharField(max_length=20, choices=Level.choices, blank=True)
    business_units = models.ManyToManyField(BusinessUnit, blank=True, related_name="+")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    assigned_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="access_level_demo_only"),
        ]

    def __str__(self):
        return f"{self.user} — {self.get_level_display()}"


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
        COUNT_GAIN = "count_gain", "Stock count surplus"
        COUNT_LOSS = "count_loss", "Stock count write-off"
        RETURN_IN = "return_in", "Customer return"
        CAPITALIZED = "capitalized", "Taken into fixed assets"
        OPENING = "opening", "Opening stock at go-live"
        PRODUCTION_USE = "production_use", "Taken for a factory job"
        PRODUCTION_IN = "production_in", "Made in the factory"

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
                    | Q(kind="count_gain", quantity_delta__gt=0, value_delta__gte=0, transfer__isnull=True)
                    | Q(kind="count_loss", quantity_delta__lt=0, value_delta__lte=0, transfer__isnull=True)
                    | Q(kind="return_in", quantity_delta__gt=0, value_delta__gte=0, transfer__isnull=True)
                    | Q(kind="capitalized", quantity_delta__lt=0, value_delta__lte=0, transfer__isnull=True)
                    | Q(kind="opening", quantity_delta__gt=0, value_delta__gte=0, transfer__isnull=True)
                    | Q(kind="production_use", quantity_delta__lt=0, value_delta__lte=0, transfer__isnull=True)
                    | Q(kind="production_in", quantity_delta__gt=0, value_delta__gte=0, transfer__isnull=True)
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
                    "pay_supplier", "approve_payment", "view_ledger", "count_stock", "approve_count",
                    "approve_write_off", "approve_refund", "close_month", "manage_cash", "approve_bank_rec",
                    "manage_reorder", "manage_items", "view_costs",
                    "read_stock", "create_order", "record_expense", "approve_expense", "prepare_budget", "approve_budget",
                    "manage_assets", "approve_asset", "prepare_journal", "approve_journal", "prepare_note", "approve_note",
                    "view_staff", "manage_staff", "view_pay", "manage_pay", "approve_pay", "import_data", "approve_import",
                    "prepare_incentives", "check_incentives", "approve_incentives",
                    "run_factory", "approve_factory", "approve_factory_cost", "buy_factory", "post_portal",
                    "run_projects", "approve_projects", "invoice_projects",
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
        SUPPLIER_PAYMENT = "supplier_payment", "Synthetic supplier payment"
        STOCK_COUNT = "stock_count", "Synthetic stock count"
        SALES_RETURN = "sales_return", "Synthetic sales return"
        SUPPLIER_BILL = "supplier_bill", "Synthetic supplier bill difference"
        CASH_ENTRY = "cash_entry", "Synthetic bank or cash entry"
        EXPENSE = "expense", "Synthetic expense"
        FIXED_ASSET = "fixed_asset", "Synthetic fixed asset"
        DEPRECIATION = "depreciation", "Synthetic depreciation"
        MANUAL = "manual", "Synthetic manual journal"
        ADJUSTMENT_NOTE = "adjustment_note", "Synthetic credit or debit note"
        SUPPLIER_ADVANCE = "supplier_advance", "Synthetic advance paid to a supplier"
        COST_SHARE = "cost_share", "Synthetic cost shared between business units"
        PAYROLL = "payroll", "Synthetic monthly payroll"
        FX_REVALUATION = "fx_revaluation", "Synthetic foreign currency revaluation"
        OPENING_BALANCE = "opening_balance", "Synthetic opening balance at go-live"
        OLD_BALANCE_SETTLEMENT = "old_balance_paid", "Synthetic payment on a balance from the old system"
        COMMISSION = "commission", "Synthetic commission and rewards"
        FACTORY = "factory", "Synthetic factory job"
        PROJECT = "project", "Synthetic project advance or claim invoice"

    idempotency = models.OneToOneField(
        IdempotencyRecord, on_delete=models.PROTECT, related_name="journal_entry"
    )
    legal_entity = models.ForeignKey(LegalEntity, on_delete=models.PROTECT, related_name="journal_entries")
    business_unit = models.ForeignKey(
        BusinessUnit,
        on_delete=models.PROTECT,
        related_name="report_journal_entries",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    currency = models.CharField(max_length=3, default="QAR")
    policy_label = models.CharField(max_length=64, default="TEST_ONLY_NOT_TIVOLI_APPROVED")
    description = models.CharField(max_length=240)
    document_date = models.DateField(
        default=timezone.localdate,
        help_text="Accounting date: the date of the source document (payment, receipt, delivery), not the posting time.",
    )
    created_at = models.DateTimeField(default=timezone.now)
    posted_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["created_at", "pk"]
        indexes = [models.Index(fields=["legal_entity", "business_unit", "document_date"], name="journal_document_date_idx")]
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


class CustomerType(models.TextChoices):
    """What kind of customer this is. It sets how long they usually take to pay (used by the cash forecast)."""

    INDIVIDUAL = "individual", "Individual"
    COMPANY = "company", "Company"
    GOVERNMENT = "government", "Government"


class CustomerTitle(models.TextChoices):
    """How a customer is addressed. A fixed list, so it can be searched and counted in reports."""

    MR = "mr", "Mr"
    MRS = "mrs", "Mrs"
    MS = "ms", "Ms"
    MISS = "miss", "Miss"
    DR = "dr", "Dr"
    ENG = "eng", "Eng."
    SHEIKH = "sheikh", "Sheikh"
    SHEIKHA = "sheikha", "Sheikha"
    HE = "he", "H.E."


CUSTOMER_TITLE_ARABIC = {
    "mr": "السيد", "mrs": "السيدة", "ms": "السيدة", "miss": "الآنسة", "dr": "الدكتور",
    "eng": "المهندس", "sheikh": "الشيخ", "sheikha": "الشيخة", "he": "سعادة",
}


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
    # A customer has a client number (retail purchases), a project number
    # (projects and fitout), or both: one record, both numbers visible and
    # linked, even though the two sides keep separate finances. A number is
    # given once and a database trigger refuses any change to it.
    client_number = models.CharField(max_length=12, null=True, blank=True, unique=True, editable=False)
    project_number = models.CharField(max_length=12, null=True, blank=True, unique=True, editable=False)
    title = models.CharField(max_length=12, choices=CustomerTitle.choices, blank=True)
    customer_type = models.CharField(max_length=12, choices=CustomerType.choices, default=CustomerType.INDIVIDUAL)
    # Qatar ID: optional, because a customer may decline to give it. When given it
    # is unique and, once set, a database trigger refuses any change. A customer
    # without one is marked as having declined, so staff know why it is blank.
    # A QID can still be added later if the customer agrees. Suppliers have none.
    qid = models.CharField(max_length=11, null=True, blank=True, unique=True)
    qid_declined = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["party_type", "code"]
        constraints = [
            models.UniqueConstraint(fields=["party_type", "code"], name="synthetic_party_code_unique"),
            models.CheckConstraint(
                condition=(
                    Q(party_type="customer", qid__isnull=False, qid__regex=r"^[23][0-9]{10}$", qid_declined=False)
                    | Q(party_type="customer", qid__isnull=True, qid_declined=True)
                    | Q(party_type="supplier", qid__isnull=True, qid_declined=False)
                ),
                name="customer_qid_given_or_declined",
            ),
            models.CheckConstraint(
                condition=(
                    (
                        Q(party_type="customer")
                        & (Q(client_number__isnull=False) | Q(project_number__isnull=False))
                        & (Q(client_number__isnull=True) | Q(client_number__regex=r"^C-[0-9]{6,}$"))
                        & (Q(project_number__isnull=True) | Q(project_number__regex=r"^P-[0-9]{6,}$"))
                    )
                    | Q(party_type="supplier", client_number__isnull=True, project_number__isnull=True, title="")
                ),
                name="customer_has_a_client_or_project_number",
            ),
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


    def save(self, *args, **kwargs):
        if self.party_type == self.Type.CUSTOMER and not (self.client_number or self.project_number):
            self.client_number = next_customer_number("client")
        return super().save(*args, **kwargs)


def next_customer_number(kind):
    """The next client number (C-000123) or project number (P-000123). Numbers are never reused."""
    from django.db import connection

    prefix, sequence = {"client": ("C", "foundation_client_number_seq"), "project": ("P", "foundation_project_number_seq")}[kind]
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT nextval('{sequence}')")
        return f"{prefix}-{cursor.fetchone()[0]:06d}"


class CustomerContactChange(models.Model):
    """A customer's earlier name or contact details, kept when they change. The QID never changes."""

    customer = models.ForeignKey(SyntheticCounterparty, on_delete=models.PROTECT, related_name="contact_changes")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="customer_contact_changes"
    )
    changed_at = models.DateTimeField(default=timezone.now)
    before = models.JSONField()
    after = models.JSONField()
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-changed_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="customer_contact_change_demo_only"),
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
        SIMULATED_CHEQUE = "simulated_cheque", "Simulated cheque"

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


class SupplierPayment(models.Model):
    """Payment against one purchase receipt: to the supplier for the goods, or to another party for a charge.

    Maker/checker: one person prepares the payment (status requested) and a
    different person approves it, which posts the journal. A payment for the
    goods clears receipt clearing; a payment for a shipping, clearance or other
    charge clears landed-charge clearing and can go to any party (payee_name).
    Amounts are in the currency of what is being paid; the rate is QAR per one
    unit on the payment day. The difference between what the receipt carried in
    QAR and what left the bank is a realized exchange gain (positive) or loss.
    """

    class Status(models.TextChoices):
        REQUESTED = "requested", "Waiting for approval"
        APPROVED = "approved", "Approved and paid"
        REJECTED = "rejected", "Rejected"

    idempotency = models.OneToOneField(
        IdempotencyRecord, on_delete=models.PROTECT, related_name="supplier_payment"
    )
    approval = models.OneToOneField(
        IdempotencyRecord, null=True, blank=True, on_delete=models.PROTECT, related_name="supplier_payment_approval"
    )
    receipt = models.ForeignKey(PurchaseReceipt, on_delete=models.PROTECT, related_name="supplier_payments")
    charge = models.ForeignKey(
        PurchaseReceiptCharge, null=True, blank=True, on_delete=models.PROTECT, related_name="payments"
    )
    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="supplier_payments")
    supplier = models.ForeignKey(
        SyntheticCounterparty, on_delete=models.PROTECT, related_name="supplier_payments"
    )
    payee_name = models.CharField(max_length=120, default="")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.REQUESTED)
    journal_entry = models.OneToOneField(
        "JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="supplier_payment"
    )
    currency = models.CharField(max_length=3, default="QAR")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=8, default=1)
    rate_source = models.CharField(max_length=120, blank=True)
    rate_date = models.DateField(null=True, blank=True)
    # QAR values: what leaves the bank, and on approval what the receipt carried for this part and the difference.
    paid_value = models.DecimalField(max_digits=16, decimal_places=2)
    cleared_value = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    exchange_difference = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    paid_on = models.DateField()
    bank_reference = models.CharField(max_length=80, blank=True)
    # Set when the money comes from an advance already paid to the supplier, not from the bank.
    advance = models.ForeignKey(
        "SupplierAdvance", null=True, blank=True, on_delete=models.PROTECT, related_name="applications"
    )
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="supplier_payments_made"
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="supplier_payments_decided",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-paid_on", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="supplier_payment_amount_positive"),
            models.CheckConstraint(condition=Q(paid_value__gt=0), name="supplier_payment_paid_positive"),
            models.CheckConstraint(
                condition=(
                    Q(status="approved", journal_entry__isnull=False, approval__isnull=False, cleared_value__gt=0,
                      exchange_difference=F("cleared_value") - F("paid_value"))
                    | (~Q(status="approved") & Q(journal_entry__isnull=True, cleared_value__isnull=True,
                                                 exchange_difference__isnull=True))
                ),
                name="supplier_payment_posted_when_approved",
            ),
            models.CheckConstraint(
                condition=Q(status="requested", decided_by__isnull=True, decided_at__isnull=True)
                | (~Q(status="requested") & Q(decided_by__isnull=False, decided_at__isnull=False)),
                name="supplier_payment_decision_recorded",
            ),
            models.CheckConstraint(condition=~Q(payee_name=""), name="supplier_payment_payee_named"),
            models.CheckConstraint(
                condition=Q(decided_by__isnull=True) | ~Q(decided_by=F("paid_by")),
                name="supplier_payment_maker_checker",
            ),
            models.CheckConstraint(
                condition=~Q(currency=BASE_CURRENCY) | Q(exchange_difference__isnull=True) | Q(exchange_difference=0),
                name="supplier_payment_qar_no_difference",
            ),
            *_exchange_rate_constraints("supplier_payment"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="supplier_payment_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            current = type(self).objects.get(pk=self.pk)
            if current.status != self.Status.REQUESTED:
                raise ValidationError("An approved or rejected supplier payment cannot change.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Supplier payments are kept as a record and cannot be deleted.")


class SupplierBill(models.Model):
    """The supplier's bill for the goods on one purchase receipt, matched against what was received.

    Goods can be paid only once a bill is recorded and either matches the receipt
    to the cent (matched) or a second person in Finance has accepted the
    difference (accepted). A bill with a difference waits (on_hold) until someone
    other than the person who recorded it accepts or rejects it. Accepting posts
    the difference, at the receipt's rate, between receipt clearing and supplier
    bill differences, so the goods are then payable at the bill amount. A
    rejected or voided bill is kept as a record and a new bill can be recorded.
    Amounts are in the receipt's currency.
    """

    class Status(models.TextChoices):
        MATCHED = "matched", "Matches the receipt"
        ON_HOLD = "on_hold", "Difference waiting for a second person"
        ACCEPTED = "accepted", "Difference accepted"
        REJECTED = "rejected", "Rejected"
        VOIDED = "voided", "Voided"

    ACTIVE = ("matched", "on_hold", "accepted")
    PAYABLE = ("matched", "accepted")

    idempotency = models.OneToOneField(IdempotencyRecord, on_delete=models.PROTECT, related_name="supplier_bill")
    receipt = models.ForeignKey(PurchaseReceipt, on_delete=models.PROTECT, related_name="supplier_bills")
    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="supplier_bills")
    supplier = models.ForeignKey(SyntheticCounterparty, on_delete=models.PROTECT, related_name="supplier_bills")
    bill_number = models.CharField(max_length=40)
    bill_date = models.DateField()
    currency = models.CharField(max_length=3, default="QAR")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    # What the receipt booked for the goods in the same currency, and amount less that.
    expected_amount = models.DecimalField(max_digits=16, decimal_places=2)
    difference = models.DecimalField(max_digits=16, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="supplier_bills_recorded"
    )
    note = models.CharField(max_length=240, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="supplier_bills_decided"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=240, blank=True)
    # Set when a difference is accepted: its QAR value at the receipt rate (positive when the bill is higher),
    # posted as a journal unless it rounds to zero.
    acceptance = models.OneToOneField(
        IdempotencyRecord, null=True, blank=True, on_delete=models.PROTECT, related_name="supplier_bill_acceptance"
    )
    journal_entry = models.OneToOneField(
        "JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="supplier_bill"
    )
    difference_value = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-bill_date", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="supplier_bill_amount_positive"),
            models.CheckConstraint(
                condition=Q(difference=F("amount") - F("expected_amount")), name="supplier_bill_difference_matches",
            ),
            models.CheckConstraint(
                condition=(Q(status="matched", difference=0) | (Q(status__in=("on_hold", "accepted", "rejected")) & ~Q(difference=0))
                           | Q(status="voided")),
                name="supplier_bill_matched_means_no_difference",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="accepted", acceptance__isnull=False, difference_value__isnull=False)
                    | (~Q(status="accepted") & Q(journal_entry__isnull=True, acceptance__isnull=True,
                                                 difference_value__isnull=True))
                ),
                name="supplier_bill_posted_when_accepted",
            ),
            models.CheckConstraint(
                condition=Q(status__in=("matched", "on_hold"), decided_by__isnull=True, decided_at__isnull=True)
                | (~Q(status__in=("matched", "on_hold")) & Q(decided_by__isnull=False, decided_at__isnull=False)),
                name="supplier_bill_decision_recorded",
            ),
            models.CheckConstraint(
                condition=~Q(status__in=("accepted", "rejected")) | ~Q(decided_by=F("recorded_by")),
                name="supplier_bill_second_person",
            ),
            models.UniqueConstraint(
                fields=["receipt"], condition=Q(status__in=("matched", "on_hold", "accepted")),
                name="supplier_bill_one_active_per_receipt",
            ),
            models.UniqueConstraint(
                "supplier", Lower("bill_number"), condition=Q(status__in=("matched", "on_hold", "accepted")),
                name="supplier_bill_number_unique_per_supplier",
            ),
            models.CheckConstraint(condition=~Q(bill_number=""), name="supplier_bill_number_given"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="supplier_bill_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            current = type(self).objects.get(pk=self.pk)
            if current.status not in (self.Status.MATCHED, self.Status.ON_HOLD):
                raise ValidationError("An accepted, rejected or voided supplier bill cannot change.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Supplier bills are kept as a record and cannot be deleted.")


class CashAccount(models.Model):
    """A bank account or cash box of one business unit.

    Every journal line on cash and bank belongs to one account: entries made
    here say which, and money from other screens goes to the unit's default
    cash box when it was cash and to its default bank account otherwise, until
    someone moves it. The balance is the opening balance plus every line dated
    on or after the opening date. QAR only for now.
    """

    class Kind(models.TextChoices):
        BANK = "bank", "Bank account"
        CASH = "cash", "Cash account"

    class AccountType(models.TextChoices):
        CURRENT = "current", "Current account"
        SAVINGS = "savings", "Savings account"
        LTR = "ltr", "LTR account (loan against trust receipt)"
        LG = "lg", "LG account (letter of guarantee margin)"
        OTHER = "other", "Other account"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="cash_accounts")
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=4, choices=Kind.choices)
    account_type = models.CharField(max_length=8, choices=AccountType.choices, blank=True)
    bank_name = models.CharField(max_length=80, blank=True)
    account_last4 = models.CharField(max_length=4, blank=True)
    currency = models.CharField(max_length=3, default="QAR")
    opening_balance = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    opening_date = models.DateField()
    is_default = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cash_accounts_created")
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["business_unit", "-kind", "bank_name", "name"]
        constraints = [
            models.UniqueConstraint("business_unit", Lower("name"), name="cash_account_name_unique_per_unit"),
            models.UniqueConstraint(
                fields=["business_unit", "kind"], condition=Q(is_default=True), name="cash_account_one_default_per_kind",
            ),
            models.CheckConstraint(condition=Q(currency="QAR"), name="cash_account_qar_only"),
            models.UniqueConstraint(fields=["business_unit"], condition=Q(kind="cash"), name="cash_account_one_cash_per_unit"),
            models.CheckConstraint(condition=Q(kind="cash", account_type="") | (Q(kind="bank") & ~Q(account_type="")),
                                   name="cash_account_bank_has_type"),
            models.CheckConstraint(condition=~Q(name=""), name="cash_account_named"),
            models.CheckConstraint(
                condition=Q(account_last4="") | Q(account_last4__regex=r"^[0-9]{4}$"), name="cash_account_last4_digits",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="cash_account_demo_only"),
        ]

    def delete(self, *args, **kwargs):
        raise ValidationError("Bank and cash accounts are kept as a record and cannot be deleted.")


class CashEntry(models.Model):
    """Money in or out recorded on the bank and cash screen: a transfer, bank charges or interest."""

    class Kind(models.TextChoices):
        TRANSFER = "transfer", "Transfer between accounts"
        BANK_CHARGE = "bank_charge", "Bank charges"
        INTEREST = "interest", "Interest received"

    idempotency = models.OneToOneField(IdempotencyRecord, on_delete=models.PROTECT, related_name="cash_entry")
    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="cash_entries")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    account = models.ForeignKey(CashAccount, on_delete=models.PROTECT, related_name="entries")
    # Transfers only: where the money went (it leaves account).
    to_account = models.ForeignKey(
        CashAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="incoming_transfers"
    )
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    entry_date = models.DateField()
    description = models.CharField(max_length=160)
    reference = models.CharField(max_length=80, blank=True)
    journal_entry = models.OneToOneField("JournalEntry", on_delete=models.PROTECT, related_name="cash_entry")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cash_entries")
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-entry_date", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="cash_entry_amount_positive"),
            models.CheckConstraint(
                condition=(Q(kind="transfer", to_account__isnull=False) & ~Q(to_account=F("account")))
                | (~Q(kind="transfer") & Q(to_account__isnull=True)),
                name="cash_entry_transfer_has_other_account",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="cash_entry_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("A bank or cash entry cannot change once posted.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Bank and cash entries are kept as a record and cannot be deleted.")


def _expense_receipt_path(instance, filename):
    return f"expense-receipts/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class Expense(models.Model):
    """A running cost (rent, fuel, a repair) entered by one person and booked once another approves it.

    Paid from a bank account or cash box (petty cash), or paid by a member of
    staff with their own money and owed back to them until reimbursed.
    """

    class Category(models.TextChoices):
        RENT = "rent", "Rent"
        UTILITIES = "utilities", "Electricity and water"
        TELECOM = "telecom", "Telephone and internet"
        MARKETING = "marketing", "Marketing and advertising"
        TRAVEL = "travel", "Travel"
        TRANSPORT = "transport", "Transport and delivery"
        REPAIRS = "repairs", "Repairs and maintenance"
        OFFICE = "office", "Office supplies"
        PROFESSIONAL = "professional", "Professional fees"
        GOVERNMENT = "government", "Government fees"
        HOSPITALITY = "hospitality", "Hospitality"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Waiting for approval"
        APPROVED = "approved", "Approved and booked"
        REJECTED = "rejected", "Rejected"
        REIMBURSED = "reimbursed", "Approved and repaid"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="expenses")
    number = models.CharField(max_length=24, unique=True)
    expense_date = models.DateField()
    category = models.CharField(max_length=16, choices=Category.choices)
    description = models.CharField(max_length=200)
    payee = models.CharField(max_length=120, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    # Exactly one: the bank account or cash box it was paid from, or the person who paid and is owed it back.
    paid_from = models.ForeignKey("CashAccount", null=True, blank=True, on_delete=models.PROTECT, related_name="expenses")
    claimant = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="expense_claims")
    receipt = models.FileField(upload_to=_expense_receipt_path, max_length=200, blank=True)
    receipt_content_type = models.CharField(max_length=40, blank=True)
    receipt_sha256 = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SUBMITTED)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="expenses_submitted")
    submitted_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="expenses_decided")
    decided_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=200, blank=True)
    journal_entry = models.OneToOneField("JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                         related_name="expense")
    reimbursed_from = models.ForeignKey("CashAccount", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="expense_reimbursements")
    reimbursed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="expenses_reimbursed")
    reimbursed_at = models.DateTimeField(null=True, blank=True)
    reimbursement_entry = models.OneToOneField("JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                               related_name="expense_reimbursement")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-expense_date", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="expense_amount_positive"),
            models.CheckConstraint(
                condition=Q(paid_from__isnull=False, claimant__isnull=True) | Q(paid_from__isnull=True, claimant__isnull=False),
                name="expense_paid_from_or_claimant",
            ),
            models.CheckConstraint(
                condition=Q(status="submitted", journal_entry__isnull=True) | Q(status="rejected", journal_entry__isnull=True)
                | Q(status__in=["approved", "reimbursed"], journal_entry__isnull=False),
                name="expense_booked_when_approved",
            ),
            models.CheckConstraint(
                condition=~Q(status="reimbursed") | Q(claimant__isnull=False, reimbursement_entry__isnull=False),
                name="expense_reimbursed_claims_only",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="expense_demo_only"),
        ]


class Budget(models.Model):
    """One business unit's budget for one calendar year: an amount per line per month.

    Prepared by someone with prepare_budget, approved by a different person with
    approve_budget. An approved budget is locked; reopening it needs a reason and
    is kept in the history.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Being prepared"
        SUBMITTED = "submitted", "Waiting for approval"
        APPROVED = "approved", "Approved"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="budgets")
    year = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    note = models.CharField(max_length=300, blank=True)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="budgets_prepared")
    updated_at = models.DateTimeField(default=timezone.now)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="budgets_approved")
    approved_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["business_unit__code", "-year"]
        constraints = [
            models.UniqueConstraint(fields=["business_unit", "year"], name="budget_unit_year_unique"),
            models.CheckConstraint(condition=Q(year__gte=2000, year__lte=2100), name="budget_year_range"),
            models.CheckConstraint(
                condition=~Q(status="approved") | Q(approved_by__isnull=False, approved_at__isnull=False),
                name="budget_approved_has_approver",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="budget_demo_only"),
        ]


class BudgetAmount(models.Model):
    """The budgeted amount for one line (sales, cost of sales or a running-cost category) in one month."""

    budget = models.ForeignKey(Budget, on_delete=models.CASCADE, related_name="amounts")
    line = models.CharField(max_length=24)
    month = models.PositiveSmallIntegerField()
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["budget", "line", "month"], name="budget_amount_unique"),
            models.CheckConstraint(condition=Q(month__gte=1, month__lte=12), name="budget_amount_month_range"),
            models.CheckConstraint(condition=Q(amount__gte=0), name="budget_amount_nonnegative"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="budget_amount_demo_only"),
        ]


class BudgetEvent(models.Model):
    """What happened to a budget and who did it: saved, sent for approval, approved, sent back, reopened."""

    class Kind(models.TextChoices):
        SAVED = "saved", "saved changes"
        SUBMITTED = "submitted", "sent it for approval"
        APPROVED = "approved", "approved it"
        RETURNED = "returned", "sent it back"
        REOPENED = "reopened", "reopened it"

    budget = models.ForeignKey(Budget, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="budget_events")
    at = models.DateTimeField(default=timezone.now)
    note = models.CharField(max_length=300, blank=True)
    total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-at", "-pk"]
        constraints = [models.CheckConstraint(condition=Q(is_synthetic=True), name="budget_event_demo_only")]


class FixedAsset(models.Model):
    """Something the business keeps and uses for more than a year (furniture, a van, computers, a showroom fit-out).

    Registered by one person and booked when a different person approves it.
    Its cost, less any value expected at the end, is spread evenly over its
    useful life by the monthly depreciation run. It ends when sold or written off.
    """

    class Category(models.TextChoices):
        FURNITURE = "furniture", "Furniture and fittings"
        FITOUT = "fitout", "Showroom and office fit-out"
        VEHICLES = "vehicles", "Vehicles"
        COMPUTERS = "computers", "Computers and IT equipment"
        MACHINERY = "machinery", "Machinery and tools"
        OTHER = "other", "Other equipment"

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Waiting for approval"
        IN_USE = "in_use", "In use"
        REJECTED = "rejected", "Rejected"
        DISPOSED = "disposed", "Sold or written off"

    class Source(models.TextChoices):
        PURCHASE = "purchase", "Bought and paid from bank or cash"
        CONSTRUCTION = "construction", "Finished construction (from assets under construction)"
        STOCK = "stock", "Taken from stock for the company's own use"
        OPENING = "opening", "Brought over from the old system at go-live"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="fixed_assets")
    number = models.CharField(max_length=24, unique=True)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=12, choices=Category.choices)
    supplier = models.CharField(max_length=120, blank=True)
    purchase_date = models.DateField()
    cost = models.DecimalField(max_digits=14, decimal_places=2)
    residual_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    life_months = models.PositiveSmallIntegerField()
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.PURCHASE)
    paid_from = models.ForeignKey("CashAccount", null=True, blank=True, on_delete=models.PROTECT, related_name="fixed_assets")
    stock_variant = models.ForeignKey(Variant, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    stock_location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    stock_quantity = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    opening_depreciation = models.DecimalField(max_digits=14, decimal_places=2, default=0,
                                               help_text="Depreciation already taken in the old system, for go-live assets.")
    depreciation_from = models.DateField(null=True, blank=True, help_text="Go-live assets: the first month the ERP depreciates.")
    opening_months_left = models.PositiveSmallIntegerField(null=True, blank=True,
                                                           help_text="Go-live assets: months of useful life left at go-live.")
    opening_monthly = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                          help_text="Go-live assets: the old system's monthly depreciation, kept by the ERP.")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SUBMITTED)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="fixed_assets_submitted")
    submitted_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="fixed_assets_decided")
    decided_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=200, blank=True)
    journal_entry = models.OneToOneField("JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                         related_name="fixed_asset")
    disposal_date = models.DateField(null=True, blank=True)
    disposal_proceeds = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    disposal_account = models.ForeignKey("CashAccount", null=True, blank=True, on_delete=models.PROTECT,
                                         related_name="fixed_asset_disposals")
    disposal_reason = models.CharField(max_length=200, blank=True)
    disposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="fixed_assets_disposed")
    disposal_entry = models.OneToOneField("JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                          related_name="fixed_asset_disposal")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-purchase_date", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(cost__gt=0), name="fixed_asset_cost_positive"),
            models.CheckConstraint(condition=Q(residual_value__gte=0, residual_value__lt=models.F("cost")),
                                   name="fixed_asset_residual_below_cost"),
            models.CheckConstraint(condition=Q(life_months__gte=1, life_months__lte=600), name="fixed_asset_life_range"),
            models.CheckConstraint(
                condition=Q(status__in=["submitted", "rejected"], journal_entry__isnull=True)
                | Q(status__in=["in_use", "disposed"], journal_entry__isnull=False),
                name="fixed_asset_booked_when_approved",
            ),
            models.CheckConstraint(
                condition=~Q(status="disposed") | Q(disposal_date__isnull=False, disposal_entry__isnull=False),
                name="fixed_asset_disposal_recorded",
            ),
            models.CheckConstraint(
                condition=Q(source="purchase", paid_from__isnull=False, stock_variant__isnull=True)
                | Q(source="construction", paid_from__isnull=True, stock_variant__isnull=True)
                | Q(source="stock", paid_from__isnull=True, stock_variant__isnull=False, stock_location__isnull=False,
                    stock_quantity__gt=0)
                | Q(source="opening", paid_from__isnull=True, stock_variant__isnull=True, depreciation_from__isnull=False,
                    opening_months_left__isnull=False, opening_monthly__gt=0),
                name="fixed_asset_source_details",
            ),
            models.CheckConstraint(condition=Q(opening_depreciation__gte=0)
                                   & Q(opening_depreciation__lte=models.F("cost") - models.F("residual_value")),
                                   name="fixed_asset_opening_depreciation_range"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="fixed_asset_demo_only"),
        ]


class DepreciationRun(models.Model):
    """One business unit's depreciation for one month, posted as one journal entry."""

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="depreciation_runs")
    month = models.DateField()
    total = models.DecimalField(max_digits=16, decimal_places=2)
    journal_entry = models.OneToOneField("JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                         related_name="depreciation_run")
    run_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="depreciation_runs")
    run_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-month"]
        constraints = [
            models.UniqueConstraint(fields=["business_unit", "month"], name="depreciation_run_unit_month_unique"),
            models.CheckConstraint(condition=Q(month__day=1), name="depreciation_run_first_of_month"),
            models.CheckConstraint(condition=Q(total__gte=0), name="depreciation_run_total_nonnegative"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="depreciation_run_demo_only"),
        ]


class FxRevaluation(models.Model):
    """One business unit's month-end revaluation of foreign currency owed to suppliers, reversed the next day."""

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="fx_revaluations")
    month = models.DateField(help_text="First day of the month revalued.")
    rates = models.JSONField(default=dict, help_text="QAR per one unit of each currency at the month end.")
    source = models.CharField(max_length=120)
    net = models.DecimalField(max_digits=16, decimal_places=2, help_text="Positive is a loss, negative a gain.")
    lines = models.JSONField(default=list)
    journal_entry = models.OneToOneField("JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                         related_name="fx_revaluation")
    reversal_entry = models.OneToOneField("JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                          related_name="fx_revaluation_reversed")
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="fx_revaluations")
    posted_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-month"]
        constraints = [
            models.UniqueConstraint(fields=["business_unit", "month"], name="fx_revaluation_unit_month_unique"),
            models.CheckConstraint(condition=Q(month__day=1), name="fx_revaluation_first_of_month"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="fx_revaluation_demo_only"),
            models.CheckConstraint(condition=Q(journal_entry__isnull=True, reversal_entry__isnull=True)
                                   | Q(journal_entry__isnull=False, reversal_entry__isnull=False), name="fx_revaluation_reversed"),
        ]


class AssetDepreciation(models.Model):
    """One asset's share of one monthly depreciation run."""

    run = models.ForeignKey(DepreciationRun, on_delete=models.PROTECT, related_name="charges")
    asset = models.ForeignKey(FixedAsset, on_delete=models.PROTECT, related_name="depreciation")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "asset"], name="asset_depreciation_once_per_run"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="asset_depreciation_positive"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="asset_depreciation_demo_only"),
        ]


class CashLineAssignment(models.Model):
    """Which bank account or cash box one cash journal line belongs to, when set explicitly."""

    journal_line = models.OneToOneField("JournalLine", on_delete=models.PROTECT, related_name="cash_assignment")
    account = models.ForeignKey(CashAccount, on_delete=models.PROTECT, related_name="assignments")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cash_assignments")
    assigned_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="cash_assignment_demo_only"),
        ]


class BankStatementLine(models.Model):
    """One line of a bank statement (or cash count sheet) for an account; positive is money in."""

    account = models.ForeignKey(CashAccount, on_delete=models.PROTECT, related_name="statement_lines")
    import_operation = models.ForeignKey(IdempotencyRecord, on_delete=models.PROTECT, related_name="statement_lines")
    line_date = models.DateField()
    description = models.CharField(max_length=160)
    reference = models.CharField(max_length=80, blank=True)
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="statement_lines_added")
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["account", "line_date", "pk"]
        constraints = [
            models.CheckConstraint(condition=~Q(amount=0), name="statement_line_not_zero"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="statement_line_demo_only"),
        ]


class CashMatch(models.Model):
    """A statement line matched to the cash journal line that records the same money."""

    statement_line = models.OneToOneField(BankStatementLine, on_delete=models.PROTECT, related_name="match")
    journal_line = models.OneToOneField("JournalLine", on_delete=models.PROTECT, related_name="cash_match")
    matched_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cash_matches")
    matched_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(is_synthetic=True), name="cash_match_demo_only")]


class BankReconciliation(models.Model):
    """One account's month, reconciled to its statement by one person and approved by another.

    The figures are a snapshot taken when it was prepared; approval checks that
    nothing changed since. Once approved, that month's statement lines and
    matches on the account are locked.
    """

    class Status(models.TextChoices):
        PREPARED = "prepared", "Waiting for approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    idempotency = models.OneToOneField(IdempotencyRecord, on_delete=models.PROTECT, related_name="bank_reconciliation")
    account = models.ForeignKey(CashAccount, on_delete=models.PROTECT, related_name="reconciliations")
    month = models.DateField(help_text="First day of the month.")
    statement_opening = models.DecimalField(max_digits=16, decimal_places=2)
    statement_closing = models.DecimalField(max_digits=16, decimal_places=2)
    statement_movement = models.DecimalField(max_digits=16, decimal_places=2)
    book_balance = models.DecimalField(max_digits=16, decimal_places=2)
    outstanding_in = models.DecimalField(max_digits=16, decimal_places=2)
    outstanding_out = models.DecimalField(max_digits=16, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PREPARED)
    note = models.CharField(max_length=240, blank=True)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="bank_recs_prepared")
    prepared_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="bank_recs_decided"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=240, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["account", "-month", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(month__day=1), name="bank_rec_first_day"),
            models.CheckConstraint(
                condition=Q(statement_closing=F("statement_opening") + F("statement_movement")),
                name="bank_rec_statement_adds_up",
            ),
            models.CheckConstraint(
                condition=Q(statement_closing=F("book_balance") - F("outstanding_in") + F("outstanding_out")),
                name="bank_rec_ties_to_book",
            ),
            models.UniqueConstraint(
                fields=["account", "month"], condition=~Q(status="rejected"), name="bank_rec_once_per_month",
            ),
            models.CheckConstraint(
                condition=Q(status="prepared", decided_by__isnull=True, decided_at__isnull=True)
                | (~Q(status="prepared") & Q(decided_by__isnull=False, decided_at__isnull=False)),
                name="bank_rec_decision_recorded",
            ),
            models.CheckConstraint(
                condition=Q(decided_by__isnull=True) | ~Q(decided_by=F("prepared_by")), name="bank_rec_second_person",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="bank_rec_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.get(pk=self.pk).status != self.Status.PREPARED:
            raise ValidationError("An approved or rejected bank reconciliation cannot change.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Bank reconciliations are kept as a record and cannot be deleted.")


class StockReorderLevel(models.Model):
    """The least stock a business unit wants of a variant, at one location or across all of them."""

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="reorder_levels")
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, related_name="reorder_levels")
    # Empty means the total across every stock-holding location of the unit.
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name="reorder_levels")
    minimum_quantity = models.DecimalField(max_digits=12, decimal_places=2)
    reorder_quantity = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    note = models.CharField(max_length=160, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reorder_levels_set")
    updated_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["variant__sku", "location__code", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["business_unit", "variant", "location"], name="unique_reorder_level_at_location",
                                    condition=Q(location__isnull=False)),
            models.UniqueConstraint(fields=["business_unit", "variant"], name="unique_reorder_level_unit_wide",
                                    condition=Q(location__isnull=True)),
            models.CheckConstraint(condition=Q(minimum_quantity__gte=0), name="reorder_minimum_nonnegative"),
            models.CheckConstraint(condition=Q(reorder_quantity__isnull=True) | Q(reorder_quantity__gt=0),
                                   name="reorder_quantity_positive"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="reorder_level_demo_only"),
        ]


class ReorderRequest(models.Model):
    """The warehouse asks purchasing to order an item that is running low; purchasing orders it or says why not."""

    class Status(models.TextChoices):
        SENT = "sent", "Waiting for purchasing"
        ORDERED = "ordered", "Ordered"
        DECLINED = "declined", "Not ordered"
        CANCELLED = "cancelled", "Withdrawn"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="reorder_requests")
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, related_name="reorder_requests")
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name="reorder_requests")
    supplier = models.ForeignKey("SyntheticCounterparty", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="reorder_requests")
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SENT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reorder_requests_sent")
    requested_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="reorder_requests_decided")
    decided_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=200, blank=True)
    commitment = models.ForeignKey(PurchaseCommitment, null=True, blank=True, on_delete=models.PROTECT, related_name="reorder_requests")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-requested_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["business_unit", "variant"], condition=Q(status="sent"), name="one_open_reorder_request"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="reorder_request_quantity_positive"),
            models.CheckConstraint(condition=Q(status="sent", decided_by__isnull=True) | (~Q(status="sent") & Q(decided_by__isnull=False)),
                                   name="reorder_request_decision_recorded"),
            models.CheckConstraint(condition=~Q(status="declined") | ~Q(reason=""), name="reorder_request_decline_reason"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="reorder_request_demo_only"),
        ]


class CatalogueImport(models.Model):
    """A supplier catalogue or price list read by AI: draft items a person checks before any are created."""

    class Status(models.TextChoices):
        REVIEW = "review", "Waiting for review"
        DONE = "done", "Items created"
        DISCARDED = "discarded", "Discarded"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="catalogue_imports")
    supplier = models.ForeignKey("SyntheticCounterparty", on_delete=models.PROTECT, related_name="catalogue_imports")
    file_name = models.CharField(max_length=200)
    pages_read = models.CharField(max_length=20, blank=True)
    code_prefix = models.CharField(max_length=6)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.REVIEW)
    # One entry per item the AI found: its suggested fields, notes and the cut-out photo's stored name.
    drafts = models.JSONField(default=list)
    summary = models.CharField(max_length=500, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="catalogue_imports")
    created_at = models.DateTimeField(default=timezone.now)
    finished_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="catalogue_imports_finished")
    finished_at = models.DateTimeField(null=True, blank=True)
    created_items = models.ManyToManyField(Item, blank=True, related_name="catalogue_imports")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="catalogue_import_demo_only"),
        ]


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
    # Not a data source: permission to download or share what the user can already see.
    EXPORT = "export", "Export reports (Excel and Word)"


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
        PDF = "pdf", "PDF document"

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


class ReportDashboardLayout(models.Model):
    """Which assigned panels a user shows on the visual dashboard, and in what order.

    Presentation only: a panel listed here still needs its panel grant and data
    grants every time it is shown, and a panel that was revoked drops out.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="report_dashboard_layout"
    )
    panels = models.JSONField(default=list)
    updated_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="report_dashboard_layout_demo_only"),
        ]


class ReportShareLink(models.Model):
    """A link that opens a report with saved filters for another signed-in user.

    The link stores filters only, never results. Whoever opens it sees the report
    through their own panel and data grants, so it can never widen access. Only a
    hash of the token is kept; the token itself is shown once to its creator.
    """

    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="report_share_links"
    )
    panel = models.CharField(max_length=32, choices=ReportingPanel.choices)
    filters = models.JSONField(default=dict)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    open_count = models.PositiveIntegerField(default=0)
    last_opened_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="report_share_link_demo_only"),
            models.CheckConstraint(condition=Q(expires_at__gt=F("created_at")), name="report_share_link_expires_after_creation"),
        ]


class ReportShareLinkOpen(models.Model):
    """Append-only record of each time a share link was opened, and by whom."""

    link = models.ForeignKey(ReportShareLink, on_delete=models.PROTECT, related_name="opens")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="report_share_opens")
    allowed = models.BooleanField()
    opened_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-opened_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="report_share_open_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Share link open records are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Share link open records are append-only.")


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
    # Suppliers brought over at go-live are approved straight away; their documents must follow by this date,
    # or the supplier is frozen until they are added.
    documents_due = models.DateField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-opened_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["cr_number"], name="supplier_kyc_cr_unique"),
            models.CheckConstraint(condition=Q(documents_due__isnull=True) | Q(status="approved"),
                                   name="supplier_kyc_documents_due_when_approved"),
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


class StockCount(models.Model):
    """A count of one location.

    Counters enter what they find; the warehouse manager reviews the live
    differences and confirms them; each difference then needs an action
    (write-off, found, or adjustment) before the count is closed.
    """

    class Status(models.TextChoices):
        COUNTING = "counting", "Counting"
        SUBMITTED = "submitted", "Waiting for review"
        DIFFERENCES = "differences", "Differences waiting for action"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="stock_counts")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="stock_counts")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.COUNTING)
    note = models.CharField(max_length=240, blank=True)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_counts_opened")
    opened_at = models.DateTimeField(default=timezone.now)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="stock_counts_submitted",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="stock_counts_decided",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=240, blank=True)
    posting = models.OneToOneField(
        IdempotencyRecord, on_delete=models.PROTECT, null=True, blank=True, related_name="stock_count",
    )
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-opened_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["business_unit", "location"],
                condition=Q(status__in=["counting", "submitted"]),
                name="stock_count_one_open_per_location",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["differences", "closed"], decided_by__isnull=False, posting__isnull=False)
                | ~Q(status__in=["differences", "closed"]) & Q(posting__isnull=True),
                name="stock_count_posted_when_confirmed",
            ),
            models.CheckConstraint(
                condition=~Q(decided_by=models.F("opened_by")) | ~Q(status__in=["differences", "closed"]),
                name="stock_count_approver_not_opener",
            ),
            models.CheckConstraint(
                condition=~Q(decided_by=models.F("submitted_by")) | ~Q(status__in=["differences", "closed"]),
                name="stock_count_approver_not_submitter",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="stock_count_demo_only"),
        ]


class StockCountAction(models.TextChoices):
    WRITE_OFF = "write_off", "Write off"
    FOUND = "found", "Found, move back"
    ACCEPT = "accept", "Accept surplus into stock"


class StockCountActionStatus(models.TextChoices):
    REQUESTED = "requested", "Waiting for Finance"
    DONE = "done", "Done"


class StockCountLine(models.Model):
    count = models.ForeignKey(StockCount, on_delete=models.PROTECT, related_name="lines")
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, related_name="stock_count_lines")
    expected_quantity = models.DecimalField(max_digits=14, decimal_places=2)
    counted_quantity = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    counted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="stock_count_lines",
    )
    counted_at = models.DateTimeField(null=True, blank=True)
    found_during_count = models.BooleanField(default=False)
    posted_quantity = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    moved_to_missing = models.DecimalField(max_digits=14, decimal_places=2, default="0.00")
    surplus_quantity = models.DecimalField(max_digits=14, decimal_places=2, default="0.00")
    surplus_value = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    # Each confirmed difference needs an action before the count closes:
    # a shortage is written off (Finance approves) or found and moved back;
    # a surplus is accepted into stock at average cost (Finance approves).
    action = models.CharField(max_length=12, choices=StockCountAction.choices, blank=True)
    action_status = models.CharField(max_length=10, choices=StockCountActionStatus.choices, blank=True)
    action_note = models.CharField(max_length=240, blank=True)
    action_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="stock_count_actions_requested",
    )
    action_requested_at = models.DateTimeField(null=True, blank=True)
    action_decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="stock_count_actions_decided",
    )
    action_decided_at = models.DateTimeField(null=True, blank=True)
    action_decision_note = models.CharField(max_length=240, blank=True)
    action_posting = models.OneToOneField(
        IdempotencyRecord, on_delete=models.PROTECT, null=True, blank=True, related_name="stock_count_line_action",
    )
    written_off_value = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["variant__sku", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["count", "variant"], name="stock_count_line_unique_variant"),
            models.CheckConstraint(condition=Q(expected_quantity__gte=0), name="stock_count_expected_nonnegative"),
            models.CheckConstraint(
                condition=Q(counted_quantity__isnull=True) | Q(counted_quantity__gte=0),
                name="stock_count_counted_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(moved_to_missing__gte=0, surplus_quantity__gte=0, surplus_value__gte=0),
                name="stock_count_posting_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(action="", action_status="") | ~Q(action="") & ~Q(action_status=""),
                name="stock_count_line_action_has_status",
            ),
            models.CheckConstraint(
                condition=~Q(action_status="done") | Q(action_decided_by__isnull=False, action_posting__isnull=False),
                name="stock_count_line_action_posted_when_done",
            ),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="stock_count_line_demo_only"),
        ]


class StockCountEvent(models.Model):
    count = models.ForeignKey(StockCount, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_count_events")
    event = models.CharField(max_length=24)
    note = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [models.CheckConstraint(condition=Q(is_synthetic=True), name="stock_count_event_demo_only")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Stock count history is append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Stock count history is append-only.")


class CustomerCreditNoteSequence(models.Model):
    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="credit_note_sequences")
    year = models.PositiveSmallIntegerField()
    last_number = models.PositiveIntegerField(default=0)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business_unit", "year"], name="credit_note_sequence_once_per_year"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="credit_note_sequence_demo_only"),
        ]


class CustomerCreditNote(models.Model):
    """The credit note Finance issues when it approves a customer return.

    The credit first reduces what the customer still owes on the invoice
    (applied_to_balance); the rest is refunded in cash or by card. Approving it
    puts the returned goods back into stock at their original cost and posts the
    sales return journal. Immutable once issued.
    """

    class RefundMethod(models.TextChoices):
        CASH = "cash", "Cash"
        CARD = "card", "Card"

    idempotency = models.OneToOneField(
        IdempotencyRecord, on_delete=models.PROTECT, related_name="customer_credit_note"
    )
    intake = models.OneToOneField(SalesReturnIntake, on_delete=models.PROTECT, related_name="credit_note")
    invoice = models.ForeignKey(CustomerInvoice, on_delete=models.PROTECT, related_name="credit_notes")
    customer = models.ForeignKey(SyntheticCounterparty, on_delete=models.PROTECT, related_name="credit_notes")
    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="credit_notes")
    number = models.CharField(max_length=40, unique=True)
    issued_at = models.DateTimeField(default=timezone.now)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="credit_notes_issued")
    currency = models.CharField(max_length=3, default="QAR")
    lines = models.JSONField(help_text="Returned quote lines with quantity, sales value and original cost.")
    returned_value = models.DecimalField(max_digits=16, decimal_places=2)
    returned_cost = models.DecimalField(max_digits=16, decimal_places=2)
    credit_total = models.DecimalField(max_digits=16, decimal_places=2)
    applied_to_balance = models.DecimalField(max_digits=16, decimal_places=2)
    refunded_amount = models.DecimalField(max_digits=16, decimal_places=2)
    refund_method = models.CharField(max_length=8, choices=RefundMethod.choices)
    note = models.CharField(max_length=240, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-issued_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(currency="QAR"), name="credit_note_qar_only"),
            models.CheckConstraint(condition=Q(credit_total__gt=0), name="credit_note_positive"),
            models.CheckConstraint(
                condition=Q(applied_to_balance__gte=0, refunded_amount__gte=0, returned_cost__gte=0),
                name="credit_note_parts_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(credit_total=F("applied_to_balance") + F("refunded_amount")),
                name="credit_note_parts_add_up",
            ),
            models.CheckConstraint(condition=Q(credit_total__lte=F("returned_value")), name="credit_note_within_returned_value"),
            models.CheckConstraint(condition=Q(refund_method__in=["cash", "card"]), name="credit_note_refund_method"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="credit_note_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Credit notes cannot be changed once issued.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Credit notes cannot be deleted.")


class ClosedMonth(models.Model):
    """An accounting month that Finance has closed for one business unit.

    While the row exists, no journal entry dated in that month can be posted for
    the unit: the posting service refuses it and a database trigger refuses it
    too. Reopening deletes the row. Every close and reopen is kept in
    MonthCloseEvent.
    """

    legal_entity = models.ForeignKey(LegalEntity, on_delete=models.PROTECT, related_name="closed_months")
    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="closed_months")
    month = models.DateField(help_text="First day of the closed month.")
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="months_closed")
    closed_at = models.DateTimeField(default=timezone.now)
    entry_count = models.PositiveIntegerField()
    total_debit = models.DecimalField(max_digits=16, decimal_places=2)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["business_unit", "month"]
        constraints = [
            models.UniqueConstraint(fields=["business_unit", "month"], name="closed_month_once_per_unit"),
            models.CheckConstraint(condition=Q(month__day=1), name="closed_month_first_day"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="closed_month_demo_only"),
        ]


class MonthCloseEvent(models.Model):
    class Action(models.TextChoices):
        CLOSE = "close", "Closed"
        REOPEN = "reopen", "Reopened"

    legal_entity = models.ForeignKey(LegalEntity, on_delete=models.PROTECT, related_name="month_close_events")
    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="month_close_events")
    month = models.DateField()
    action = models.CharField(max_length=8, choices=Action.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="month_close_events")
    reason = models.CharField(max_length=240, blank=True)
    entry_count = models.PositiveIntegerField()
    total_debit = models.DecimalField(max_digits=16, decimal_places=2)
    checklist = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=~Q(action="reopen") | ~Q(reason=""), name="month_reopen_has_reason"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="month_close_event_demo_only"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Month close history is append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Month close history is append-only.")


class ManualJournal(models.Model):
    """A journal entry typed in by Finance (a correction, an accrual or a prepayment).

    One person prepares it and a different person approves it; only then is it posted.
    An accrual can be set to undo itself on the first day of the next month.
    A posted journal is never changed; it is reversed by a new journal instead.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Waiting for approval"
        POSTED = "posted", "Posted"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="manual_journals")
    number = models.CharField(max_length=32, unique=True)
    entry_date = models.DateField()
    description = models.CharField(max_length=200)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    reverse_next_month = models.BooleanField(default=False)
    reversal_of = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by")
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    prepared_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    returned_reason = models.CharField(max_length=200, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    journal_entry = models.OneToOneField(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    auto_reversal_entry = models.OneToOneField(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-entry_date", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="manual_journal_demo_only"),
            models.CheckConstraint(
                condition=Q(status="posted", journal_entry__isnull=False, approved_by__isnull=False)
                | (~Q(status="posted") & Q(journal_entry__isnull=True)),
                name="manual_journal_posted_has_entry",
            ),
        ]


class ManualJournalLine(models.Model):
    journal = models.ForeignKey(ManualJournal, on_delete=models.CASCADE, related_name="lines")
    account_code = models.CharField(max_length=32)
    debit = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    credit = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    memo = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(debit__gt=0, credit=0) | Q(credit__gt=0, debit=0),
                                   name="manual_journal_line_one_side"),
        ]


class ManualJournalEvent(models.Model):
    journal = models.ForeignKey(ManualJournal, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    at = models.DateTimeField(default=timezone.now)
    change = models.CharField(max_length=32)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-at", "-pk"]


class AdjustmentNote(models.Model):
    """A credit or debit note that changes what a customer owes us or what we owe a supplier, without goods moving.

    Customer credit notes lower what is still owed on one invoice (price reductions,
    billing errors, goodwill); returned goods keep using CustomerCreditNote. On the
    supplier side a note is tied to the goods on one purchase receipt: the supplier's
    credit note or our debit note lowers what we owe, the supplier's debit note raises
    it. Amounts are in the document's currency (the receipt's currency for suppliers)
    and value is the QAR at the receipt rate. One person prepares a note and a
    different person approves it; approval posts the journal. Never changed after.
    """

    class Kind(models.TextChoices):
        CUSTOMER_CREDIT = "customer_credit", "Credit note to a customer"
        SUPPLIER_CREDIT = "supplier_credit", "Credit note from a supplier"
        OUR_DEBIT = "our_debit", "Our debit note to a supplier"
        SUPPLIER_DEBIT = "supplier_debit", "Debit note from a supplier"

    class Reason(models.TextChoices):
        PRICE = "price", "Price agreed after the sale or purchase"
        DAMAGED = "damaged", "Damaged or faulty goods kept"
        SHORT = "short", "Less delivered than billed"
        ERROR = "error", "Billing mistake"
        GOODWILL = "goodwill", "Goodwill gesture"
        EXTRA = "extra", "Extra charge agreed"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Waiting for approval"
        APPROVED = "approved", "Approved and posted"
        REJECTED = "rejected", "Rejected"

    LOWERS = ("customer_credit", "supplier_credit", "our_debit")

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="adjustment_notes")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    counterparty = models.ForeignKey(SyntheticCounterparty, on_delete=models.PROTECT, related_name="adjustment_notes")
    invoice = models.ForeignKey(CustomerInvoice, null=True, blank=True, on_delete=models.PROTECT, related_name="adjustment_notes")
    receipt = models.ForeignKey(PurchaseReceipt, null=True, blank=True, on_delete=models.PROTECT, related_name="adjustment_notes")
    number = models.CharField(max_length=40, blank=True, help_text="Our number, given on approval to notes we issue.")
    their_reference = models.CharField(max_length=40, blank=True, help_text="The supplier's own note number.")
    note_date = models.DateField()
    reason = models.CharField(max_length=10, choices=Reason.choices)
    description = models.CharField(max_length=200)
    currency = models.CharField(max_length=3, default="QAR")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    rate = models.DecimalField(max_digits=18, decimal_places=8, default=1)
    value = models.DecimalField(max_digits=16, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SUBMITTED)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    prepared_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)
    journal_entry = models.OneToOneField(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-note_date", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="adjustment_note_demo_only"),
            models.CheckConstraint(condition=Q(amount__gt=0, value__gt=0), name="adjustment_note_positive"),
            models.CheckConstraint(
                condition=Q(kind="customer_credit", invoice__isnull=False, receipt__isnull=True, currency="QAR")
                | (~Q(kind="customer_credit") & Q(invoice__isnull=True, receipt__isnull=False)),
                name="adjustment_note_one_document",
            ),
            models.CheckConstraint(
                condition=Q(status="approved", journal_entry__isnull=False, decided_by__isnull=False)
                | Q(status="rejected", journal_entry__isnull=True, decided_by__isnull=False)
                | Q(status="submitted", journal_entry__isnull=True, decided_by__isnull=True),
                name="adjustment_note_decision_recorded",
            ),
            models.CheckConstraint(condition=Q(decided_by__isnull=True) | ~Q(decided_by=F("prepared_by")),
                                   name="adjustment_note_second_person"),
        ]


class DebitNoteSequence(models.Model):
    """Gap-free numbering of the debit notes we send suppliers, per business unit and year."""

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="debit_note_sequences")
    year = models.PositiveSmallIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business_unit", "year"], name="debit_note_sequence_once_per_year")]


class SupplierAdvance(models.Model):
    """Money paid to a supplier before their bill, for example a deposit on an order.

    One person prepares it and a different person approves it, which posts it from
    the bank to advances paid to suppliers. Later it is set against the goods on
    the supplier's receipts: each use is a supplier payment whose money comes from
    the advance instead of the bank. Amounts are in the advance's currency; value is
    QAR at the rate on the day it was paid.
    """

    class Status(models.TextChoices):
        REQUESTED = "requested", "Waiting for approval"
        APPROVED = "approved", "Approved and paid"
        REJECTED = "rejected", "Rejected"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="supplier_advances")
    supplier = models.ForeignKey(SyntheticCounterparty, on_delete=models.PROTECT, related_name="supplier_advances")
    number = models.CharField(max_length=40, unique=True)
    paid_on = models.DateField()
    purpose = models.CharField(max_length=200)
    currency = models.CharField(max_length=3, default="QAR")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=8, default=1)
    rate_source = models.CharField(max_length=120, blank=True)
    rate_date = models.DateField(null=True, blank=True)
    value = models.DecimalField(max_digits=16, decimal_places=2)
    bank_reference = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REQUESTED)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    prepared_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)
    journal_entry = models.OneToOneField(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-paid_on", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="supplier_advance_demo_only"),
            models.CheckConstraint(condition=Q(amount__gt=0, value__gt=0), name="supplier_advance_positive"),
            models.CheckConstraint(
                condition=Q(status="approved", journal_entry__isnull=False, decided_by__isnull=False)
                | Q(status="rejected", journal_entry__isnull=True, decided_by__isnull=False)
                | Q(status="requested", journal_entry__isnull=True, decided_by__isnull=True),
                name="supplier_advance_decision_recorded",
            ),
            models.CheckConstraint(condition=Q(decided_by__isnull=True) | ~Q(decided_by=F("prepared_by")),
                                   name="supplier_advance_second_person"),
            *_exchange_rate_constraints("supplier_advance"),
        ]


class Cheque(models.Model):
    """A cheque received from a customer or written to a supplier, usually dated ahead (post-dated).

    A cheque received is kept off the books while it is held and deposited. When the bank clears it,
    it is recorded as the customer's payment on their order and lands in the bank account it was
    deposited to. A bounced cheque changes nothing in the books: the customer still owes the money.
    A cheque written is a list of what will leave each bank account on which day; the payment itself
    is booked through supplier payments.
    """

    class Direction(models.TextChoices):
        RECEIVED = "received", "Received from a customer"
        ISSUED = "issued", "Written to a supplier or other payee"

    class Status(models.TextChoices):
        HELD = "held", "Held, not yet due"
        DEPOSITED = "deposited", "Deposited, waiting for the bank"
        CLEARED = "cleared", "Cleared"
        BOUNCED = "bounced", "Bounced"
        RETURNED = "returned", "Returned to the customer"
        ISSUED = "issued", "Written, not yet cashed"
        CANCELLED = "cancelled", "Cancelled or stopped"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="cheques")
    direction = models.CharField(max_length=8, choices=Direction.choices)
    number = models.CharField(max_length=20)
    drawn_on = models.CharField(max_length=80, blank=True)
    account = models.ForeignKey(CashAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="cheques")
    customer = models.ForeignKey(SyntheticCounterparty, null=True, blank=True, on_delete=models.PROTECT, related_name="cheques")
    quote = models.ForeignKey(SalesQuote, null=True, blank=True, on_delete=models.PROTECT, related_name="cheques")
    payee = models.CharField(max_length=120, blank=True)
    supplier_payment = models.ForeignKey(SupplierPayment, null=True, blank=True, on_delete=models.PROTECT, related_name="cheques")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    cheque_date = models.DateField()
    received_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices)
    note = models.CharField(max_length=200, blank=True)
    replaces = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="replaced_by")
    payment = models.OneToOneField(QuotePayment, null=True, blank=True, on_delete=models.PROTECT, related_name="cheque")
    deposited_on = models.DateField(null=True, blank=True)
    finished_on = models.DateField(null=True, blank=True)
    reason = models.CharField(max_length=200, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(default=timezone.now)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["cheque_date", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="cheque_demo_only"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="cheque_positive"),
            models.CheckConstraint(condition=Q(direction="received", customer__isnull=False, quote__isnull=False)
                                   | Q(direction="issued", customer__isnull=True, quote__isnull=True) & ~Q(payee=""),
                                   name="cheque_party"),
            models.CheckConstraint(condition=Q(direction="received", status__in=["held", "deposited", "cleared", "bounced", "returned"])
                                   | Q(direction="issued", status__in=["issued", "cleared", "cancelled"]), name="cheque_status_fits"),
            models.CheckConstraint(condition=~Q(status="cleared", direction="received") | Q(payment__isnull=False, account__isnull=False),
                                   name="cheque_cleared_is_paid"),
            models.CheckConstraint(condition=Q(direction="received", received_on__isnull=False) | Q(direction="issued", account__isnull=False),
                                   name="cheque_received_date_or_account"),
            models.UniqueConstraint(fields=["direction", "account", "number"], condition=Q(direction="issued"),
                                    name="cheque_issued_number_unique"),
        ]


class CashForecastItem(models.Model):
    """A planned receipt or payment that the cash-flow forecast cannot see in the books yet, such as rent or salaries."""

    class Direction(models.TextChoices):
        IN = "in", "Money in"
        OUT = "out", "Money out"

    class Repeat(models.TextChoices):
        ONCE = "once", "Once"
        WEEKLY = "weekly", "Every week"
        MONTHLY = "monthly", "Every month"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="cash_forecast_items")
    name = models.CharField(max_length=120)
    direction = models.CharField(max_length=3, choices=Direction.choices)
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    first_date = models.DateField()
    repeat = models.CharField(max_length=8, choices=Repeat.choices, default=Repeat.ONCE)
    until = models.DateField(null=True, blank=True)
    # The amount is in this currency; the forecast counts it in QAR at the latest rate the books used.
    currency = models.CharField(max_length=3, default="QAR")
    # The bank account it is paid from or into; empty means the unit's default bank account.
    cash_account = models.ForeignKey("CashAccount", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    removed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    removed_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["first_date", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="cash_forecast_item_demo_only"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="cash_forecast_item_positive"),
            models.CheckConstraint(condition=Q(until__isnull=True) | Q(until__gte=F("first_date")),
                                   name="cash_forecast_item_until_after_start"),
        ]


class BankTransferLimit(models.Model):
    """How much a bank lets one business unit send abroad in foreign currency on one working day, in QAR."""

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="bank_transfer_limits")
    bank_name = models.CharField(max_length=80)
    daily_limit = models.DecimalField(max_digits=16, decimal_places=2)
    set_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    set_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["business_unit", "bank_name"]
        constraints = [
            models.UniqueConstraint("business_unit", Lower("bank_name"), name="bank_transfer_limit_one_per_bank"),
            models.CheckConstraint(condition=Q(daily_limit__gt=0), name="bank_transfer_limit_positive"),
            models.CheckConstraint(condition=~Q(bank_name=""), name="bank_transfer_limit_named"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="bank_transfer_limit_demo_only"),
        ]


class CostShare(models.Model):
    """Part of a cost booked in one business unit, moved to other units of the same company (for example shared rent).

    One person prepares it and a different person approves it. Approval posts one journal in the unit that paid
    (the cost leaves it) and one in each receiving unit (the cost arrives), through a sharing account that nets to zero.
    """

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Waiting for approval"
        APPROVED = "approved", "Shared"
        REJECTED = "rejected", "Rejected"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="cost_shares")
    number = models.CharField(max_length=32, unique=True)
    share_date = models.DateField()
    account_code = models.CharField(max_length=32)
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    kept_percent = models.DecimalField(max_digits=5, decimal_places=2)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SUBMITTED)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    prepared_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)
    journal_entry = models.OneToOneField(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-share_date", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="cost_share_demo_only"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="cost_share_positive"),
            models.CheckConstraint(condition=Q(kept_percent__gte=0, kept_percent__lt=100), name="cost_share_kept_percent"),
            models.CheckConstraint(
                condition=Q(status="approved", journal_entry__isnull=False, decided_by__isnull=False)
                | Q(status="rejected", journal_entry__isnull=True, decided_by__isnull=False)
                | Q(status="submitted", journal_entry__isnull=True, decided_by__isnull=True),
                name="cost_share_decision_recorded",
            ),
            models.CheckConstraint(condition=Q(decided_by__isnull=True) | ~Q(decided_by=F("prepared_by")),
                                   name="cost_share_second_person"),
        ]


class CostShareLine(models.Model):
    """One receiving unit's part of a shared cost."""

    share = models.ForeignKey(CostShare, on_delete=models.CASCADE, related_name="lines")
    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="received_cost_shares")
    percent = models.DecimalField(max_digits=5, decimal_places=2)
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    journal_entry = models.OneToOneField(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(percent__gt=0, percent__lte=100), name="cost_share_line_percent"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="cost_share_line_positive"),
            models.UniqueConstraint(fields=["share", "business_unit"], name="cost_share_line_one_per_unit"),
        ]


class Employee(models.Model):
    """A member of staff: their job, where they work and their contract. Pay is kept apart, under payroll."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Working"
        NOTICE = "notice", "Serving notice"
        LEFT = "left", "Left"

    class Contract(models.TextChoices):
        UNLIMITED = "unlimited", "Open-ended"
        LIMITED = "limited", "Fixed term"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="employees")
    staff_number = models.CharField(max_length=20, unique=True)
    full_name = models.CharField(max_length=120)
    arabic_name = models.CharField(max_length=120, blank=True)
    job_title = models.CharField(max_length=80)
    department = models.CharField(max_length=60)
    grade = models.CharField(max_length=20, blank=True)
    manager = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reports")
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="employee")
    nationality = models.CharField(max_length=60)
    date_of_birth = models.DateField(null=True, blank=True)
    work_email = models.EmailField(blank=True)
    mobile = models.CharField(max_length=20, blank=True)
    joined_on = models.DateField()
    contract = models.CharField(max_length=10, choices=Contract.choices, default=Contract.UNLIMITED)
    contract_end = models.DateField(null=True, blank=True)
    probation_end = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ACTIVE)
    last_day = models.DateField(null=True, blank=True)
    work_schedule = models.ForeignKey("WorkSchedule", null=True, blank=True, on_delete=models.PROTECT, related_name="people")
    leaving_reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["full_name", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="employee_demo_only"),
            models.CheckConstraint(condition=Q(contract="unlimited", contract_end__isnull=True)
                                   | Q(contract="limited", contract_end__isnull=False), name="employee_contract_end"),
            models.CheckConstraint(condition=Q(contract_end__isnull=True) | Q(contract_end__gt=F("joined_on")),
                                   name="employee_contract_after_joining"),
            models.CheckConstraint(condition=Q(status="active", last_day__isnull=True)
                                   | (Q(status__in=["notice", "left"]) & Q(last_day__isnull=False)), name="employee_last_day"),
            models.CheckConstraint(condition=Q(last_day__isnull=True) | Q(last_day__gte=F("joined_on")),
                                   name="employee_last_day_after_joining"),
            models.CheckConstraint(condition=~Q(manager=F("pk")), name="employee_not_own_manager"),
        ]


class EmployeeDocument(models.Model):
    """An identity or work document of an employee, such as the QID or passport, with its expiry date."""

    class Kind(models.TextChoices):
        QID = "qid", "Qatar ID (QID)"
        PASSPORT = "passport", "Passport"
        RESIDENCE = "residence", "Residence permit"
        WORK_PERMIT = "work_permit", "Work permit"
        HEALTH_CARD = "health_card", "Health card"
        DRIVING = "driving", "Driving licence"
        OTHER = "other", "Other"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="documents")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    number = models.CharField(max_length=40)
    issued_by = models.CharField(max_length=60, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=120, blank=True)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    added_at = models.DateTimeField(default=timezone.now)
    removed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    removed_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["kind", "-added_at"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="employee_document_demo_only"),
            models.CheckConstraint(condition=~Q(kind="qid") | Q(number__regex=r"^[23][0-9]{10}$"), name="employee_document_qid_format"),
        ]


class EmployeeEvent(models.Model):
    """Who changed a staff record, when, and what changed. Append-only."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    at = models.DateTimeField(default=timezone.now)
    change = models.CharField(max_length=24)
    what = models.CharField(max_length=400, blank=True)

    class Meta:
        ordering = ["-at", "-pk"]


class LeaveRequest(models.Model):
    """Time off asked for by or for an employee. Someone other than the person who asked approves it."""

    class Kind(models.TextChoices):
        ANNUAL = "annual", "Annual leave"
        SICK = "sick", "Sick leave"
        UNPAID = "unpaid", "Unpaid leave"

    class Status(models.TextChoices):
        REQUESTED = "requested", "Waiting for approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="leave_requests")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    start = models.DateField()
    end = models.DateField()
    days = models.PositiveSmallIntegerField()
    reason = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REQUESTED)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    requested_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # Set when a colleague at the same level is away too close: the line manager and then HR must both approve.
    overlap_note = models.CharField(max_length=300, blank=True)
    manager_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                            related_name="+")
    manager_approved_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-start", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="leave_request_demo_only"),
            models.CheckConstraint(condition=Q(end__gte=F("start")), name="leave_request_end_after_start"),
            models.CheckConstraint(condition=Q(days__gt=0), name="leave_request_has_days"),
            models.CheckConstraint(condition=Q(decided_by__isnull=True) | ~Q(decided_by=F("requested_by")),
                                   name="leave_request_second_person"),
            models.CheckConstraint(condition=Q(status="requested", decided_by__isnull=True)
                                   | Q(status__in=["approved", "rejected"], decided_by__isnull=False)
                                   | Q(status="cancelled", cancelled_by__isnull=False), name="leave_request_decision_recorded"),
        ]


class LeaveAdjustment(models.Model):
    """Annual leave days added or taken away by HR, such as days carried over from last year."""

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="leave_adjustments")
    year = models.PositiveSmallIntegerField()
    days = models.DecimalField(max_digits=5, decimal_places=1)
    reason = models.CharField(max_length=200)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    added_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-added_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="leave_adjustment_demo_only"),
            models.CheckConstraint(condition=~Q(days=0), name="leave_adjustment_not_zero"),
        ]


class AttendanceRecord(models.Model):
    """One person's attendance on one working day, recorded by their manager or HR."""

    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        LATE = "late", "Late"
        ABSENT = "absent", "Absent"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="attendance")
    day = models.DateField()
    status = models.CharField(max_length=8, choices=Status.choices)
    check_in = models.TimeField(null=True, blank=True)
    check_out = models.TimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    # A late arrival or early leaving that the manager or HR accepted does not count against the person.
    excused = models.BooleanField(default=False)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["day", "employee_id"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="attendance_demo_only"),
            models.UniqueConstraint(fields=["employee", "day"], name="attendance_one_per_day"),
            models.CheckConstraint(condition=Q(check_in__isnull=True) | Q(check_out__isnull=True) | Q(check_out__gt=F("check_in")),
                                   name="attendance_out_after_in"),
            models.CheckConstraint(condition=~Q(status="absent") | Q(check_in__isnull=True, check_out__isnull=True),
                                   name="attendance_absent_has_no_times"),
        ]


class WorkSchedule(models.Model):
    """A set of working hours in a unit, such as Workshop/Warehouse or Showroom evening shift, set by HR.

    Each person is given one; attendance uses it to count minutes late or left early."""

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="work_schedules")
    name = models.CharField(max_length=60)
    starts_at = models.TimeField()
    ends_at = models.TimeField()
    grace_minutes = models.PositiveSmallIntegerField(default=0)
    set_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    set_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["starts_at", "name"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="working_hours_demo_only"),
            models.UniqueConstraint(fields=["business_unit", "name"], name="working_hours_name_once_per_unit"),
            models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="working_hours_end_after_start"),
            models.CheckConstraint(condition=Q(grace_minutes__lte=60), name="working_hours_grace_range"),
        ]


class EmployeePay(models.Model):
    """A person's monthly pay from a given month on. Append-only: a change is a new row."""

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="pay")
    effective_from = models.DateField()
    basic = models.DecimalField(max_digits=10, decimal_places=2)
    housing = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    transport = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    mobile = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    other = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    other_label = models.CharField(max_length=60, blank=True)
    bank_name = models.CharField(max_length=80, blank=True)
    iban = models.CharField(max_length=29, blank=True)
    reason = models.CharField(max_length=200)
    set_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    set_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-effective_from", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="employee_pay_demo_only"),
            models.CheckConstraint(condition=Q(effective_from__day=1), name="employee_pay_from_first_of_month"),
            models.CheckConstraint(condition=Q(basic__gt=0, housing__gte=0, transport__gte=0, mobile__gte=0, other__gte=0),
                                   name="employee_pay_amounts"),
            models.CheckConstraint(condition=Q(iban="") | Q(iban__regex=r"^QA[0-9]{2}[A-Z]{4}[A-Z0-9]{21}$"),
                                   name="employee_pay_qatar_iban"),
            models.CheckConstraint(condition=Q(other=0) | ~Q(other_label=""), name="employee_pay_other_named"),
        ]


class WpsSettings(models.Model):
    """What the unit's WPS salary file needs about the employer and the account that pays."""

    business_unit = models.OneToOneField(BusinessUnit, on_delete=models.PROTECT, related_name="wps_settings")
    employer_eid = models.CharField(max_length=8)
    payer_bank = models.CharField(max_length=4)
    payer_iban = models.CharField(max_length=29)
    set_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    set_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="wps_settings_demo_only"),
            models.CheckConstraint(condition=Q(employer_eid__regex=r"^[0-9]{7,8}$"), name="wps_settings_eid"),
            models.CheckConstraint(condition=Q(payer_iban__regex=r"^QA[0-9]{2}[A-Z]{4}[A-Z0-9]{21}$"), name="wps_settings_iban"),
        ]


class LetterSettings(models.Model):
    """How a unit's staff letters look: the company lines at the top and the preset signature at the bottom."""

    business_unit = models.OneToOneField(BusinessUnit, on_delete=models.PROTECT, related_name="letter_settings")
    company_name = models.CharField(max_length=120)
    address = models.CharField(max_length=200, blank=True)
    signatory_name = models.CharField(max_length=120)
    signatory_title = models.CharField(max_length=80)
    signature_png = models.BinaryField(blank=True, default=b"")
    hr_checks_first = models.BooleanField(default=False)
    set_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    set_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(is_synthetic=True), name="letter_settings_demo_only")]


class StaffLetter(models.Model):
    """A letter about someone's employment, such as a salary certificate, filled from their records and signed."""

    class Kind(models.TextChoices):
        BANK = "bank", "Bank letter (account or credit)"
        LOAN = "loan", "Loan certificate (financing)"
        TO_WHOM = "to_whom", "To whom it may concern"
        EMBASSY = "embassy", "Embassy letter"
        FAMILY = "family", "Letter for family residence"
        POLO = "polo", "POLO / OWWA letter (Filipino workers)"
        HOME_BANK = "home_bank", "Salary certificate for a bank at home"

    class Status(models.TextChoices):
        REQUESTED = "requested", "Waiting for HR"
        ISSUED = "issued", "Ready to download"
        REJECTED = "rejected", "Not issued"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="letters")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    addressee = models.CharField(max_length=120, blank=True)
    purpose = models.CharField(max_length=200, blank=True)
    include_salary = models.BooleanField(default=True)
    include_iban = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REQUESTED)
    number = models.CharField(max_length=30, blank=True)
    content = models.JSONField(default=dict, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    requested_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=200, blank=True)
    downloads = models.PositiveIntegerField(default=0)
    last_downloaded_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-requested_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="staff_letter_demo_only"),
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""), name="staff_letter_number_unique"),
            models.CheckConstraint(condition=Q(status="requested", decided_at__isnull=True, number="")
                                   | Q(status="issued", decided_at__isnull=False) & ~Q(number="")
                                   | Q(status="rejected", decided_by__isnull=False) & ~Q(rejection_reason=""),
                                   name="staff_letter_decision_recorded"),
        ]


class EndOfServiceSettlement(models.Model):
    """What is owed to someone who leaves: end of service pay, unused leave and anything else agreed, paid through payroll."""

    class Status(models.TextChoices):
        PREPARED = "prepared", "Waiting for approval"
        APPROVED = "approved", "Approved"
        CANCELLED = "cancelled", "Cancelled"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="+")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="settlements")
    last_day = models.DateField()
    pay_month = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PREPARED)
    service_days = models.PositiveIntegerField()
    unpaid_days = models.PositiveIntegerField(default=0)
    basic = models.DecimalField(max_digits=12, decimal_places=2)
    gratuity = models.DecimalField(max_digits=12, decimal_places=2)
    already_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    leave_days = models.DecimalField(max_digits=6, decimal_places=1, default=0)
    leave_pay = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    additions = models.JSONField(default=list)
    deductions = models.JSONField(default=list)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    detail = models.JSONField(default=dict)
    note = models.CharField(max_length=300, blank=True)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    prepared_at = models.DateTimeField(default=timezone.now)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=200, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-last_day", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="settlement_demo_only"),
            models.CheckConstraint(condition=Q(pay_month__day=1), name="settlement_pay_month_first"),
            models.CheckConstraint(condition=Q(total__gte=0, gratuity__gte=0, already_paid__gte=0), name="settlement_amounts"),
            models.UniqueConstraint(fields=["employee"], condition=~Q(status="cancelled"), name="settlement_one_live_per_person"),
            models.CheckConstraint(condition=Q(approved_by__isnull=True) | ~Q(approved_by=F("prepared_by")),
                                   name="settlement_second_person"),
            models.CheckConstraint(condition=Q(status="prepared", approved_by__isnull=True)
                                   | Q(status="approved", approved_by__isnull=False) | Q(status="cancelled", cancelled_by__isnull=False),
                                   name="settlement_decision_recorded"),
        ]


BALANCE_KINDS = ["opening_stock", "trial_balance", "customer_balances", "supplier_balances", "fixed_assets", "staff_loans"]


class ImportBatch(models.Model):
    """One go-live import file: checked row by row, then approved by a second person, who creates the records."""

    class Kind(models.TextChoices):
        CUSTOMERS = "customers", "Customers"
        SUPPLIERS = "suppliers", "Suppliers"
        ITEMS = "items", "Items"
        OPENING_STOCK = "opening_stock", "Opening stock"
        TRIAL_BALANCE = "trial_balance", "Opening trial balance"
        CUSTOMER_BALANCES = "customer_balances", "Customer balances"
        SUPPLIER_BALANCES = "supplier_balances", "Supplier balances"
        FIXED_ASSETS = "fixed_assets", "Fixed assets"
        STAFF_LOANS = "staff_loans", "Staff loans and advances"

    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting for approval"
        IMPORTED = "imported", "Imported"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.WAITING)
    # Opening stock and the opening trial balance are booked on this day (the day before go-live).
    balances_on = models.DateField(null=True, blank=True)
    file_name = models.CharField(max_length=200)
    file_sha256 = models.CharField(max_length=64)
    rows = models.JSONField(default=list)
    row_count = models.PositiveIntegerField()
    created_ids = models.JSONField(default=list)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    prepared_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=300, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-prepared_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="import_batch_demo_only"),
            models.CheckConstraint(condition=Q(row_count__gt=0), name="import_batch_has_rows"),
            models.CheckConstraint(condition=Q(decided_by__isnull=True) | ~Q(decided_by=F("prepared_by"))
                                   | Q(status="withdrawn"), name="import_batch_second_person"),
            models.CheckConstraint(condition=Q(status="waiting", decided_at__isnull=True) | (~Q(status="waiting") & Q(decided_at__isnull=False)),
                                   name="import_batch_decision_recorded"),
            models.CheckConstraint(condition=~Q(status="rejected") | ~Q(decision_note=""), name="import_batch_rejection_reason"),
            models.CheckConstraint(condition=Q(kind__in=BALANCE_KINDS, balances_on__isnull=False)
                                   | (~Q(kind__in=BALANCE_KINDS) & Q(balances_on__isnull=True)),
                                   name="import_batch_balances_date"),
        ]


class AlertEmail(models.Model):
    """One email alert to one person: things waiting for their approval, or documents about to expire."""

    class Kind(models.TextChoices):
        APPROVALS = "approvals", "Waiting for your approval"
        EXPIRING = "expiring", "Documents about to expire"
        PORTAL = "portal", "New on the HR Portal"

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Could not be sent"
        NO_EMAIL = "no_email", "No email address"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="alert_emails")
    to_address = models.CharField(max_length=254, blank=True)
    subject = models.CharField(max_length=200)
    body = models.TextField()
    # What the email was about, so the same thing is not sent to the same person twice.
    items = models.JSONField(default=list)
    status = models.CharField(max_length=10, choices=Status.choices)
    error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [models.CheckConstraint(condition=Q(is_synthetic=True), name="alert_email_demo_only")]


class AlertSent(models.Model):
    """That one thing has been included in an alert to one person (sent, or recorded without an address)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    key = models.CharField(max_length=120)
    email = models.ForeignKey(AlertEmail, on_delete=models.CASCADE, related_name="sent_items")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "key"], name="alert_sent_once")]


class AlertPreference(models.Model):
    """Which alerts a person is assigned to receive, and where. Without a row, they receive none."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="alert_preference")
    # Where the alerts go. Empty: the email address on the person's account.
    email = models.EmailField(max_length=254, blank=True)
    # The alert types the person receives (see alerts.TYPES).
    types = models.JSONField(default=list)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(default=timezone.now)


class DiscountCeiling(models.Model):
    """The largest discount a role may give on its own in a business unit. Without a row, the built-in default applies."""

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="discount_ceilings")
    role = models.CharField(max_length=20, choices=BusinessUnitApprovalRole.choices)
    percent = models.DecimalField(max_digits=5, decimal_places=2)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="discount_ceiling_demo_only"),
            models.CheckConstraint(condition=Q(percent__gte=0, percent__lte=100), name="discount_ceiling_percent"),
            models.UniqueConstraint(fields=["business_unit", "role"], name="discount_ceiling_once"),
        ]


class PersonDiscountLimit(models.Model):
    """The largest discount one person may give on their own when selling stock items in a business unit.

    It replaces the ceiling of the person's role for stock sales only. Without a row, the role ceiling applies.
    """

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="person_discount_limits")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="discount_limits")
    percent = models.DecimalField(max_digits=5, decimal_places=2)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="person_discount_demo_only"),
            models.CheckConstraint(condition=Q(percent__gte=0, percent__lte=50), name="person_discount_percent"),
            models.UniqueConstraint(fields=["business_unit", "user"], name="person_discount_once"),
        ]


class ApprovalLimit(models.Model):
    """The most, in QAR, one person may approve with one approval permission in a business unit. No row: no limit."""

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="approval_limits")
    action = models.CharField(max_length=32, choices=UserActionGrant.Action.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="approval_limits")
    max_amount = models.DecimalField(max_digits=16, decimal_places=2)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="approval_limit_demo_only"),
            models.CheckConstraint(condition=Q(max_amount__gte=0), name="approval_limit_not_negative"),
            models.UniqueConstraint(fields=["business_unit", "action", "user"], name="approval_limit_once"),
        ]


class OldBalance(models.Model):
    """An unpaid invoice or supplier bill brought over from the old system at go-live.

    A customer balance is what a customer still owed the unit on an old invoice
    (always in QAR); a supplier balance is what the unit still owed a supplier on
    an old bill, in the bill's currency, with its QAR value at go-live.
    """

    class Side(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        SUPPLIER = "supplier", "Supplier"

    batch = models.ForeignKey(ImportBatch, on_delete=models.PROTECT, related_name="old_balances")
    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="old_balances")
    side = models.CharField(max_length=8, choices=Side.choices)
    party = models.ForeignKey(SyntheticCounterparty, on_delete=models.PROTECT, related_name="old_balances")
    reference = models.CharField(max_length=60)
    document_date = models.DateField()
    currency = models.CharField(max_length=3, default="QAR")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    value = models.DecimalField(max_digits=16, decimal_places=2)
    note = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["document_date", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="old_balance_demo_only"),
            models.CheckConstraint(condition=Q(amount__gt=0, value__gt=0), name="old_balance_positive"),
            models.CheckConstraint(condition=Q(side="supplier") | Q(currency="QAR", value=F("amount")), name="old_balance_customer_in_qar"),
            models.UniqueConstraint(fields=["business_unit", "party", "reference"], name="old_balance_reference_once"),
        ]


class OldBalanceSettlement(models.Model):
    """Money received from a customer, or paid to a supplier, on an old balance. A second person approves it."""

    class Status(models.TextChoices):
        REQUESTED = "requested", "Waiting for approval"
        APPROVED = "approved", "Approved and booked"
        REJECTED = "rejected", "Rejected"

    balance = models.ForeignKey(OldBalance, on_delete=models.PROTECT, related_name="settlements")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REQUESTED)
    # In the balance's currency; the rate is QAR per one unit on the payment day.
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=8, default=1)
    rate_source = models.CharField(max_length=120, blank=True)
    rate_date = models.DateField(null=True, blank=True)
    paid_value = models.DecimalField(max_digits=16, decimal_places=2)
    cleared_value = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    paid_on = models.DateField()
    cash_account = models.ForeignKey(CashAccount, on_delete=models.PROTECT, related_name="old_balance_settlements")
    bank_reference = models.CharField(max_length=80, blank=True)
    journal_entry = models.OneToOneField("JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="old_balance_settlement")
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="old_settlement_demo_only"),
            models.CheckConstraint(condition=Q(amount__gt=0, paid_value__gt=0), name="old_settlement_positive"),
            models.CheckConstraint(condition=Q(decided_by__isnull=True) | ~Q(decided_by=F("prepared_by")), name="old_settlement_second_person"),
            models.CheckConstraint(condition=~Q(status="approved") | Q(journal_entry__isnull=False, cleared_value__isnull=False),
                                   name="old_settlement_approved_is_booked"),
            models.CheckConstraint(condition=~Q(status="rejected") | ~Q(decision_note=""), name="old_settlement_rejection_reason"),
        ]


class StaffLoan(models.Model):
    """Money lent to a member of staff, or a salary advance, paid out once and taken back through payroll."""

    class Kind(models.TextChoices):
        LOAN = "loan", "Staff loan"
        ADVANCE = "advance", "Salary advance"

    class Status(models.TextChoices):
        REQUESTED = "requested", "Waiting for approval"
        APPROVED = "approved", "Approved, to pay out"
        REPAYING = "repaying", "Being repaid"
        REPAID = "repaid", "Repaid"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="+")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="loans")
    kind = models.CharField(max_length=7, choices=Kind.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    instalment = models.DecimalField(max_digits=12, decimal_places=2)
    first_month = models.DateField()
    reason = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REQUESTED)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    requested_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)
    paid_from = models.ForeignKey(CashAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    paid_on = models.DateField(null=True, blank=True)
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    payout_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-requested_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="staff_loan_demo_only"),
            models.CheckConstraint(condition=Q(first_month__day=1), name="staff_loan_first_month_first"),
            models.CheckConstraint(condition=Q(amount__gt=0, instalment__gt=0) & Q(instalment__lte=F("amount")),
                                   name="staff_loan_amounts"),
            models.CheckConstraint(condition=Q(decided_by__isnull=True) | ~Q(decided_by=F("requested_by")),
                                   name="staff_loan_second_person"),
            models.CheckConstraint(condition=Q(status="requested", decided_by__isnull=True)
                                   | (Q(status__in=["approved", "rejected"]) & Q(decided_by__isnull=False))
                                   | (Q(status__in=["repaying", "repaid"]) & Q(payout_entry__isnull=False))
                                   | Q(status="cancelled"),
                                   name="staff_loan_status_recorded"),
        ]


class StaffLoanRepayment(models.Model):
    """One amount taken back from a loan: a payroll instalment or the end-of-service settlement."""

    loan = models.ForeignKey(StaffLoan, on_delete=models.PROTECT, related_name="repayments")
    run = models.ForeignKey("PayrollRun", on_delete=models.PROTECT, related_name="+")
    month = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    from_settlement = models.BooleanField(default=False)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["month", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="staff_loan_repayment_demo_only"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="staff_loan_repayment_positive"),
            models.UniqueConstraint(fields=["loan", "run"], name="staff_loan_repayment_once_per_run"),
        ]


class PayrollRun(models.Model):
    """One unit's payroll for one month: prepared, then approved and posted by someone else."""

    class Status(models.TextChoices):
        PREPARED = "prepared", "Waiting for approval"
        POSTED = "posted", "Approved and posted"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="+")
    month = models.DateField()
    period_start = models.DateField()
    period_end = models.DateField()
    pay_date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PREPARED)
    totals = models.JSONField(default=dict)
    warnings = models.JSONField(default=list)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    prepared_at = models.DateTimeField(default=timezone.now)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    journal_entry = models.OneToOneField("JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-month", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="payroll_run_demo_only"),
            models.CheckConstraint(condition=Q(month__day=1), name="payroll_run_first_of_month"),
            models.UniqueConstraint(fields=["business_unit", "month"], name="payroll_run_once_a_month"),
            models.CheckConstraint(condition=Q(status="prepared", approved_by__isnull=True, journal_entry__isnull=True)
                                   | Q(status="posted", approved_by__isnull=False, journal_entry__isnull=False),
                                   name="payroll_run_posting_recorded"),
            models.CheckConstraint(condition=Q(approved_by__isnull=True) | ~Q(approved_by=F("prepared_by")),
                                   name="payroll_run_second_person"),
        ]


class PayrollLine(models.Model):
    """One person's pay in a payroll run, with how each amount was worked out."""

    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="lines")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payroll_lines")
    days_paid = models.PositiveSmallIntegerField()
    basic = models.DecimalField(max_digits=12, decimal_places=2)
    allowances = models.DecimalField(max_digits=12, decimal_places=2)
    overtime = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    overtime_hours = models.DecimalField(max_digits=6, decimal_places=1, default=0)
    end_of_service = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net = models.DecimalField(max_digits=12, decimal_places=2)
    detail = models.JSONField(default=dict)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="payroll_line_demo_only"),
            models.UniqueConstraint(fields=["run", "employee"], name="payroll_line_once_per_person"),
            models.CheckConstraint(condition=Q(net__gte=0, deductions__gte=0, overtime__gte=0, end_of_service__gte=0),
                                   name="payroll_line_amounts"),
        ]


class OvertimeRequest(models.Model):
    """Overtime agreed before the work: who, when, how long, and the sales order it is for."""

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Waiting for approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="+")
    sales_order = models.ForeignKey("SalesQuote", on_delete=models.PROTECT, related_name="overtime")
    work_date = models.DateField()
    rest_day = models.BooleanField(default=False)
    rate_percent = models.PositiveSmallIntegerField()
    work = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SUBMITTED)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    prepared_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-work_date", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="overtime_demo_only"),
            models.CheckConstraint(condition=Q(rate_percent__in=[125, 150]), name="overtime_rate_known"),
            models.CheckConstraint(condition=~Q(status__in=["approved", "rejected"]) | ~Q(decided_by=F("prepared_by")),
                                   name="overtime_second_person"),
            models.CheckConstraint(condition=Q(status="submitted", decided_by__isnull=True)
                                   | Q(status__in=["approved", "rejected", "cancelled"], decided_by__isnull=False),
                                   name="overtime_decision_recorded"),
        ]


class OvertimeLine(models.Model):
    """One person's hours on an overtime request."""

    request = models.ForeignKey(OvertimeRequest, on_delete=models.CASCADE, related_name="lines")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="overtime")
    hours = models.DecimalField(max_digits=4, decimal_places=1)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="overtime_line_demo_only"),
            models.CheckConstraint(condition=Q(hours__gt=0, hours__lte=12), name="overtime_line_hours_range"),
            models.UniqueConstraint(fields=["request", "employee"], name="overtime_line_once_per_person"),
        ]


class AttendanceMonth(models.Model):
    """HR confirms a unit's month of attendance; after that it cannot change unless HR reopens it."""

    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmed"
        REOPENED = "reopened", "Reopened"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="+")
    month = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CONFIRMED)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    confirmed_at = models.DateTimeField(default=timezone.now)
    summary = models.JSONField(default=dict)
    reopened_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopen_reason = models.CharField(max_length=200, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="attendance_month_demo_only"),
            models.CheckConstraint(condition=Q(status="confirmed") | Q(reopened_by__isnull=False) & ~Q(reopen_reason=""),
                                   name="attendance_month_reopen_recorded"),
            models.CheckConstraint(condition=Q(month__day=1), name="attendance_month_first_day"),
            models.UniqueConstraint(fields=["business_unit", "month"], name="attendance_month_once"),
        ]


class IncentiveYear(models.Model):
    """One legal entity's incentive year: its policy settings and the central company figures.

    The policy holds every rate, band and weight (copied from the brief's working model, Finance-test and
    provisional until approved). Company figures left blank stay blank: they make results pending, never zero.
    """

    class PolicyStatus(models.TextChoices):
        PROVISIONAL = "provisional", "Provisional (Finance-test)"
        APPROVED = "approved", "Approved"

    legal_entity = models.ForeignKey(LegalEntity, on_delete=models.PROTECT, related_name="incentive_years")
    year = models.PositiveSmallIntegerField()
    policy = models.JSONField(default=dict)
    policy_version = models.PositiveIntegerField(default=1)
    policy_status = models.CharField(max_length=12, choices=PolicyStatus.choices, default=PolicyStatus.PROVISIONAL)
    policy_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                           related_name="+")
    policy_approved_at = models.DateTimeField(null=True, blank=True)
    ebitda_target = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    ebitda_actual = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    operational = models.JSONField(default=dict, blank=True,
                                   help_text="Achievement % of collections, GP and margin, inventory, strategic execution.")
    reward_payment_month = models.DateField(null=True, blank=True,
                                            help_text="When fixed rewards are paid (the first day of that month).")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-year"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="incentive_year_demo_only"),
            models.CheckConstraint(condition=Q(year__gte=2000, year__lte=2100), name="incentive_year_range"),
            models.UniqueConstraint(fields=["legal_entity", "year"], name="incentive_year_once"),
        ]


class IncentiveEvent(models.Model):
    """What happened in an incentive year and who did it: policy versions, figures, plans, runs. Kept for good."""

    year = models.ForeignKey(IncentiveYear, on_delete=models.CASCADE, related_name="events")
    what = models.CharField(max_length=40)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    at = models.DateTimeField(default=timezone.now)
    detail = models.JSONField(default=dict, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-at", "-pk"]
        constraints = [models.CheckConstraint(condition=Q(is_synthetic=True), name="incentive_event_demo_only")]


class IncentiveUnit(models.Model):
    """One business unit's central figures for the incentive year.

    Revenue and direct-cost targets come from the unit's approved budget, and actuals from the posted ledger.
    Collections and operational achievement are entered here, once, and validated by Finance.
    """

    year = models.ForeignKey(IncentiveYear, on_delete=models.CASCADE, related_name="units")
    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="incentive_units")
    counts_in_company = models.BooleanField(default=True, help_text="One of the revenue-generating units in company revenue.")
    collections_target = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    collections_actual = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    operational_achievement = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    minimum_margin = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    validated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    validated_at = models.DateTimeField(null=True, blank=True)
    validated_figures = models.JSONField(default=dict, blank=True,
                                         help_text="The figures as Finance validated them; a later change needs validating again.")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["business_unit__code"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="incentive_unit_demo_only"),
            models.UniqueConstraint(fields=["year", "business_unit"], name="incentive_unit_once"),
        ]


class IncentiveTeam(models.Model):
    """A sales team for supervisor commission: its own targets and Finance-approved collection fraction.

    Its actual revenue and GP are the sum of its salespeople's validated figures, so nothing is entered twice; a supervisor's own
    sales count only in their personal part, never again in the team part.
    """

    year = models.ForeignKey(IncentiveYear, on_delete=models.CASCADE, related_name="teams")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=80)
    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="incentive_teams")
    revenue_target = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    gp_target = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    collection_fraction = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="incentive_team_demo_only"),
            models.CheckConstraint(condition=Q(collection_fraction__isnull=True) | Q(collection_fraction__gte=0, collection_fraction__lte=1),
                                   name="incentive_team_fraction_range"),
            models.UniqueConstraint(fields=["year", "code"], name="incentive_team_code_once"),
        ]


class IncentiveEntitlement(models.Model):
    """One person's incentive plan for part or all of a year: which plan, the months it covers and its inputs.

    Proposed until someone other than the proposer approves it. A grade alone never makes anyone eligible.
    Two plans for the same person cannot cover the same month, so one contribution is never rewarded twice.
    """

    class Plan(models.TextChoices):
        SALES = "sales_commission", "Sales commission"
        HYBRID = "supervisor_hybrid", "Supervisor hybrid"
        GROWTH = "business_growth", "Business growth"
        MANAGEMENT = "management_bonus", "Management bonus"
        REWARD = "performance_reward", "Performance reward"

    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        APPROVED = "approved", "Approved"
        WITHDRAWN = "withdrawn", "Withdrawn"

    year = models.ForeignKey(IncentiveYear, on_delete=models.CASCADE, related_name="entitlements")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="incentive_entitlements")
    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="incentive_entitlements",
                                      help_text="The unit whose results count for this person.")
    plan = models.CharField(max_length=20, choices=Plan.choices)
    channel = models.CharField(max_length=12, blank=True)
    reward_band = models.CharField(max_length=6, blank=True)
    category = models.CharField(max_length=30, blank=True)
    first_month = models.PositiveSmallIntegerField(default=1)
    last_month = models.PositiveSmallIntegerField(default=12)
    revenue_target = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    gp_target = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    revenue_actual = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True,
                                         help_text="Qualifying revenue as validated by Finance.")
    gp_actual = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    collection_fraction = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    rating = models.CharField(max_length=20, blank=True)
    team = models.ForeignKey(IncentiveTeam, null=True, blank=True, on_delete=models.PROTECT, related_name="members")
    team_share = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True,
                                     help_text="A supervisor's share of the team pool, in %.")
    note = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PROPOSED)
    proposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    proposed_at = models.DateTimeField(default=timezone.now)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["employee__full_name", "first_month", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="incentive_entitlement_demo_only"),
            models.CheckConstraint(condition=Q(first_month__gte=1, last_month__lte=12, first_month__lte=models.F("last_month")),
                                   name="incentive_entitlement_months"),
            models.CheckConstraint(condition=Q(collection_fraction__isnull=True) | Q(collection_fraction__gte=0, collection_fraction__lte=1),
                                   name="incentive_entitlement_fraction_range"),
            models.CheckConstraint(condition=~Q(status="approved") | Q(approved_by__isnull=False),
                                   name="incentive_entitlement_approved_by"),
        ]


class IncentiveRun(models.Model):
    """A worked-out set of commission and rewards for an incentive year.

    Prepared, checked, approved and released by different people; approved runs are frozen. Each plan is
    paid by one run only. Released amounts become instalments that payroll pays.
    """

    class Status(models.TextChoices):
        PREPARED = "prepared", "Waiting to be checked"
        CHECKED = "checked", "Checked, waiting for approval"
        APPROVED = "approved", "Approved, waiting for release"
        RELEASED = "released", "Released to payroll"
        PAID = "paid", "Paid in full"

    year = models.ForeignKey(IncentiveYear, on_delete=models.PROTECT, related_name="runs")
    number = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PREPARED)
    policy_version = models.PositiveIntegerField()
    policy = models.JSONField(default=dict)
    company = models.JSONField(default=dict)
    units = models.JSONField(default=list)
    lines = models.JSONField(default=list)
    commission_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    reward_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    pending_count = models.PositiveIntegerField(default=0)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    prepared_at = models.DateTimeField(default=timezone.now)
    checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    checked_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    released_at = models.DateTimeField(null=True, blank=True)
    journal_entries = models.ManyToManyField(JournalEntry, blank=True, related_name="+")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-year__year", "-number"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="incentive_run_demo_only"),
            models.CheckConstraint(condition=Q(commission_total__gte=0, reward_total__gte=0), name="incentive_run_not_negative"),
            models.UniqueConstraint(fields=["year", "number"], name="incentive_run_number_once"),
        ]


class IncentiveInstalment(models.Model):
    """One amount that payroll pays to one person from a released incentive run."""

    class Kind(models.TextChoices):
        COMMISSION = "commission", "Commission"
        REWARD = "reward", "Reward"

    run = models.ForeignKey(IncentiveRun, on_delete=models.PROTECT, related_name="instalments")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="incentive_instalments")
    entitlement = models.ForeignKey(IncentiveEntitlement, on_delete=models.PROTECT, related_name="instalments")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    due_month = models.DateField(help_text="The first day of the payroll month that pays it.")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    paid_in = models.ForeignKey(PayrollRun, null=True, blank=True, on_delete=models.PROTECT, related_name="incentive_instalments")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["due_month", "employee__full_name", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="incentive_instalment_demo_only"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="incentive_instalment_positive"),
            models.CheckConstraint(condition=Q(due_month__day=1), name="incentive_instalment_first_day"),
        ]


# Factory (Phase 2). Built behind a switch: until the Master Admin turns it on, only the Master Admin sees it.

class Workshop(models.TextChoices):
    JOINERY = "joinery", "Joinery"
    PAINT = "paint", "Paint"
    CNC = "cnc", "CNC (wood and metal)"
    METAL = "metal", "Metal"


class FactorySettings(models.Model):
    """The factory's switch, hourly rates, overtime and overhead, one row per company. Only the Master Admin changes it."""

    legal_entity = models.OneToOneField(LegalEntity, on_delete=models.PROTECT, related_name="factory_settings")
    is_live = models.BooleanField(default=False)
    overhead_percent = models.DecimalField(max_digits=5, decimal_places=2, default="15.00")
    overrun_percent = models.DecimalField(max_digits=5, decimal_places=2, default="10.00")
    overtime_percent = models.DecimalField(max_digits=5, decimal_places=2, default="125.00")
    rate_joinery = models.DecimalField(max_digits=8, decimal_places=2, default="35.00")
    rate_paint = models.DecimalField(max_digits=8, decimal_places=2, default="30.00")
    rate_cnc = models.DecimalField(max_digits=8, decimal_places=2, default="45.00")
    rate_metal = models.DecimalField(max_digits=8, decimal_places=2, default="35.00")
    # Sam, 7 Nov: QAR 2,000 until Finance replies. A machine costing this much or more is a fixed asset, depreciated over machine_life_months.
    asset_threshold = models.DecimalField(max_digits=12, decimal_places=2, default="2000.00")
    machine_life_months = models.PositiveSmallIntegerField(default=60)
    # Provisional (asked 7 Nov): until purchasing gives an expected arrival, goods arrive this many days after ordering;
    # one person works on at most this many JD stages at the same time.
    order_lead_days = models.PositiveSmallIntegerField(default=21)
    stages_per_person = models.PositiveSmallIntegerField(default=2)
    next_number = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="factory_settings_demo_only"),
            models.CheckConstraint(condition=Q(overhead_percent__gte=0, overhead_percent__lte=200,
                                               overrun_percent__gte=0, overrun_percent__lte=100,
                                               overtime_percent__gte=100, overtime_percent__lte=300),
                                   name="factory_settings_percent_range"),
            models.CheckConstraint(condition=Q(rate_joinery__gte=0, rate_paint__gte=0, rate_cnc__gte=0, rate_metal__gte=0),
                                   name="factory_settings_rates_not_negative"),
            models.CheckConstraint(condition=Q(asset_threshold__gte=0, machine_life_months__gte=12,
                                               machine_life_months__lte=360), name="factory_settings_machine_policy"),
            models.CheckConstraint(condition=Q(order_lead_days__gte=1, order_lead_days__lte=365, stages_per_person__gte=1,
                                               stages_per_person__lte=10), name="factory_settings_planner"),
        ]


class RawMaterial(models.Model):
    """A stock item the factory usually uses (boards, veneer, paint, hardware, metal), on the raw materials list."""

    class Category(models.TextChoices):
        BOARDS = "boards", "Wood and boards"
        VENEER = "veneer", "Veneer and laminates"
        PAINT = "paint", "Paint and finishes"
        HARDWARE = "hardware", "Hardware and fittings"
        METAL = "metal", "Metal"
        FABRIC = "fabric", "Fabric and foam"
        OTHER = "other", "Other"

    variant = models.OneToOneField(Variant, on_delete=models.PROTECT, related_name="raw_material")
    category = models.CharField(max_length=10, choices=Category.choices)
    size = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=True)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    added_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["category", "variant__sku"]
        constraints = [models.CheckConstraint(condition=Q(is_synthetic=True), name="raw_material_demo_only")]


class ProductionOrder(models.Model):
    """A factory job order: the works on a signed sales order (BOQ items JD-00, JD-01...), or showroom furniture."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Being prepared"
        WAITING = "waiting", "Waiting for the Factory Manager"
        IN_PROGRESS = "in_progress", "In production"
        COST_CHECK = "cost_check", "Finished, waiting for Finance (over estimate)"
        STORED = "stored", "Finished, stored in the warehouse"
        ON_THE_WAY = "on_the_way", "On its way to the client's site"
        DELIVERED = "delivered", "Received at the client's site"
        IN_STOCK = "in_stock", "Finished and in showroom stock"
        CANCELLED = "cancelled", "Cancelled"

    class Purpose(models.TextChoices):
        FITOUT = "fitout", "Fit-out project"
        STUDIO = "studio", "Textile and wallcovering studio client"
        SHOWROOM = "showroom", "Showroom furniture"

    class Destination(models.TextChoices):
        WAREHOUSE = "warehouse", "Store in the warehouse"
        SITE = "site", "Send straight to the client's site"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="production_orders")
    number = models.CharField(max_length=20)
    purpose = models.CharField(max_length=10, choices=Purpose.choices)
    quote = models.ForeignKey(SalesQuote, null=True, blank=True, on_delete=models.PROTECT, related_name="production_orders")
    project_name = models.CharField(max_length=120, blank=True)
    # The fit-out project this job makes JD items for (sent from Projects & Fit-Out).
    project = models.ForeignKey("Project", null=True, blank=True, on_delete=models.PROTECT, related_name="factory_jobs")
    design_reference = models.CharField(max_length=120, blank=True)
    # Showroom furniture only: the stock item made, and how many.
    variant = models.ForeignKey(Variant, null=True, blank=True, on_delete=models.PROTECT, related_name="production_orders")
    quantity = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    due_date = models.DateField()
    destination = models.CharField(max_length=10, choices=Destination.choices, default=Destination.WAREHOUSE)
    finish_location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name="production_orders")
    note = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    planned_hours_joinery = models.DecimalField(max_digits=10, decimal_places=2, default="0.00")
    planned_hours_paint = models.DecimalField(max_digits=10, decimal_places=2, default="0.00")
    planned_hours_cnc = models.DecimalField(max_digits=10, decimal_places=2, default="0.00")
    planned_hours_metal = models.DecimalField(max_digits=10, decimal_places=2, default="0.00")
    # The estimate, fixed when the job is sent for approval.
    estimate_materials = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    estimate_labour = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    estimate_overhead = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    overhead_percent = models.DecimalField(max_digits=5, decimal_places=2, default="0.00")
    overrun_percent = models.DecimalField(max_digits=5, decimal_places=2, default="0.00")
    # The actual cost, fixed when the job is finished.
    actual_overhead = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    actual_total = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    finished_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    finished_at = models.DateTimeField(null=True, blank=True)
    cost_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    cost_approved_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    received_on = models.DateField(null=True, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)
    finish_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    received_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="production_order_demo_only"),
            models.CheckConstraint(condition=Q(planned_hours_joinery__gte=0, planned_hours_paint__gte=0,
                                               planned_hours_cnc__gte=0, planned_hours_metal__gte=0),
                                   name="production_order_hours_not_negative"),
            models.CheckConstraint(
                condition=Q(purpose="showroom", quote__isnull=True, project__isnull=True, variant__isnull=False,
                            quantity__gt=0)
                | (Q(purpose="fitout", variant__isnull=True, quantity__isnull=True)
                   & (Q(quote__isnull=False) | Q(project__isnull=False)))
                | Q(purpose="studio", quote__isnull=False, project__isnull=True, variant__isnull=True,
                    quantity__isnull=True),
                name="production_order_purpose_fields"),
            models.UniqueConstraint(fields=["business_unit", "number"], name="production_order_number_once"),
        ]


class ProductionBoqLine(models.Model):
    """One BOQ item the job makes (code JD-00, JD-01...): a door, a cabinet, wall cladding."""

    order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="boq_lines")
    code = models.CharField(max_length=20)
    description = models.CharField(max_length=500)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit = models.CharField(max_length=20, default="piece")
    # Other columns from the uploaded BOQ sheet (size, finish, material, area, remarks...), as heading -> value.
    details = models.JSONField(default=dict, blank=True)
    # Materials this JD needs, from the BOQ's Materials sheet: [{"variant_id", "sku", "quantity"}] for the whole JD.
    materials = models.JSONField(default=list, blank=True)
    # Where it goes on site, as in the BOQ (Majlis 1 · Ground floor · Zone 1).
    area = models.CharField(max_length=80, blank=True)
    floor = models.CharField(max_length=80, blank=True)
    zone = models.CharField(max_length=80, blank=True)
    tags = models.ManyToManyField("FactoryTag", blank=True, related_name="boq_lines")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="production_boq_demo_only"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="production_boq_positive"),
            models.UniqueConstraint(fields=["order", "code"], name="production_boq_code_once"),
        ]


class ProductionOrderMaterial(models.Model):
    """A material the job needs, as quantified by the factory from the production drawings."""

    order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="materials")
    material = models.ForeignKey(Variant, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default="0.00")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["material__sku"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="production_material_demo_only"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="production_material_positive"),
            models.UniqueConstraint(fields=["order", "material"], name="production_material_once"),
        ]


class ProductionIssue(models.Model):
    """Materials taken for a job. From the warehouse, each request waits for the Factory Manager, then the warehouse admin."""

    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting for the Factory Manager"
        WITH_WAREHOUSE = "warehouse", "With the warehouse"
        ISSUED = "issued", "Received and used"
        REJECTED = "rejected", "Rejected"

    order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="issues")
    material = models.ForeignKey(Variant, on_delete=models.PROTECT, related_name="+")
    # Where it is taken from: the raw materials store (the warehouse delivers it), or a factory location (used directly).
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    # The factory location the warehouse delivers to; blank when used directly from factory stock.
    deliver_to = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    # The warehouse's transfer request, handled like any other move: schedule, dispatch, receive.
    transfer_request = models.ForeignKey(StockTransferRequest, null=True, blank=True, on_delete=models.PROTECT,
                                         related_name="factory_issues")
    # The material request slip this line is on (Factory Manager, then warehouse admin approve it, then it is printed).
    request = models.ForeignKey("FactoryMaterialRequest", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="issues")
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    value = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    beyond_plan = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=Status.choices)
    note = models.CharField(max_length=200, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    requested_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["requested_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="production_issue_demo_only"),
            models.CheckConstraint(condition=Q(quantity__gt=0, value__gte=0), name="production_issue_positive"),
        ]


class ProductionLabour(models.Model):
    """Hours someone worked on a job in one workshop, costed at that workshop's rate (more for overtime)."""

    order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="labour")
    workshop = models.CharField(max_length=12, choices=Workshop.choices)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="production_labour")
    # The BOQ item (JD) the hours were spent on, when known: feeds each person's record and what the factory learns.
    line = models.ForeignKey("ProductionBoqLine", null=True, blank=True, on_delete=models.SET_NULL, related_name="labour")
    work_date = models.DateField()
    hours = models.DecimalField(max_digits=6, decimal_places=2)
    overtime = models.BooleanField(default=False)
    rate = models.DecimalField(max_digits=8, decimal_places=2)
    cost = models.DecimalField(max_digits=14, decimal_places=2)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(default=timezone.now)
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["work_date", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="production_labour_demo_only"),
            models.CheckConstraint(condition=Q(hours__gt=0, hours__lte=16), name="production_labour_hours_range"),
            models.CheckConstraint(condition=Q(rate__gte=0, cost__gte=0), name="production_labour_not_negative"),
        ]


class FactoryPurchase(models.Model):
    """A special purchase for one job, not taken from the warehouse: requested, approved, then bought by cash or card."""

    class Status(models.TextChoices):
        REQUESTED = "requested", "Waiting for the Factory Manager"
        APPROVED = "approved", "Approved, to be bought"
        BOUGHT = "bought", "Bought"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="purchases")
    description = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    estimated_cost = models.DecimalField(max_digits=14, decimal_places=2)
    supplier_name = models.CharField(max_length=120, blank=True)
    reason = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REQUESTED)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    requested_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)
    bought_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    bought_on = models.DateField(null=True, blank=True)
    actual_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    paid_from = models.ForeignKey(CashAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    receipt_reference = models.CharField(max_length=60, blank=True)
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-requested_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="factory_purchase_demo_only"),
            models.CheckConstraint(condition=Q(quantity__gt=0, estimated_cost__gt=0), name="factory_purchase_positive"),
            models.CheckConstraint(condition=Q(status="bought", actual_cost__gt=0, paid_from__isnull=False)
                                   | (~Q(status="bought") & Q(actual_cost__isnull=True)),
                                   name="factory_purchase_bought_has_cost"),
        ]


class FactoryTag(models.Model):
    """A kind of work (door, cabinet, cladding, glass...) the Factory Manager keeps, to count work over the years."""

    legal_entity = models.ForeignKey(LegalEntity, on_delete=models.PROTECT, related_name="factory_tags")
    name = models.CharField(max_length=40)
    color = models.CharField(max_length=7, default="#8a5a2b")
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="factory_tag_demo_only"),
            models.UniqueConstraint(Lower("name"), "legal_entity", name="factory_tag_name_once"),
            models.CheckConstraint(condition=Q(color__regex=r"^#[0-9a-fA-F]{6}$"), name="factory_tag_color_hex"),
        ]


class ProductionStage(models.Model):
    """One step of making a BOQ item: drawings, materials, saw/CNC, joinery, paint, metal, ready for delivery."""

    class Kind(models.TextChoices):
        DRAWINGS = "drawings", "Drawings and cutting list"
        MATERIALS = "materials", "Materials ready"
        SAW = "saw", "Saw / CNC"
        JOINERY = "joinery", "Joinery"
        PAINT = "paint", "Paint"
        METAL = "metal", "Metal"
        READY = "ready", "Ready for delivery"

    class Status(models.TextChoices):
        NOT_STARTED = "not_started", "Not started"
        IN_PROGRESS = "in_progress", "In progress"
        STUCK = "stuck", "Stuck"
        DONE = "done", "Completed"
        SKIPPED = "skipped", "Not needed"

    line = models.ForeignKey(ProductionBoqLine, on_delete=models.CASCADE, related_name="stages")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    position = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NOT_STARTED)
    planned_start = models.DateField(null=True, blank=True)
    planned_end = models.DateField(null=True, blank=True)
    started_on = models.DateField(null=True, blank=True)
    finished_on = models.DateField(null=True, blank=True)
    lead = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.PROTECT, related_name="production_stages_led")
    helpers = models.ManyToManyField(Employee, blank=True, related_name="production_stages_helped")
    stuck_reason = models.CharField(max_length=200, blank=True)
    # Dates a person set on the stage itself; the factory planner works around them and does not move them.
    planned_by_hand = models.BooleanField(default=False)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["position", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="production_stage_demo_only"),
            models.UniqueConstraint(fields=["line", "kind"], name="production_stage_once"),
            models.CheckConstraint(condition=Q(planned_start__isnull=True) | Q(planned_end__isnull=True)
                                   | Q(planned_end__gte=F("planned_start")), name="production_stage_planned_order"),
            models.CheckConstraint(condition=~Q(status="stuck") | ~Q(stuck_reason=""), name="production_stage_stuck_reason"),
            models.CheckConstraint(condition=~Q(status="done") | Q(finished_on__isnull=False), name="production_stage_done_date"),
        ]


class FactorySection(models.TextChoices):
    """Where a factory person works. Each JD stage is done by one section."""

    DRAWINGS = "drawings", "Drawing office"
    STORE = "store", "Raw materials store"
    CNC = "cnc", "CNC (wood and metal)"
    JOINERY = "joinery", "Joinery"
    PAINT = "paint", "Paint"
    METAL = "metal", "Metal"
    UPHOLSTERY = "upholstery", "Upholstery"
    DISPATCH = "dispatch", "Dispatch"
    SUPERVISION = "supervision", "Supervisors"


class FactoryWorker(models.Model):
    """A company employee who works in the factory, and the section they work in. Kept by the Factory Manager."""

    employee = models.OneToOneField(Employee, on_delete=models.PROTECT, related_name="factory_worker")
    section = models.CharField(max_length=12, choices=FactorySection.choices)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["section", "employee__full_name"]
        constraints = [models.CheckConstraint(condition=Q(is_synthetic=True), name="factory_worker_demo_only")]


class FactoryMaterialRequest(models.Model):
    """A request slip for raw materials from the warehouse: the Factory Manager approves, then the warehouse admin,
    then it is printed with all the approvals and handed to the warehouse team, and filed."""

    class Status(models.TextChoices):
        MANAGER = "manager", "Waiting for the Factory Manager"
        WAREHOUSE = "warehouse", "Waiting for the warehouse admin"
        APPROVED = "approved", "Approved: print and hand to the warehouse"
        DELIVERED = "delivered", "Delivered to the factory"
        REJECTED = "rejected", "Rejected"

    order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="material_requests")
    number = models.CharField(max_length=20)
    store = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    deliver_to = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.MANAGER)
    note = models.CharField(max_length=200, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    requested_at = models.DateTimeField(default=timezone.now)
    manager_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    manager_at = models.DateTimeField(null=True, blank=True)
    # The warehouse's own transfer request; its approver is the warehouse admin's approval on the slip.
    transfer_request = models.OneToOneField(StockTransferRequest, null=True, blank=True, on_delete=models.PROTECT,
                                            related_name="factory_request")
    rejected_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    rejection_reason = models.CharField(max_length=200, blank=True)
    printed_count = models.PositiveSmallIntegerField(default=0)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-requested_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="factory_material_request_demo_only"),
            models.UniqueConstraint(fields=["order", "number"], name="factory_material_request_number_once"),
            models.CheckConstraint(condition=~Q(status="rejected") | ~Q(rejection_reason=""),
                                   name="factory_material_request_rejection_reason"),
        ]


class FactoryMachine(models.Model):
    """A machine or power tool in the factory: its condition, its maintenance, and how Finance treats its cost."""

    class Kind(models.TextChoices):
        CNC = "cnc", "CNC router"
        SAW = "saw", "Panel saw / sector saw"
        EDGE = "edge", "Edge bander"
        PRESS = "press", "Press"
        SANDER = "sander", "Sander"
        SPRAY = "spray", "Paint booth / spray"
        COMPRESSOR = "compressor", "Compressor"
        DUST = "dust", "Dust extraction"
        WELDER = "welder", "Welder / metal machine"
        TOOL = "tool", "Hand power tool"
        OTHER = "other", "Other"

    class Condition(models.TextChoices):
        NEW = "new", "New"
        GOOD = "good", "Good condition"
        FAIR = "fair", "Working, needs attention"
        REPAIR = "repair", "Needs repair"
        REPLACE = "replace", "Needs replacement"
        OUT = "out", "Out of service"

    legal_entity = models.ForeignKey(LegalEntity, on_delete=models.PROTECT, related_name="factory_machines")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    section = models.CharField(max_length=12, choices=FactorySection.choices)
    make_model = models.CharField(max_length=120, blank=True)
    serial_number = models.CharField(max_length=60, blank=True)
    quantity = models.PositiveSmallIntegerField(default=1)
    condition = models.CharField(max_length=8, choices=Condition.choices, default=Condition.GOOD)
    bought_on = models.DateField(null=True, blank=True)
    cost = models.DecimalField(max_digits=14, decimal_places=2, default="0.00")
    # Maintenance: every so many days, counted from the last service.
    service_every_days = models.PositiveSmallIntegerField(null=True, blank=True)
    last_service_on = models.DateField(null=True, blank=True)
    fixed_asset = models.OneToOneField("FixedAsset", null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="factory_machine")
    note = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["section", "code"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="factory_machine_demo_only"),
            models.UniqueConstraint(fields=["legal_entity", "code"], name="factory_machine_code_once"),
            models.CheckConstraint(condition=Q(cost__gte=0, quantity__gte=1), name="factory_machine_positive"),
            models.CheckConstraint(condition=Q(service_every_days__isnull=True)
                                   | Q(service_every_days__gte=1, service_every_days__lte=730),
                                   name="factory_machine_service_range"),
        ]


class FactoryMachineService(models.Model):
    """A maintenance visit or repair done on a machine."""

    class Kind(models.TextChoices):
        ROUTINE = "routine", "Routine maintenance"
        REPAIR = "repair", "Repair"
        INSPECTION = "inspection", "Inspection"

    machine = models.ForeignKey(FactoryMachine, on_delete=models.CASCADE, related_name="services")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    done_on = models.DateField()
    done_by = models.CharField(max_length=120, blank=True)
    cost = models.DecimalField(max_digits=12, decimal_places=2, default="0.00")
    note = models.CharField(max_length=200, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-done_on", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="factory_machine_service_demo_only"),
            models.CheckConstraint(condition=Q(cost__gte=0), name="factory_machine_service_cost"),
        ]


class FactoryPart(models.Model):
    """A spare part or consumable for the machines (blades, CNC bits, filters, belts, sandpaper...). Always an expense."""

    class Kind(models.TextChoices):
        SPARE = "spare", "Spare part"
        CONSUMABLE = "consumable", "Consumable"

    legal_entity = models.ForeignKey(LegalEntity, on_delete=models.PROTECT, related_name="factory_parts")
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    machine = models.ForeignKey(FactoryMachine, null=True, blank=True, on_delete=models.SET_NULL, related_name="parts")
    unit = models.CharField(max_length=20, default="pcs")
    on_hand = models.DecimalField(max_digits=12, decimal_places=2, default="0.00")
    reorder_at = models.DecimalField(max_digits=12, decimal_places=2, default="0.00")
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default="0.00")
    is_active = models.BooleanField(default=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["kind", "name"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="factory_part_demo_only"),
            models.CheckConstraint(condition=Q(on_hand__gte=0, reorder_at__gte=0, unit_cost__gte=0),
                                   name="factory_part_not_negative"),
        ]


class PortalPost(models.Model):
    """Something posted on the HR Portal for staff: an announcement, a memo, a procedure or an event."""

    class Kind(models.TextChoices):
        ANNOUNCEMENT = "announcement", "Announcement"
        MEMO = "memo", "Memo"
        PROCEDURE = "procedure", "Procedure"
        EVENT = "event", "Event"

    class Status(models.TextChoices):
        PUBLISHED = "published", "On the portal"
        WITHDRAWN = "withdrawn", "Taken down"

    # Empty business unit: the post is for everyone in the group.
    business_unit = models.ForeignKey(BusinessUnit, null=True, blank=True, on_delete=models.PROTECT, related_name="portal_posts")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    title = models.CharField(max_length=150)
    body = models.TextField(max_length=8000)
    must_read = models.BooleanField(default=False)
    pinned = models.BooleanField(default=False)
    event_on = models.DateField(null=True, blank=True)
    event_time = models.CharField(max_length=40, blank=True)
    event_place = models.CharField(max_length=120, blank=True)
    file_name = models.CharField(max_length=120, blank=True)
    file_type = models.CharField(max_length=20, blank=True)
    file_data = models.BinaryField(blank=True, default=b"")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PUBLISHED)
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    posted_at = models.DateTimeField(default=timezone.now)
    withdrawn_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    withdrawn_reason = models.CharField(max_length=200, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-pinned", "-posted_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="portal_post_demo_only"),
            models.CheckConstraint(condition=~Q(kind="event") | Q(event_on__isnull=False), name="portal_event_has_date"),
            models.CheckConstraint(condition=Q(status="published", withdrawn_at__isnull=True)
                                   | Q(status="withdrawn", withdrawn_at__isnull=False) & ~Q(withdrawn_reason=""),
                                   name="portal_post_withdrawal_recorded"),
        ]


class PortalPhoto(models.Model):
    """One photo in an event's album on the HR Portal."""

    post = models.ForeignKey(PortalPost, on_delete=models.CASCADE, related_name="photos")
    image = models.BinaryField()
    image_type = models.CharField(max_length=5)
    caption = models.CharField(max_length=150, blank=True)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    added_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["added_at", "pk"]
        constraints = [models.CheckConstraint(condition=Q(is_synthetic=True), name="portal_photo_demo_only")]


class PortalRead(models.Model):
    """Someone confirmed they have read a must-read post."""

    post = models.ForeignKey(PortalPost, on_delete=models.CASCADE, related_name="reads")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    read_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["read_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="portal_read_demo_only"),
            models.UniqueConstraint(fields=["post", "user"], name="portal_read_once"),
        ]


class PortalSuggestion(models.Model):
    """A suggestion from a member of staff to the company, read by HR. It always carries the sender's name (Sam)."""

    class Topic(models.TextChoices):
        IDEA = "idea", "An idea to improve something"
        WORKPLACE = "workplace", "Our workplace"
        PROCESS = "process", "How we work"
        OTHER = "other", "Something else"

    class Status(models.TextChoices):
        NEW = "new", "New"
        SEEN = "seen", "Read by HR"
        CLOSED = "closed", "Answered"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="portal_suggestions")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    topic = models.CharField(max_length=10, choices=Topic.choices, default=Topic.IDEA)
    text = models.TextField(max_length=2000)
    sent_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=6, choices=Status.choices, default=Status.NEW)
    reply = models.CharField(max_length=1000, blank=True)
    replied_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    replied_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-sent_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="portal_suggestion_demo_only"),
            models.CheckConstraint(condition=~Q(status="closed") | Q(replied_at__isnull=False), name="portal_suggestion_reply_recorded"),
        ]


class PurchaseArrival(models.Model):
    """When the goods of a purchase order are expected; orders themselves are append-only, so the date lives here."""

    commitment = models.OneToOneField(PurchaseCommitment, on_delete=models.CASCADE, related_name="arrival")
    expected_on = models.DateField()
    note = models.CharField(max_length=160, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(is_synthetic=True), name="purchase_arrival_demo_only")]


class FactoryPlanBrief(models.Model):
    """What a factory admin tells the planner before it plans a job: where and when to start, and anything else."""

    order = models.OneToOneField(ProductionOrder, on_delete=models.CASCADE, related_name="plan_brief")
    start_on = models.DateField()
    floor_order = models.JSONField(default=list)   # floors in the order to work on them
    first_codes = models.JSONField(default=list)   # JDs to do before the others
    finish_by = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=500, blank=True)
    given_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    given_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(is_synthetic=True), name="factory_plan_brief_demo_only")]


class FactoryPlanRun(models.Model):
    """Each time the planner planned the factory: by itself in the morning, or when someone asked; what moved."""

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="factory_plan_runs")
    ran_on = models.DateField()
    ran_at = models.DateTimeField(default=timezone.now)
    ran_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=120, blank=True)
    changes = models.PositiveIntegerField(default=0)
    moves = models.JSONField(default=list)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-ran_at", "-pk"]
        constraints = [models.CheckConstraint(condition=Q(is_synthetic=True), name="factory_plan_run_demo_only")]


# Projects & Fit-Out (Phase 2). Built behind a switch: until the Master Admin turns it on, only the Master Admin sees it.

class ProjectSettings(models.Model):
    """The projects switch and the default client terms, one row per company (Master Admin only)."""

    legal_entity = models.OneToOneField(LegalEntity, on_delete=models.PROTECT, related_name="project_settings")
    is_live = models.BooleanField(default=False)
    # Sam, 7 Nov 2026: a down payment of 20% up to 50%, progress payments certified by the consultant, then 5%
    # retention paid after handover, either at once or after the warranty period (usually 400 days).
    advance_percent = models.DecimalField(max_digits=5, decimal_places=2, default="20.00")
    retention_percent = models.DecimalField(max_digits=5, decimal_places=2, default="5.00")
    retention_release = models.CharField(max_length=10, default="warranty")
    warranty_days = models.PositiveSmallIntegerField(default=400)
    bond_reminder_days = models.PositiveSmallIntegerField(default=30)
    claim_due_days = models.PositiveSmallIntegerField(default=30)
    next_number = models.PositiveIntegerField(default=1)
    next_tender = models.PositiveIntegerField(default=1)
    next_invoice = models.PositiveIntegerField(default=1)
    next_order = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="project_settings_demo_only"),
            models.CheckConstraint(condition=Q(advance_percent__gte=0, advance_percent__lte=100,
                                               retention_percent__gte=0, retention_percent__lte=50,
                                               warranty_days__lte=2000,
                                               retention_release__in=("handover", "warranty"),
                                               bond_reminder_days__lte=365, claim_due_days__lte=365),
                                   name="project_settings_terms_range"),
        ]


class Tender(models.Model):
    """A tender or quotation sent to a client, followed until it is won, lost or cancelled."""

    class Status(models.TextChoices):
        PREPARING = "preparing", "Being prepared"
        SUBMITTED = "submitted", "Submitted"
        WON = "won", "Won"
        LOST = "lost", "Lost"
        CANCELLED = "cancelled", "Cancelled"

    class Progress(models.TextChoices):
        NONE = "", "—"
        PRODUCTION = "production", "Production"
        SHIPPING = "shipping", "Shipping"
        COMPLETED = "completed", "Completed"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="tenders")
    number = models.CharField(max_length=20)
    client_name = models.CharField(max_length=160)
    customer = models.ForeignKey(SyntheticCounterparty, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="tenders")
    project_name = models.CharField(max_length=200, blank=True)
    contact_person = models.CharField(max_length=120, blank=True)
    email = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=60, blank=True)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    responsible = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    start_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    quote_reference = models.CharField(max_length=80, blank=True)
    revision = models.CharField(max_length=10, blank=True)
    brands = models.CharField(max_length=200, blank=True)
    exworks = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    rate = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    total = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SUBMITTED)
    progress = models.CharField(max_length=10, choices=Progress.choices, default="", blank=True)
    remarks = models.CharField(max_length=300, blank=True)
    client_order = models.CharField(max_length=80, blank=True)
    decided_on = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-start_date", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="tender_demo_only"),
            models.CheckConstraint(condition=Q(total__gte=0), name="tender_total_not_negative"),
            models.UniqueConstraint(fields=["business_unit", "number"], name="tender_number_once"),
        ]


class Project(models.Model):
    """A signed fit-out project, opened from its signed BOQ: items, prices, costs, budget, schedule and money."""

    class Status(models.TextChoices):
        SETUP = "setup", "Signed, budget waiting for the Construction Manager"
        RUNNING = "running", "In progress"
        HANDOVER = "handover", "Handed over, in the defects period"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.PROTECT, related_name="projects")
    number = models.CharField(max_length=20)
    name = models.CharField(max_length=160)
    customer = models.ForeignKey(SyntheticCounterparty, on_delete=models.PROTECT, related_name="projects")
    tender = models.ForeignKey(Tender, null=True, blank=True, on_delete=models.PROTECT, related_name="projects")
    site = models.CharField(max_length=160, blank=True)
    design_reference = models.CharField(max_length=120, blank=True)
    quote_reference = models.CharField(max_length=80, blank=True)
    boq_file_name = models.CharField(max_length=200, blank=True)
    project_manager = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SETUP)
    start_date = models.DateField(null=True, blank=True)
    handover_due = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)
    # The signed contract: the BOQ's items at their prices, less the discount on the signed total.
    contract_reference = models.CharField(max_length=60)
    signed_on = models.DateField()
    discount_percent = models.DecimalField(max_digits=7, decimal_places=4, default="0.0000")
    contract_value = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    advance_percent = models.DecimalField(max_digits=5, decimal_places=2, default="0.00")
    retention_percent = models.DecimalField(max_digits=5, decimal_places=2, default="0.00")
    # When the retention is paid: at handover, or after the warranty period counted from handover.
    retention_release = models.CharField(max_length=10, default="warranty")
    warranty_days = models.PositiveSmallIntegerField(default=400)
    # The approved cost: the budget, fixed when the Construction Manager approves it.
    budget_cost = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    handed_over_on = models.DateField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="project_demo_only"),
            models.CheckConstraint(condition=Q(contract_value__gte=0, budget_cost__gte=0, discount_percent__gte=0,
                                               discount_percent__lte=100), name="project_amounts_not_negative"),
            models.UniqueConstraint(fields=["business_unit", "number"], name="project_number_once"),
        ]


class ProjectBoqLine(models.Model):
    """One priced item of the signed BOQ: where it goes, what it sells for, what it costs, who supplies it."""

    class Source(models.TextChoices):
        FACTORY = "factory", "Our factory (joinery)"
        SUPPLY = "supply", "Supplier"
        SUBCONTRACT = "subcontract", "Subcontractor"
        OWN = "own", "Our own team or stock"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="boq_lines")
    position = models.PositiveIntegerField()
    code = models.CharField(max_length=20)
    # The BOQ's headings above the item: the zone (Main Villa - All Floors), the area, the kind of work.
    section = models.CharField(max_length=160, blank=True)
    area = models.CharField(max_length=160, blank=True)
    floor = models.CharField(max_length=120, blank=True)
    work_type = models.CharField(max_length=60, blank=True)
    price_group = models.CharField(max_length=40, blank=True)
    category = models.CharField(max_length=80, blank=True)
    description = models.CharField(max_length=2000)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit = models.CharField(max_length=20, default="piece")
    rate = models.DecimalField(max_digits=16, decimal_places=4, default="0")
    cost_rate = models.DecimalField(max_digits=16, decimal_places=4, default="0")
    supplier = models.CharField(max_length=160, blank=True)
    rfq_date = models.DateField(null=True, blank=True)
    remarks = models.CharField(max_length=300, blank=True)
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.SUPPLY)
    done_percent = models.DecimalField(max_digits=5, decimal_places=2, default="0.00")
    factory_job = models.ForeignKey(ProductionOrder, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="project_lines")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["position", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="project_boq_demo_only"),
            models.CheckConstraint(condition=Q(quantity__gt=0, rate__gte=0, cost_rate__gte=0, done_percent__gte=0,
                                               done_percent__lte=100), name="project_boq_amounts"),
            models.UniqueConstraint(fields=["project", "position"], name="project_boq_position_once"),
        ]


class ProjectLineStep(models.Model):
    """One step of an item on the project schedule: ordered, paid, shipped, delivered, installed, handed over."""

    class Step(models.TextChoices):
        ORDERED = "ordered", "Ordered"
        PAID = "paid", "Paid"
        SHIPPED = "shipped", "Shipped or made"
        DELIVERED = "delivered", "Delivered to site"
        INSTALLED = "installed", "Installed"
        HANDED_OVER = "handed_over", "Handed over"

    line = models.ForeignKey(ProjectBoqLine, on_delete=models.CASCADE, related_name="steps")
    step = models.CharField(max_length=12, choices=Step.choices)
    planned_on = models.DateField(null=True, blank=True)
    done_on = models.DateField(null=True, blank=True)
    not_needed = models.BooleanField(default=False)
    note = models.CharField(max_length=200, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["line", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="project_step_demo_only"),
            models.UniqueConstraint(fields=["line", "step"], name="project_step_once"),
        ]


class ProjectVariation(models.Model):
    """A change to the contract (added or removed work). The Construction Manager approves it; the client signs it."""

    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting for the Construction Manager"
        AT_RISK = "at_risk", "Approved, at risk until the client signs"
        SIGNED = "signed", "Signed by the client"
        REJECTED = "rejected", "Rejected"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="variations")
    number = models.CharField(max_length=20)
    description = models.CharField(max_length=300)
    value = models.DecimalField(max_digits=16, decimal_places=2)
    cost_estimate = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.WAITING)
    client_reference = models.CharField(max_length=60, blank=True)
    client_signed_on = models.DateField(null=True, blank=True)
    done_percent = models.DecimalField(max_digits=5, decimal_places=2, default="0.00")
    decision_note = models.CharField(max_length=200, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    requested_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="project_variation_demo_only"),
            models.CheckConstraint(condition=Q(cost_estimate__gte=0, done_percent__gte=0, done_percent__lte=100),
                                   name="project_variation_amounts"),
            models.UniqueConstraint(fields=["project", "number"], name="project_variation_number_once"),
        ]


class ProjectOrder(models.Model):
    """An order to a supplier or subcontractor for project items, checked against the items' approved budget.

    Within budget it is approved at once. Over budget, the Project Manager registers the excess with its reason;
    the Construction Manager approves it, then Finance approves it or asks for more details (Sam, 7 Nov 2026).
    """

    class Kind(models.TextChoices):
        SUPPLY = "supply", "Supplier order"
        SUBCONTRACT = "subcontract", "Subcontract"
        OTHER = "other", "Other project cost"

    class Status(models.TextChoices):
        MANAGER = "manager", "Over budget, waiting for the Construction Manager"
        FINANCE = "finance", "Over budget, waiting for Finance"
        DETAILS = "details", "Finance asked for more details"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="orders")
    number = models.CharField(max_length=20)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    supplier = models.CharField(max_length=160)
    description = models.CharField(max_length=300)
    lines = models.ManyToManyField(ProjectBoqLine, blank=True, related_name="orders")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    # The budget of its items still free when it was asked for, and what it goes over by.
    budget_left = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    over_budget = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    over_reason = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices)
    reference = models.CharField(max_length=80, blank=True)
    payment_due = models.DateField(null=True, blank=True)
    # Payment terms differ by supplier and agreement (Sam, 7 Nov 2026): instalments as
    # [{"label", "percent", "event", "due_on"}], the event being on order, before production, shipped,
    # delivered, installed, handed over or a date. Delivery is at once or after production and shipping.
    terms = models.JSONField(default=list, blank=True)
    delivery = models.CharField(max_length=12, default="immediate")
    expected_delivery = models.DateField(null=True, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    requested_at = models.DateTimeField(default=timezone.now)
    manager_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    manager_at = models.DateTimeField(null=True, blank=True)
    finance_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    finance_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)
    # Finance's question when it asks for more details, and the Project Manager's answer.
    finance_question = models.CharField(max_length=300, blank=True)
    details_answer = models.CharField(max_length=500, blank=True)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="project_order_demo_only"),
            models.CheckConstraint(condition=Q(amount__gt=0, budget_left__gte=0, over_budget__gte=0),
                                   name="project_order_amounts"),
            models.UniqueConstraint(fields=["project", "number"], name="project_order_number_once"),
        ]


class ProjectPayment(models.Model):
    """Money paid to a supplier or subcontractor on an approved project order (cash out), recorded by Finance."""

    order = models.ForeignKey(ProjectOrder, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    paid_on = models.DateField()
    reference = models.CharField(max_length=60)
    account = models.ForeignKey("CashAccount", on_delete=models.PROTECT, related_name="+")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(default=timezone.now)
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["paid_on", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="project_payment_demo_only"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="project_payment_positive"),
        ]


class ProjectClaim(models.Model):
    """A bill to the client: a monthly progress claim, or the release of retention after handover or the defects period."""

    class Kind(models.TextChoices):
        PROGRESS = "progress", "Progress claim"
        RETENTION = "retention", "Retention release"

    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting for the Construction Manager"
        APPROVED = "approved", "Approved, to submit to the consultant"
        SUBMITTED = "submitted", "With the consultant"
        CERTIFIED = "certified", "Certified by the consultant, waiting for Finance to invoice"
        INVOICED = "invoiced", "Invoiced"
        REJECTED = "rejected", "Rejected"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="claims")
    number = models.CharField(max_length=20)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.PROGRESS)
    period_end = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.WAITING)
    # Work done to date (BOQ after discount and signed variations, at their done %), less what earlier claims billed.
    work_to_date = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    previous = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    gross = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    retention = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    advance_recovery = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    retention_released = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    net = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    lines = models.JSONField(default=list, blank=True)
    note = models.CharField(max_length=200, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    prepared_at = models.DateTimeField(default=timezone.now)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    # The site team's payment request goes to the consultant, who certifies it (possibly for less).
    requested_gross = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    submitted_on = models.DateField(null=True, blank=True)
    consultant_reference = models.CharField(max_length=80, blank=True)
    certified_on = models.DateField(null=True, blank=True)
    certified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    invoice_number = models.CharField(max_length=20, blank=True)
    invoiced_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    invoiced_on = models.DateField(null=True, blank=True)
    due_on = models.DateField(null=True, blank=True)
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="project_claim_demo_only"),
            models.CheckConstraint(condition=Q(gross__gte=0, retention__gte=0, advance_recovery__gte=0,
                                               retention_released__gte=0, net__gte=0), name="project_claim_amounts"),
            models.UniqueConstraint(fields=["project", "number"], name="project_claim_number_once"),
        ]


class ProjectReceipt(models.Model):
    """Money the client paid (cash in): the advance, or payment of a claim invoice. Recorded by Finance."""

    class Kind(models.TextChoices):
        ADVANCE = "advance", "Advance"
        CLAIM = "claim", "Claim payment"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="receipts")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    claim = models.ForeignKey(ProjectClaim, null=True, blank=True, on_delete=models.PROTECT, related_name="receipts")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    received_on = models.DateField()
    reference = models.CharField(max_length=60)
    account = models.ForeignKey("CashAccount", on_delete=models.PROTECT, related_name="+")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(default=timezone.now)
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["received_on", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="project_receipt_demo_only"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="project_receipt_positive"),
            models.CheckConstraint(condition=Q(kind="advance", claim__isnull=True) | Q(kind="claim", claim__isnull=False),
                                   name="project_receipt_kind_claim"),
        ]


class ProjectBond(models.Model):
    """A bid bond, performance bond, guarantee cheque, advance payment guarantee or warranty, with its expiry."""

    class Kind(models.TextChoices):
        BID_BOND = "bid_bond", "Bid bond"
        PERFORMANCE = "performance", "Performance bond"
        ADVANCE_GUARANTEE = "advance", "Advance payment guarantee"
        GUARANTEE_CHEQUE = "cheque", "Guarantee cheque"
        WARRANTY = "warranty", "Warranty"
        RETENTION_GUARANTEE = "retention", "Retention guarantee"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RELEASED = "released", "Released back to us"
        CLAIMED = "claimed", "Claimed (cashed) by the client"
        EXPIRED = "expired", "Expired"

    tender = models.ForeignKey(Tender, null=True, blank=True, on_delete=models.CASCADE, related_name="bonds")
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.CASCADE, related_name="bonds")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    reference = models.CharField(max_length=80)
    issuer = models.CharField(max_length=120, blank=True)
    amount = models.DecimalField(max_digits=16, decimal_places=2, default="0.00")
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    note = models.CharField(max_length=300, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(default=timezone.now)
    is_synthetic = models.BooleanField(default=True, editable=False)

    class Meta:
        ordering = ["expires_on", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="project_bond_demo_only"),
            models.CheckConstraint(condition=Q(amount__gte=0), name="project_bond_not_negative"),
            models.CheckConstraint(condition=Q(tender__isnull=False) | Q(project__isnull=False), name="project_bond_owner"),
        ]

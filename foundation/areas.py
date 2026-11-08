"""Areas: what each person sees on the home page and in the menu (Sam, 5 Nov 2026).

Sam's group structure: the business areas are Tivoli Furniture (every Tivoli
showroom, textile and surface included), Roche Bobois, Natuzzi, Factory,
Projects & Fit-Out and Smart. The shared areas are Finance, Warehouse,
Logistics & Imports (a different team from the warehouse), Purchasing (IT makes
purchase orders today; a procurement team may later), HR & Admin and ERP setup.

A person sees only the sections of their areas. Areas tidy the screen; they do
not grant anything. Every page still checks the person's own permissions.

Who sees which area:
- The Master Admin always sees every area.
- Someone given "All areas" sees every area.
- Someone given areas on Users and permissions sees exactly those.
- Someone not given any yet sees the areas their current permissions point to,
  worked out generously so nobody testing Phase 1 loses a section they use.
"""

from django.db import transaction
from django.utils import timezone

from .access import active_grants
from .models import BusinessUnit, BusinessUnitRoleAssignment, LocationCapacityGrant, UserActionGrant, UserArea

Area = UserArea.Area
A = UserActionGrant.Action

# Sections every signed-in person keeps: the home page, messages, My Page and the HR portal (when switched on).
ALWAYS = ("dashboard", "chat", "my-page", "portal")

# key, kind, short description, sections, home business unit code, built yet
AREAS = [
    (Area.FURNITURE, "business", "Every Tivoli showroom, including textile and surface", ("retail",), "TVL", True),
    (Area.ROCHE_BOBOIS, "business", "Roche Bobois furniture showroom", ("retail",), "RBB", True),
    (Area.NATUZZI, "business", "Natuzzi furniture showroom", ("retail",), "NAT", True),
    (Area.FACTORY, "business", "Job orders, raw materials and workshop hours", ("factory",), None, True),
    (Area.PROJECTS, "business", "Fit-out projects from the signed BOQ, tenders, bonds and guarantees, cash in and out", ("projects",), None, True),
    (Area.SMART, "business", "Automation and AV systems", (), None, False),
    (Area.FINANCE, "shared", "Accounts, payments, bank and cash, budgets and reports", ("finance", "reports"), None, True),
    (Area.WAREHOUSE, "shared", "Stock, transfers, counts and delivery", ("inventory", "delivery"), None, True),
    (Area.LOGISTICS, "shared", "Imports, shipments and receiving goods", ("logistics", "procurement"), None, True),
    (Area.PURCHASING, "shared", "Purchase orders and suppliers", ("purchasing", "procurement"), None, True),
    (Area.HR, "shared", "Staff records, leave, attendance and payroll", ("hr",), None, True),
    (Area.SETUP, "shared", "Users, permissions and the ERP's setup", ("governance", "reports"), None, True),
]
AREA_KEYS = [row[0].value for row in AREAS]

SHOWROOM_UNITS = {"TVL": Area.FURNITURE, "RBB": Area.ROCHE_BOBOIS, "NAT": Area.NATUZZI}
# Everyone near stock can read it, so only making quotations and orders marks a seller.
SELLING = {A.CREATE_ORDER, A.APPROVE_REFUND}
FINANCE = {A.PAY_SUPPLIER, A.APPROVE_PAYMENT, A.VIEW_LEDGER, A.CLOSE_MONTH, A.MANAGE_CASH, A.APPROVE_BANK_REC,
           A.APPROVE_EXPENSE, A.PREPARE_BUDGET, A.APPROVE_BUDGET, A.MANAGE_ASSETS, A.APPROVE_ASSET,
           A.PREPARE_JOURNAL, A.APPROVE_JOURNAL, A.PREPARE_NOTE, A.APPROVE_NOTE, A.VIEW_COSTS}
WAREHOUSE = {A.COUNT_STOCK, A.APPROVE_COUNT, A.EXECUTE_TRANSFER, A.APPROVE_TRANSFER, A.APPROVE_WRITE_OFF,
             A.MANAGE_ITEMS, A.MANAGE_REORDER}
SUPPLIERS = {A.OPEN_SUPPLIER, A.APPROVE_SUPPLIER}
HR = {A.MANAGE_STAFF, A.VIEW_PAY, A.MANAGE_PAY, A.APPROVE_PAY, A.CHECK_INCENTIVES}
SETUP = {A.IMPORT_DATA, A.APPROVE_IMPORT}


def _from_permissions(user):
    """The areas a person's current permissions point to. Generous on purpose."""
    from .access_levels import can_manage_users

    areas = set()
    grants = list(active_grants(user).values_list("business_unit__code", "action"))
    actions = {action for _code, action in grants}
    for code, action in grants:
        if code in SHOWROOM_UNITS and action in SELLING:
            areas.add(SHOWROOM_UNITS[code])
    for code in BusinessUnitRoleAssignment.objects.filter(user=user, revoked_at__isnull=True) \
            .values_list("business_unit__code", flat=True):
        if code in SHOWROOM_UNITS:
            areas.add(SHOWROOM_UNITS[code])
    if actions & FINANCE:
        areas.add(Area.FINANCE)
    # Until areas are ticked, the warehouse team keeps the receiving pages it uses today.
    if actions & WAREHOUSE or LocationCapacityGrant.objects.filter(user=user, revoked_at__isnull=True).exists():
        areas.update({Area.WAREHOUSE, Area.LOGISTICS})
    if actions & SUPPLIERS:
        areas.update({Area.PURCHASING, Area.LOGISTICS})
    if actions & HR:
        areas.add(Area.HR)
    if any("factory" in action for action in actions):
        areas.add(Area.FACTORY)
    if any("projects" in action for action in actions):
        areas.add(Area.PROJECTS)
    if actions & SETUP or user.is_staff or can_manage_users(user):
        areas.add(Area.SETUP)
    if user.is_staff:
        # IT staff make the purchase orders today.
        areas.add(Area.PURCHASING)
    if not areas and A.READ_STOCK in actions:
        # Someone who can only look at stock keeps their showroom and the stock pages they use today.
        areas.update(SHOWROOM_UNITS[code] for code, action in grants if action == A.READ_STOCK and code in SHOWROOM_UNITS)
        areas.add(Area.WAREHOUSE)
    return {area.value if hasattr(area, "value") else area for area in areas}


def chosen_areas(user):
    return set(UserArea.objects.filter(user=user).values_list("area", flat=True))


def areas_for(user):
    """(area keys, how they were decided) for one person."""
    if user.is_superuser:
        return set(AREA_KEYS), "all"
    chosen = chosen_areas(user)
    if Area.ALL.value in chosen:
        return set(AREA_KEYS), "all"
    if chosen:
        return chosen & set(AREA_KEYS), "chosen"
    return _from_permissions(user), "from_permissions"


def my_areas(user):
    """What the home page and the menu need: the person's areas and the sections they open."""
    keys, source = areas_for(user)
    units = {unit.code: unit for unit in BusinessUnit.objects.filter(code__in=[r[4] for r in AREAS if r[4]], is_active=True)}
    rows, sections = [], set(ALWAYS)
    for area, kind, description, area_sections, unit_code, built in AREAS:
        if area.value not in keys:
            continue
        unit = units.get(unit_code) if unit_code else None
        rows.append({
            "key": area.value, "name": area.label, "kind": kind, "description": description,
            "sections": list(area_sections), "coming_soon": not built,
            "unit": {"id": unit.pk, "code": unit.code, "name": unit.name} if unit else None,
        })
        sections.update(area_sections)
    from .reporting_services import has_report_destination_access

    if has_report_destination_access(actor=user):
        # Reports given through a reporting role stay open, whatever the person's areas.
        sections.add("reports")
    return {"areas": rows, "sections": sorted(sections), "source": source}


def area_choices():
    return [{"key": area.value, "name": area.label, "kind": kind, "description": description, "coming_soon": not built}
            for area, kind, description, _sections, _unit, built in AREAS]


def person_areas(person):
    """For Users and permissions: what is ticked, and what the person sees today."""
    keys, source = areas_for(person)
    return {
        "choices": area_choices(),
        "chosen": sorted(chosen_areas(person)),
        "all": Area.ALL.value in chosen_areas(person) or person.is_superuser,
        "seen": sorted(keys),
        "source": source,
    }


@transaction.atomic
def set_area(*, actor, person, area, enabled):
    """Tick or untick one area (or "all") for a person. Returns True when something changed."""
    from .purchasing_services import WorkflowPermissionDenied
    from .transaction_services import InvalidTransaction

    if area not in Area.values:
        raise InvalidTransaction("Choose an area.")
    if person.is_superuser:
        raise WorkflowPermissionDenied("The Master Admin always sees every area.")
    if not UserArea.objects.select_for_update().filter(user=person).exists() and area != Area.ALL.value:
        # The first change starts from the areas the person sees today (worked out
        # from their permissions), so one tick does not hide everything else.
        for key in sorted(_from_permissions(person)):
            UserArea.objects.create(user=person, area=key, granted_by=actor, granted_at=timezone.now())
    current = UserArea.objects.select_for_update().filter(user=person, area=area).first()
    if enabled and current is None:
        UserArea.objects.create(user=person, area=area, granted_by=actor, granted_at=timezone.now())
        return True
    if not enabled and current is not None:
        others = UserArea.objects.filter(user=person).exclude(pk=current.pk)
        if not others.exists() and area != Area.ALL.value:
            raise InvalidTransaction("Leave at least one area ticked. To stop someone using the ERP, switch their account off.")
        current.delete()
        # Unticking "All areas" with nothing else ticked goes back to the areas their permissions point to.
        return True
    return False

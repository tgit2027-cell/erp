"""Share links for reports.

A link carries a panel and filters, never report rows. The person who opens it
gets the report rebuilt under their own panel and data grants, so sharing can
never show someone data they could not already open themselves. Creating a link
needs the export permission for the report's scope, because a link is a way of
sending a report out.
"""

import hashlib
import secrets
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import ReportShareLink, ReportShareLinkOpen
from .reporting_services import (
    ReportingPermissionDenied,
    ReportingValidationError,
    _require_synthetic_user,
    build_report,
    require_report_export,
)

LINK_LIFETIME = timedelta(days=7)
FILTER_KEYS = ("period", "start", "end", "unit", "company", "location")
NOT_ALLOWED = "This shared report covers data you are not allowed to see. Ask the sender or a reporting administrator."


class ShareLinkNotFound(ReportingValidationError):
    pass


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _stored_filters(report):
    """The filters the report was actually built with, so the link opens the same view."""
    scope = report["scope"]
    filters = {
        "period": report["granularity"],
        "start": str(report["filters"]["start"]),
        "end": str(report["filters"]["end"]),
    }
    if scope.get("scope_type") == "company" and scope.get("company"):
        filters["company"] = scope["company"]["id"]
    elif scope.get("business_unit"):
        filters["unit"] = scope["business_unit"]["id"]
    if report["filters"].get("location_id"):
        filters["location"] = report["filters"]["location_id"]
    return filters


def serialize_link(link, *, now=None):
    now = now or timezone.now()
    if link.revoked_at:
        state = "revoked"
    elif link.expires_at <= now:
        state = "expired"
    else:
        state = "active"
    return {
        "id": link.pk,
        "panel": link.panel,
        "panel_label": link.get_panel_display(),
        "filters": link.filters,
        "note": link.note,
        "created_at": link.created_at.isoformat(),
        "expires_at": link.expires_at.isoformat(),
        "revoked_at": link.revoked_at.isoformat() if link.revoked_at else None,
        "open_count": link.open_count,
        "last_opened_at": link.last_opened_at.isoformat() if link.last_opened_at else None,
        "state": state,
    }


@transaction.atomic
def create_share_link(*, actor, panel, filters, note=""):
    _require_synthetic_user(actor)
    note = (note or "").strip()
    if len(note) > 200:
        raise ReportingValidationError("Keep the note to 200 characters or fewer.")
    query = {key: filters[key] for key in FILTER_KEYS if filters.get(key) not in (None, "")}
    report = build_report(actor=actor, panel=panel, query_params=query, paginate=False)
    stored = _stored_filters(report)
    if "unit" not in stored and "company" not in stored:
        raise ReportingValidationError("Choose a business unit or a company before sharing a report.")
    require_report_export(actor=actor, report=report)
    token = secrets.token_urlsafe(32)
    now = timezone.now()
    link = ReportShareLink.objects.create(
        token_hash=_hash(token),
        created_by=actor,
        panel=panel,
        filters=stored,
        note=note,
        created_at=now,
        expires_at=now + LINK_LIFETIME,
    )
    return link, token


def my_share_links(*, actor):
    _require_synthetic_user(actor)
    now = timezone.now()
    return [serialize_link(link, now=now) for link in ReportShareLink.objects.filter(created_by=actor)[:50]]


@transaction.atomic
def revoke_share_link(*, actor, link_id):
    _require_synthetic_user(actor)
    link = ReportShareLink.objects.select_for_update().filter(pk=link_id, created_by=actor).first()
    if link is None:
        raise ShareLinkNotFound("That share link was not found among your links.")
    if link.revoked_at is None:
        link.revoked_at = timezone.now()
        link.save(update_fields=["revoked_at"])
    return link


def open_share_link(*, actor, token):
    """Check a link and rebuild its report as the person opening it.

    Returns the panel and filters for the reports screen to apply. Every attempt
    on a live link is logged, including refusals.
    """
    _require_synthetic_user(actor)
    link = ReportShareLink.objects.select_related("created_by").filter(token_hash=_hash(token or "")).first()
    if link is None:
        raise ShareLinkNotFound("This share link is not valid. Check that the whole link was copied.")
    now = timezone.now()
    if link.revoked_at:
        raise ReportingValidationError("This share link was withdrawn by the person who sent it.")
    if link.expires_at <= now:
        raise ReportingValidationError("This share link has expired. Ask the sender for a new one.")
    allowed = True
    try:
        build_report(actor=actor, panel=link.panel, query_params=link.filters, paginate=False)
    except ReportingPermissionDenied:
        allowed = False
    with transaction.atomic():
        ReportShareLinkOpen.objects.create(link=link, user=actor, allowed=allowed, opened_at=now)
        ReportShareLink.objects.filter(pk=link.pk).update(open_count=F("open_count") + 1, last_opened_at=now)
    if not allowed:
        raise ReportingPermissionDenied(NOT_ALLOWED)
    return {
        "panel": link.panel,
        "panel_label": link.get_panel_display(),
        "filters": link.filters,
        "note": link.note,
        "shared_by": link.created_by.get_full_name() or link.created_by.get_username(),
        "created_at": link.created_at.isoformat(),
        "expires_at": link.expires_at.isoformat(),
    }

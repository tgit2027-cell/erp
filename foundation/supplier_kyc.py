"""Supplier onboarding (KYC): IT opens a local supplier, a manager approves it.

Opening and approving are separate permissions, granted on the user permissions
screen. A grant in any business unit counts, because suppliers are shared by the
whole company (provisional). The approver can never be the person who opened
the file. Purchase commitments and receipts refuse a supplier whose KYC file is
not approved; suppliers opened before KYC existed have no file and are not
blocked (provisional).
"""

import datetime
import hashlib
import re
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .access import active_grants
from .models import (
    DemoUserMarker,
    SupplierKyc,
    SupplierKycDocument,
    SupplierKycEvent,
    SyntheticCounterparty,
    UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied, require_supplier_approved  # noqa: F401
from .transaction_services import InvalidTransaction

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
ALLOWED_DOCUMENTS = {
    ".pdf": ("application/pdf", (b"%PDF-",)),
    ".png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    ".jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
}
EDITABLE = (SupplierKyc.Status.DRAFT, SupplierKyc.Status.REJECTED)
TEXT_FIELDS = {
    "legal_name": 160, "address": 300, "cr_number": 40, "vat_number": 40,
    "signatory_name": 120, "signatory_title": 120, "contact_email": 160, "contact_phone": 40,
}


def _demo_user(user):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Supplier onboarding runs only in the synthetic demo environment.")
    if not getattr(user, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can use supplier onboarding.")


def can_open(user):
    return active_grants(user, UserActionGrant.Action.OPEN_SUPPLIER).filter(business_unit__is_active=True).exists()


def can_approve(user):
    return active_grants(user, UserActionGrant.Action.APPROVE_SUPPLIER).filter(business_unit__is_active=True).exists()


def _require_opener(user):
    _demo_user(user)
    if not can_open(user):
        raise WorkflowPermissionDenied("You do not have permission to open suppliers. Ask a system administrator.")


def _require_viewer(user):
    _demo_user(user)
    if not (user.is_superuser or can_open(user) or can_approve(user)):
        raise WorkflowPermissionDenied("You do not have access to supplier onboarding.")


def _text(data, field, *, required=False):
    value = data.get(field, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise InvalidTransaction(f"{field.replace('_', ' ').capitalize()} must be text.")
    value = " ".join(value.split())
    if required and not value:
        raise InvalidTransaction(f"{field.replace('_', ' ').capitalize()} is required.")
    if len(value) > TEXT_FIELDS[field]:
        raise InvalidTransaction(f"{field.replace('_', ' ').capitalize()} is too long.")
    return value


def _details(data):
    vat_registered = data.get("vat_registered", False)
    if type(vat_registered) is not bool:
        raise InvalidTransaction("Say whether the supplier is VAT registered.")
    details = {
        "legal_name": _text(data, "legal_name", required=True),
        "address": _text(data, "address"),
        "cr_number": _text(data, "cr_number", required=True).upper(),
        "vat_registered": vat_registered,
        "vat_number": _text(data, "vat_number", required=vat_registered).upper() if vat_registered else "",
        "signatory_name": _text(data, "signatory_name", required=True),
        "signatory_title": _text(data, "signatory_title"),
        "contact_email": _text(data, "contact_email"),
        "contact_phone": _text(data, "contact_phone"),
    }
    if not re.fullmatch(r"[A-Z0-9/-]{3,40}", details["cr_number"]):
        raise InvalidTransaction("CR number may use letters, digits, / and - only.")
    if details["contact_email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", details["contact_email"]):
        raise InvalidTransaction("Contact email does not look like an email address.")
    expiry = data.get("cr_expiry") or None
    if expiry is not None:
        try:
            expiry = datetime.date.fromisoformat(expiry)
        except (TypeError, ValueError):
            raise InvalidTransaction("CR expiry must be a date (YYYY-MM-DD).") from None
    details["cr_expiry"] = expiry
    return details


def _cr_taken(cr_number, exclude=None):
    query = SupplierKyc.objects.filter(cr_number=cr_number)
    if exclude is not None:
        query = query.exclude(pk=exclude.pk)
    return query.exists()


def _log(kyc, actor, event, note=""):
    SupplierKycEvent.objects.create(kyc=kyc, actor=actor, event=event, note=note[:500])


@transaction.atomic
def open_supplier(*, actor, data):
    _require_opener(actor)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the supplier details.")
    code = data.get("code")
    code = code.strip().upper() if isinstance(code, str) else ""
    if not re.fullmatch(r"TEST-SUP-[A-Z0-9-]+", code) or len(code) > 40:
        raise InvalidTransaction("Synthetic supplier codes must begin with TEST-SUP- and use letters, digits and -.")
    if SyntheticCounterparty.objects.filter(party_type=SyntheticCounterparty.Type.SUPPLIER, code=code).exists():
        raise InvalidTransaction("A supplier with this code already exists.")
    details = _details(data)
    if _cr_taken(details["cr_number"]):
        raise InvalidTransaction("A supplier with this CR number is already on file.")
    supplier = SyntheticCounterparty.objects.create(
        party_type=SyntheticCounterparty.Type.SUPPLIER, code=code,
        name=f"TEST ONLY {details['legal_name']}"[:120],
    )
    kyc = SupplierKyc.objects.create(supplier=supplier, opened_by=actor, **details)
    _log(kyc, actor, "opened")
    return kyc_detail(actor=actor, kyc_id=kyc.pk)


@transaction.atomic
def update_supplier(*, actor, kyc_id, data):
    _require_opener(actor)
    kyc = _locked(kyc_id)
    if kyc.status not in EDITABLE:
        raise InvalidTransaction("Only a draft or rejected file can be changed.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the supplier details.")
    details = _details(data)
    if _cr_taken(details["cr_number"], exclude=kyc):
        raise InvalidTransaction("A supplier with this CR number is already on file.")
    for field, value in details.items():
        setattr(kyc, field, value)
    reopened = kyc.status == SupplierKyc.Status.REJECTED
    if reopened:
        # A corrected file goes back to draft; the rejection stays in the history.
        kyc.status, kyc.decided_by, kyc.decided_at, kyc.decision_note = SupplierKyc.Status.DRAFT, None, None, ""
    kyc.save()
    _log(kyc, actor, "reopened" if reopened else "edited")
    return kyc_detail(actor=actor, kyc_id=kyc.pk)


def _locked(kyc_id):
    kyc = SupplierKyc.objects.select_for_update().select_related("supplier").filter(pk=kyc_id, is_synthetic=True).first()
    if kyc is None:
        raise InvalidTransaction("Supplier file not found.")
    return kyc


def _check_upload(upload):
    name = Path(getattr(upload, "name", "") or "").name
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_DOCUMENTS:
        raise InvalidTransaction("Attach a PDF, PNG or JPG file.")
    size = getattr(upload, "size", 0) or 0
    if size <= 0:
        raise InvalidTransaction("The file is empty.")
    if size > MAX_DOCUMENT_BYTES:
        raise InvalidTransaction("Files can be at most 10 MB.")
    content_type, signatures = ALLOWED_DOCUMENTS[suffix]
    upload.seek(0)
    head = upload.read(16)
    if not any(head.startswith(signature) for signature in signatures):
        raise InvalidTransaction("The file's content does not match its type.")
    upload.seek(0)
    digest = hashlib.sha256()
    for chunk in upload.chunks():
        digest.update(chunk)
    upload.seek(0)
    return name[:200], content_type, size, digest.hexdigest()


@transaction.atomic
def add_document(*, actor, kyc_id, kind, upload):
    _require_opener(actor)
    kyc = _locked(kyc_id)
    if kyc.status not in EDITABLE:
        raise InvalidTransaction("Documents can be added only while the file is a draft or was rejected.")
    if kind not in SupplierKycDocument.Kind.values:
        raise InvalidTransaction("Choose what kind of document this is.")
    if upload is None:
        raise InvalidTransaction("Choose a file to attach.")
    name, content_type, size, sha256 = _check_upload(upload)
    document = SupplierKycDocument(
        kyc=kyc, kind=kind, original_name=name, content_type=content_type, size=size, sha256=sha256,
        uploaded_by=actor,
    )
    document.file.save(name, upload, save=False)
    document.save()
    _log(kyc, actor, "document_added", f"{SupplierKycDocument.Kind(kind).label}: {name}")
    return kyc_detail(actor=actor, kyc_id=kyc.pk)


def missing_documents(kyc):
    kinds = set(kyc.documents.values_list("kind", flat=True))
    needed = [SupplierKycDocument.Kind.CR_CERTIFICATE, SupplierKycDocument.Kind.SIGNATORY_ID]
    if kyc.vat_registered:
        needed.append(SupplierKycDocument.Kind.VAT_CERTIFICATE)
    return [kind for kind in needed if kind not in kinds]


@transaction.atomic
def submit_supplier(*, actor, kyc_id):
    _require_opener(actor)
    kyc = _locked(kyc_id)
    if kyc.status != SupplierKyc.Status.DRAFT:
        raise InvalidTransaction("Only a draft file can be sent for approval.")
    missing = missing_documents(kyc)
    if missing:
        labels = ", ".join(SupplierKycDocument.Kind(kind).label for kind in missing)
        raise InvalidTransaction(f"Attach these documents first: {labels}.")
    kyc.status, kyc.submitted_at = SupplierKyc.Status.SUBMITTED, timezone.now()
    kyc.save(update_fields=["status", "submitted_at"])
    _log(kyc, actor, "submitted")
    return kyc_detail(actor=actor, kyc_id=kyc.pk)


@transaction.atomic
def decide_supplier(*, actor, kyc_id, approve, note):
    _demo_user(actor)
    if not can_approve(actor):
        raise WorkflowPermissionDenied("You do not have permission to approve suppliers.")
    kyc = _locked(kyc_id)
    if kyc.status != SupplierKyc.Status.SUBMITTED:
        raise InvalidTransaction("Only a file waiting for approval can be approved or rejected.")
    if kyc.opened_by_id == actor.pk:
        raise WorkflowPermissionDenied("The person who opened the supplier cannot approve it.")
    if type(approve) is not bool:
        raise InvalidTransaction("Choose approve or reject.")
    note = " ".join(note.split()) if isinstance(note, str) else ""
    if not approve and not note:
        raise InvalidTransaction("Give a reason for the rejection.")
    if len(note) > 500:
        raise InvalidTransaction("The note is too long.")
    kyc.status = SupplierKyc.Status.APPROVED if approve else SupplierKyc.Status.REJECTED
    kyc.decided_by, kyc.decided_at, kyc.decision_note = actor, timezone.now(), note
    kyc.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
    _log(kyc, actor, "approved" if approve else "rejected", note)
    return kyc_detail(actor=actor, kyc_id=kyc.pk)


def _person(user):
    if user is None:
        return None
    return {"id": user.pk, "username": user.username, "name": user.get_full_name() or user.username}


def _row(kyc):
    return {
        "id": kyc.pk,
        "supplier": {"id": kyc.supplier_id, "code": kyc.supplier.code, "name": kyc.supplier.name},
        "legal_name": kyc.legal_name,
        "cr_number": kyc.cr_number,
        "vat_number": kyc.vat_number,
        "status": kyc.status,
        "status_label": SupplierKyc.Status(kyc.status).label,
        "opened_by": _person(kyc.opened_by),
        "opened_at": kyc.opened_at.isoformat(),
        "submitted_at": kyc.submitted_at.isoformat() if kyc.submitted_at else None,
        "decided_by": _person(kyc.decided_by),
        "decided_at": kyc.decided_at.isoformat() if kyc.decided_at else None,
    }


def kyc_overview(*, actor, status=None):
    _require_viewer(actor)
    query = SupplierKyc.objects.filter(is_synthetic=True).select_related("supplier", "opened_by", "decided_by")
    if status:
        if status not in SupplierKyc.Status.values:
            raise InvalidTransaction("Choose a known status.")
        query = query.filter(status=status)
    counts = {value: 0 for value in SupplierKyc.Status.values}
    for value in SupplierKyc.objects.values_list("status", flat=True):
        counts[value] += 1
    return {
        "permissions": {"can_open": can_open(actor), "can_approve": can_approve(actor)},
        "counts": counts,
        "document_kinds": [{"kind": kind, "label": label} for kind, label in SupplierKycDocument.Kind.choices],
        "files": [_row(kyc) for kyc in query[:200]],
    }


def kyc_detail(*, actor, kyc_id):
    _require_viewer(actor)
    kyc = SupplierKyc.objects.select_related("supplier", "opened_by", "decided_by").filter(
        pk=kyc_id, is_synthetic=True,
    ).first()
    if kyc is None:
        raise InvalidTransaction("Supplier file not found.")
    row = _row(kyc)
    row.update({
        "address": kyc.address,
        "cr_expiry": kyc.cr_expiry.isoformat() if kyc.cr_expiry else None,
        "vat_registered": kyc.vat_registered,
        "signatory_name": kyc.signatory_name,
        "signatory_title": kyc.signatory_title,
        "contact_email": kyc.contact_email,
        "contact_phone": kyc.contact_phone,
        "decision_note": kyc.decision_note,
        "missing_documents": [
            {"kind": kind, "label": SupplierKycDocument.Kind(kind).label} for kind in missing_documents(kyc)
        ],
        "documents": [
            {
                "id": document.pk,
                "kind": document.kind,
                "kind_label": SupplierKycDocument.Kind(document.kind).label,
                "name": document.original_name,
                "size": document.size,
                "uploaded_by": document.uploaded_by.username,
                "uploaded_at": document.uploaded_at.isoformat(),
            }
            for document in kyc.documents.select_related("uploaded_by")
        ],
        "history": [
            {"event": event.event, "actor": event.actor.username, "note": event.note, "at": event.occurred_at.isoformat()}
            for event in kyc.events.select_related("actor")
        ],
        "can_edit": kyc.status in EDITABLE and can_open(actor),
        "can_decide": (
            kyc.status == SupplierKyc.Status.SUBMITTED and can_approve(actor) and kyc.opened_by_id != actor.pk
        ),
    })
    return row


def document_for_download(*, actor, kyc_id, document_id):
    _require_viewer(actor)
    document = SupplierKycDocument.objects.filter(pk=document_id, kyc_id=kyc_id, is_synthetic=True).first()
    if document is None:
        raise InvalidTransaction("Document not found.")
    return document

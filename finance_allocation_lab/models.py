from django.db import models
from django.db.models import Q


class DraftCreateReceipt(models.Model):
    """An acknowledged create outcome, committed with its original draft."""
    actor = models.ForeignKey("auth.User", on_delete=models.PROTECT)
    legal_entity = models.ForeignKey("foundation.LegalEntity", on_delete=models.PROTECT)
    request_id = models.UUIDField()
    resource = models.CharField(max_length=40)
    fingerprint = models.CharField(max_length=64)
    response = models.JSONField(default=dict)
    status = models.PositiveSmallIntegerField(default=0)
    is_synthetic = models.BooleanField(default=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=("actor", "legal_entity", "request_id"),
                                    name="lab_create_request_identity"),
            models.CheckConstraint(condition=Q(is_synthetic=True), name="lab_create_receipt_synthetic"),
        ]


class AllocationProposal(models.Model):
    legal_entity = models.ForeignKey("foundation.LegalEntity", on_delete=models.PROTECT)
    payload = models.JSONField()
    revision = models.PositiveIntegerField(default=1)
    is_synthetic = models.BooleanField(default=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="lab_allocation_synthetic"),
            models.CheckConstraint(condition=Q(revision__gte=1), name="lab_allocation_revision_positive"),
        ]


class AllocationRevision(models.Model):
    proposal = models.ForeignKey(AllocationProposal, on_delete=models.PROTECT, related_name="revisions")
    revision = models.PositiveIntegerField()
    snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["proposal", "revision"], name="lab_allocation_revision_unique"),
        ]


class BudgetScenario(models.Model):
    """Disposable proposal only; never an operative budget or spending limit."""
    legal_entity = models.ForeignKey("foundation.LegalEntity", on_delete=models.PROTECT)
    payload = models.JSONField()
    revision = models.PositiveIntegerField(default=1)
    is_synthetic = models.BooleanField(default=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="lab_budget_synthetic"),
            models.CheckConstraint(condition=Q(revision__gte=1), name="lab_budget_revision_positive"),
        ]


class BudgetRevision(models.Model):
    scenario = models.ForeignKey(BudgetScenario, on_delete=models.PROTECT, related_name="revisions")
    revision = models.PositiveIntegerField()
    snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["scenario", "revision"], name="lab_budget_revision_unique"),
        ]


class ReconciliationProposal(models.Model):
    """Explicit synthetic comparisons, never operational reconciliation state."""
    legal_entity = models.ForeignKey("foundation.LegalEntity", on_delete=models.PROTECT)
    payload = models.JSONField()
    revision = models.PositiveIntegerField(default=1)
    is_synthetic = models.BooleanField(default=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="lab_recon_synthetic"),
            models.CheckConstraint(condition=Q(revision__gte=1), name="lab_recon_revision_positive"),
        ]


class ReconciliationRevision(models.Model):
    proposal = models.ForeignKey(ReconciliationProposal, on_delete=models.PROTECT, related_name="revisions")
    revision = models.PositiveIntegerField()
    snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["proposal", "revision"], name="lab_recon_revision_unique"),
        ]


class ExpenseProposal(models.Model):
    """Synthetic review inputs, never a claim approval or cash disbursement."""
    legal_entity = models.ForeignKey("foundation.LegalEntity", on_delete=models.PROTECT)
    payload = models.JSONField()
    revision = models.PositiveIntegerField(default=1)
    is_synthetic = models.BooleanField(default=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="lab_expense_synthetic"),
            models.CheckConstraint(condition=Q(revision__gte=1), name="lab_expense_revision_positive"),
        ]


class ExpenseRevision(models.Model):
    proposal = models.ForeignKey(ExpenseProposal, on_delete=models.PROTECT, related_name="revisions")
    revision = models.PositiveIntegerField()
    snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["proposal", "revision"], name="lab_expense_revision_unique"),
        ]


class FixedAssetDraft(models.Model):
    """Explicit synthetic proposals, not a capitalized asset subledger."""
    legal_entity = models.ForeignKey("foundation.LegalEntity", on_delete=models.PROTECT)
    payload = models.JSONField()
    revision = models.PositiveIntegerField(default=1)
    is_synthetic = models.BooleanField(default=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="lab_asset_synthetic"),
            models.CheckConstraint(condition=Q(revision__gte=1), name="lab_asset_revision_positive"),
        ]


class FixedAssetRevision(models.Model):
    draft = models.ForeignKey(FixedAssetDraft, on_delete=models.PROTECT, related_name="revisions")
    revision = models.PositiveIntegerField()
    snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["draft", "revision"], name="lab_asset_revision_unique"),
        ]


class CashForecastDraft(models.Model):
    """Explicit synthetic scenarios, not cash balances or payment instructions."""
    legal_entity = models.ForeignKey("foundation.LegalEntity", on_delete=models.PROTECT)
    payload = models.JSONField()
    revision = models.PositiveIntegerField(default=1)
    is_synthetic = models.BooleanField(default=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="lab_forecast_synthetic"),
            models.CheckConstraint(condition=Q(revision__gte=1), name="lab_forecast_revision_positive"),
        ]


class CashForecastRevision(models.Model):
    draft = models.ForeignKey(CashForecastDraft, on_delete=models.PROTECT, related_name="revisions")
    revision = models.PositiveIntegerField()
    snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["draft", "revision"], name="lab_forecast_revision_unique"),
        ]


class SupplierInvoiceDraft(models.Model):
    """Manual synthetic references, not supplier master records or payables."""
    legal_entity = models.ForeignKey("foundation.LegalEntity", on_delete=models.PROTECT)
    payload = models.JSONField()
    revision = models.PositiveIntegerField(default=1)
    is_synthetic = models.BooleanField(default=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="lab_invoice_synthetic"),
            models.CheckConstraint(condition=Q(revision__gte=1), name="lab_invoice_revision_positive"),
        ]


class SupplierInvoiceRevision(models.Model):
    draft = models.ForeignKey(SupplierInvoiceDraft, on_delete=models.PROTECT, related_name="revisions")
    revision = models.PositiveIntegerField()
    snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["draft", "revision"], name="lab_invoice_revision_unique"),
        ]


class SupplierAdjustmentDraft(models.Model):
    """Credit/debit review classifications, never signed payable adjustments."""
    legal_entity = models.ForeignKey("foundation.LegalEntity", on_delete=models.PROTECT)
    linked_invoice = models.ForeignKey(SupplierInvoiceDraft, null=True, blank=True, on_delete=models.PROTECT)
    invoice_reference_snapshot = models.JSONField(null=True)
    payload = models.JSONField()
    revision = models.PositiveIntegerField(default=1)
    is_synthetic = models.BooleanField(default=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="lab_supplier_adj_synthetic"),
            models.CheckConstraint(condition=Q(revision__gte=1), name="lab_supplier_adj_revision_positive"),
        ]


class SupplierAdjustmentRevision(models.Model):
    draft = models.ForeignKey(SupplierAdjustmentDraft, on_delete=models.PROTECT, related_name="revisions")
    revision = models.PositiveIntegerField()
    snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["draft", "revision"], name="lab_supplier_adj_revision_unique"),
        ]


class CustomerInvoiceDraft(models.Model):
    """Internal synthetic preparation, not customer master or receivables."""
    legal_entity = models.ForeignKey("foundation.LegalEntity", on_delete=models.PROTECT)
    payload = models.JSONField()
    revision = models.PositiveIntegerField(default=1)
    is_synthetic = models.BooleanField(default=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="lab_ar_invoice_synthetic"),
            models.CheckConstraint(condition=Q(revision__gte=1), name="lab_ar_invoice_revision_positive"),
        ]


class CustomerInvoiceRevision(models.Model):
    draft = models.ForeignKey(CustomerInvoiceDraft, on_delete=models.PROTECT, related_name="revisions")
    revision = models.PositiveIntegerField()
    snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["draft", "revision"], name="lab_ar_invoice_revision_unique"),
        ]


class CustomerCreditDraft(models.Model):
    """Internal review proposal; no issued note or signed receivable effect."""
    legal_entity = models.ForeignKey("foundation.LegalEntity", on_delete=models.PROTECT)
    payload = models.JSONField()
    linked_invoice = models.ForeignKey(CustomerInvoiceDraft, null=True, blank=True, on_delete=models.PROTECT)
    invoice_reference_snapshot = models.JSONField(null=True)
    revision = models.PositiveIntegerField(default=1)
    is_synthetic = models.BooleanField(default=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="lab_ar_credit_synthetic"),
            models.CheckConstraint(condition=Q(revision__gte=1), name="lab_ar_credit_revision_positive"),
        ]


class CustomerCreditRevision(models.Model):
    draft = models.ForeignKey(CustomerCreditDraft, on_delete=models.PROTECT, related_name="revisions")
    revision = models.PositiveIntegerField()
    snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["draft", "revision"], name="lab_ar_credit_revision_unique"),
        ]


class SupplierAdvanceDraft(models.Model):
    """Explicit unpaid, unapplied review proposal, not an operational advance."""
    legal_entity = models.ForeignKey("foundation.LegalEntity", on_delete=models.PROTECT)
    payload = models.JSONField()
    revision = models.PositiveIntegerField(default=1)
    is_synthetic = models.BooleanField(default=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="lab_supplier_advance_synthetic"),
            models.CheckConstraint(condition=Q(revision__gte=1), name="lab_supplier_advance_positive"),
        ]


class ReversalProposal(models.Model):
    """Explicit non-posting review linked to an immutable synthetic revision."""
    legal_entity = models.ForeignKey("foundation.LegalEntity", on_delete=models.PROTECT)
    source_record = models.ForeignKey("foundation.FinanceDraftJournalRevision", on_delete=models.PROTECT)
    payload = models.JSONField()
    revision = models.PositiveIntegerField(default=1)
    is_synthetic = models.BooleanField(default=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=Q(is_synthetic=True), name="lab_reversal_synthetic"),
            models.CheckConstraint(condition=Q(revision__gte=1), name="lab_reversal_positive"),
        ]


class ReversalRevision(models.Model):
    draft = models.ForeignKey(ReversalProposal, on_delete=models.PROTECT, related_name="revisions")
    source_record = models.ForeignKey("foundation.FinanceDraftJournalRevision", on_delete=models.PROTECT)
    revision = models.PositiveIntegerField()
    snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["draft", "revision"], name="lab_reversal_revision_unique"),
        ]


class SupplierAdvanceRevision(models.Model):
    draft = models.ForeignKey(SupplierAdvanceDraft, on_delete=models.PROTECT, related_name="revisions")
    revision = models.PositiveIntegerField()
    snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["draft", "revision"], name="lab_supplier_advance_rev_unique"),
        ]

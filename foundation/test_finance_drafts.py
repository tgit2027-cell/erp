from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.db import connection
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import Resolver404, resolve

from .finance_drafts import (
    FinanceDraftValidationError,
    create_finance_draft_account,
    create_finance_draft_dimension,
    create_finance_draft_dimension_value,
    create_finance_draft_journal,
    review_finance_draft_journal,
)
from .models import (
    FinanceDraftAccount,
    FinanceDraftDimension,
    FinanceDraftJournal,
    FinanceDraftJournalLine,
    IdempotencyRecord,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    StockBalance,
    StockMovement,
)
from .test_transactions import TransactionFixtureMixin


@override_settings(APP_ENV="demo", FINANCE_DRAFT_TEST_MODE=True)
class FinanceDraftFoundationTests(TransactionFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_synthetic_catalog()

    def setUp(self):
        self.posted_baseline = {
            model.__name__: model.objects.count()
            for model in (
                LedgerAccount, JournalEntry, JournalLine, IdempotencyRecord,
                StockBalance, StockMovement,
            )
        }

    def make_account(self, suffix="CASH"):
        return create_finance_draft_account(
            legal_entity=self.legal_entity,
            code=f"TEST-DRAFT-ACCT-{suffix}",
            name_en=f"Synthetic {suffix.title()} proposal",
            name_ar="حساب تجريبي",
        )

    def test_accounts_dimensions_and_balanced_decimal_draft_stay_separate_from_posted_ledger(self):
        account = self.make_account()
        dimension = create_finance_draft_dimension(
            legal_entity=self.legal_entity,
            code="TEST-DRAFT-DIM-BU",
            label_en="Business unit",
            label_ar="وحدة الأعمال",
        )
        value = create_finance_draft_dimension_value(
            dimension=dimension,
            code="TEST-DRAFT-VAL-TVL",
            label_en="Synthetic showroom",
            label_ar="صالة عرض تجريبية",
        )
        journal = create_finance_draft_journal(
            legal_entity=self.legal_entity,
            title_en="Synthetic decimal review",
            title_ar="مراجعة عشرية تجريبية",
            lines=[
                {
                    "account": account,
                    "business_unit": self.business_unit,
                    "dimensions": [value],
                    "debit": "0.30",
                    "credit": "0.00",
                    "memo_en": "Test debit",
                },
                {
                    "account": account,
                    "business_unit": self.business_unit,
                    "dimensions": [value],
                    "debit": "0.00",
                    "credit": Decimal("0.10"),
                    "memo_ar": "دائن تجريبي",
                },
                {
                    "account": account,
                    "business_unit": self.business_unit,
                    "dimensions": [value],
                    "debit": "0.00",
                    "credit": "0.20",
                },
            ],
        )
        with CaptureQueriesContext(connection) as queries:
            review = review_finance_draft_journal(journal)
        self.assertFalse(any(
            query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
            for query in queries.captured_queries
        ), "Review is read-only.")

        self.assertEqual(review, {
            "state": "draft",
            "currency": "QAR",
            "synthetic_only": True,
            "posted": False,
            "balanced": True,
            "debit_total": "0.30",
            "credit_total": "0.30",
            "difference": "0.00",
            "issues": [],
        })
        self.assertEqual(journal.state, FinanceDraftJournal.State.DRAFT)
        self.assertEqual(journal.lines.count(), 3)
        self.assertEqual(journal.lines.first().dimensions.get(), value)
        self.assertTrue(account.is_synthetic and dimension.is_synthetic and value.is_synthetic)
        self.assertEqual(
            {
                model.__name__: model.objects.count()
                for model in (
                    LedgerAccount, JournalEntry, JournalLine, IdempotencyRecord,
                    StockBalance, StockMovement,
                )
            },
            self.posted_baseline,
        )

    def test_unbalanced_and_incomplete_drafts_are_reviewed_without_being_posted(self):
        account = self.make_account()
        unbalanced = create_finance_draft_journal(
            legal_entity=self.legal_entity,
            title_en="Needs correction",
            lines=[
                {"account": account, "debit": "10.00", "credit": "0.00"},
                {"account": account, "debit": "0.00", "credit": "9.99"},
            ],
        )
        review = review_finance_draft_journal(unbalanced)
        self.assertFalse(review["balanced"])
        self.assertFalse(review["posted"])
        self.assertIn("totals_not_balanced", {item["code"] for item in review["issues"]})

        incomplete = create_finance_draft_journal(
            legal_entity=self.legal_entity,
            title_en="Incomplete entry",
            lines=[{"account": account, "debit": "1.00", "credit": "1.00"}],
        )
        incomplete_review = review_finance_draft_journal(incomplete)
        self.assertFalse(incomplete_review["balanced"])
        codes = {item["code"] for item in incomplete_review["issues"]}
        self.assertIn("at_least_two_lines", codes)
        self.assertTrue(any(code.endswith("_one_side_only") for code in codes))
        self.assertEqual(JournalEntry.objects.count(), self.posted_baseline["JournalEntry"])

    def test_float_negative_excess_precision_and_cross_entity_references_are_rejected_atomically(self):
        account = self.make_account()
        before = FinanceDraftJournal.objects.count()
        for bad_amount in (0.1, "-1.00", "1.001", "NaN"):
            with self.subTest(amount=bad_amount):
                with self.assertRaises(FinanceDraftValidationError):
                    create_finance_draft_journal(
                        legal_entity=self.legal_entity,
                        title_en="Rejected amount",
                        lines=[{"account": account, "debit": bad_amount, "credit": "0.00"}],
                    )
        other_entity = type(self.legal_entity).objects.create(
            code="TEST-OTHER-ENTITY",
            name="TEST ONLY other entity",
            is_synthetic=True,
        )
        with self.assertRaisesMessage(FinanceDraftValidationError, "same synthetic legal entity"):
            create_finance_draft_journal(
                legal_entity=other_entity,
                title_en="Cross-entity account attempt",
                lines=[{"account": account, "debit": "1.00"}],
            )
        self.assertEqual(FinanceDraftJournal.objects.count(), before)
        self.assertEqual(JournalEntry.objects.count(), self.posted_baseline["JournalEntry"])

    def test_database_constraints_keep_records_synthetic_test_coded_and_draft_only(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            FinanceDraftAccount.objects.create(
                legal_entity=self.legal_entity,
                code="REAL-ACCOUNT-CODE",
                name_en="Must not be accepted",
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            FinanceDraftDimension.objects.create(
                legal_entity=self.legal_entity,
                code="REAL-DIMENSION-CODE",
                label_en="Must not be accepted",
            )
        account = self.make_account()
        journal = create_finance_draft_journal(
            legal_entity=self.legal_entity,
            title_en="Unposted state check",
            lines=[{"account": account, "debit": "1.00"}],
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            FinanceDraftJournal.objects.filter(pk=journal.pk).update(state="posted")
        with self.assertRaises(IntegrityError), transaction.atomic():
            FinanceDraftJournalLine.objects.filter(journal=journal).update(is_synthetic=False)

    @override_settings(APP_ENV="development")
    def test_draft_services_fail_closed_outside_explicit_synthetic_demo_mode(self):
        with self.assertRaisesMessage(FinanceDraftValidationError, "synthetic demo"):
            create_finance_draft_account(
                legal_entity=self.legal_entity,
                code="TEST-DRAFT-ACCT-CLOSED",
                name_en="Closed environment test",
            )

    def test_no_shared_finance_api_route_is_registered(self):
        with self.assertRaises(Resolver404):
            resolve("/api/finance/drafts/")
        self.assertFalse(Permission.objects.filter(content_type__model__startswith="financedraft").exists())

    @override_settings(FINANCE_DRAFT_TEST_MODE=False)
    def test_demo_mode_alone_does_not_enable_persistence_or_create_access(self):
        with self.assertRaisesMessage(FinanceDraftValidationError, "isolated PostgreSQL test databases"):
            self.make_account()
        self.assertEqual(FinanceDraftAccount.objects.count(), 0)

    def test_line_insert_failure_rolls_back_the_entire_draft(self):
        account = self.make_account()
        original = FinanceDraftJournalLine.objects.create
        calls = 0

        def fail_second_line(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise IntegrityError("Synthetic forced draft insert failure")
            return original(**kwargs)

        with patch.object(FinanceDraftJournalLine.objects, "create", side_effect=fail_second_line):
            with self.assertRaisesMessage(IntegrityError, "Synthetic forced"):
                create_finance_draft_journal(
                    legal_entity=self.legal_entity,
                    title_en="Atomic rollback draft",
                    lines=[
                        {"account": account, "debit": "1.00", "credit": "0.00"},
                        {"account": account, "debit": "0.00", "credit": "1.00"},
                    ],
                )
        self.assertEqual(FinanceDraftJournal.objects.count(), 0)
        self.assertEqual(FinanceDraftJournalLine.objects.count(), 0)
        self.assertEqual(JournalEntry.objects.count(), self.posted_baseline["JournalEntry"])

    def test_business_unit_and_dimension_values_must_stay_in_one_synthetic_legal_entity(self):
        account = self.make_account()
        other_entity = type(self.legal_entity).objects.create(
            code="TEST-DRAFT-OTHER", name="TEST ONLY other draft entity", is_synthetic=True,
        )
        other_unit = type(self.business_unit).objects.create(
            legal_entity=other_entity, code="test-draft-other-bu", name="TEST ONLY other BU",
        )
        other_dimension = create_finance_draft_dimension(
            legal_entity=other_entity, code="TEST-DRAFT-DIM-OTHER", label_en="Other dimension",
        )
        other_value = create_finance_draft_dimension_value(
            dimension=other_dimension, code="TEST-DRAFT-VAL-OTHER", label_en="Other value",
        )
        for extra in ({"business_unit": other_unit}, {"dimensions": [other_value]}):
            with self.subTest(extra=tuple(extra)):
                with self.assertRaisesMessage(FinanceDraftValidationError, "outside the synthetic legal entity"):
                    create_finance_draft_journal(
                        legal_entity=self.legal_entity,
                        title_en="Rejected cross-entity dimensions",
                        lines=[{"account": account, "debit": "1.00", **extra}],
                    )
        self.assertEqual(FinanceDraftJournal.objects.count(), 0)


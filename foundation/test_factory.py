"""Factory job orders (Phase 2): the switch, approvals, materials, hours, purchases, costing, stock and the ledger."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .factory import (
    create_order,
    decide_material_request,
    factory_overview,
    material_request_slip,
    order_action,
    purchase_action,
    read_boq_sheet,
    receive_materials,
    record_hours,
    request_purchase,
    save_raw_material,
    take_materials,
    update_settings,
    visibility,
)
from .models import (
    CashAccount,
    DemoUserMarker,
    Employee,
    FactorySettings,
    JournalLine,
    Location,
    ProductionOrder,
    SalesQuote,
    StockBalance,
    StockMovement,
    StockTransferRequest,
    UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied
from .stock_transfer_requests import (
    decide_transfer_request,
    dispatch_transfer_request,
    receive_transfer_request,
    schedule_transfer_request,
)
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action
S = ProductionOrder.Status
TODAY = timezone.localdate


class FactoryFixtureMixin(PurchasingFixtureMixin):
    """Users, stores, a carpenter, a card and 20 sheets of material; shared with the planning tests."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        cls.master = get_user_model().objects.create_user(
            username="test_factory_master", password="unused-test-password", is_staff=True, is_superuser=True)
        DemoUserMarker.objects.create(user=cls.master)
        cls.runner, cls.manager, cls.finance = cls.sales, cls.admin, cls.roche_user
        UserActionGrant.objects.get_or_create(user=cls.runner, business_unit=cls.unit, action=A.RUN_FACTORY)
        UserActionGrant.objects.get_or_create(user=cls.manager, business_unit=cls.unit, action=A.APPROVE_FACTORY)
        UserActionGrant.objects.get_or_create(user=cls.finance, business_unit=cls.unit, action=A.APPROVE_FACTORY_COST)
        UserActionGrant.objects.get_or_create(user=cls.finance, business_unit=cls.unit, action=A.BUY_FACTORY)
        cls.store = Location.objects.get(code="W41-04.2")   # the factories' raw materials store
        cls.warehouse = Location.objects.get(code="W41-01")
        cls.factory = Location.objects.get(code="W41-FAC")  # where the warehouse delivers the factory's materials
        # The warehouse team moves stock (schedule, dispatch, receive) as it does for furniture.
        cls.storekeeper = cls.finance
        UserActionGrant.objects.get_or_create(user=cls.storekeeper, business_unit=cls.unit, action=A.EXECUTE_TRANSFER)
        # The warehouse admin approves the factory's request slips after the Factory Manager.
        UserActionGrant.objects.get_or_create(user=cls.storekeeper, business_unit=cls.unit, action=A.APPROVE_TRANSFER)
        cls.worker = Employee.objects.create(
            business_unit=cls.unit, staff_number="TEST-F-001", full_name="TEST ONLY Carpenter", job_title="Carpenter",
            department="Factory", nationality="Testland", joined_on=datetime.date(2024, 1, 1), created_by=cls.admin)
        cls.cash = CashAccount.objects.create(business_unit=cls.unit, name="TEST ONLY factory card", kind="bank", account_type="other",
                                              opening_date=datetime.date(2024, 1, 1), created_by=cls.admin)
        cls.product, cls.material = cls.variant, cls.alternate_variant

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        # 20 sheets of the material in the raw materials store at 50.00 each.
        self.make_receipt(key="factory-material", variant=self.material, location=self.store, quantity="20.00",
                          unit_cost="50.00", charges=[])

    def go_live(self):
        update_settings(actor=self.master, business_unit=self.unit, data={"is_live": True})

    def sales_order(self, key="factory-order"):
        self.make_receipt(key=f"{key}-stock")
        quote = SalesQuote.objects.get(pk=self.make_quote(key=key)["quote_id"])
        self.pay(quote, key=f"{key}-pay", amount="1000.00")
        return quote

    def job(self, **extra):
        data = {"purpose": "fitout", "quote_id": self.sales_order().pk, "project_name": "TEST ONLY Villa fit-out",
                "design_reference": "DP-12 rev B",
                "due_date": (TODAY() + datetime.timedelta(days=14)).isoformat(),
                "boq": [{"code": "JD-00", "description": "TEST ONLY oak door", "quantity": "2"},
                        {"code": "jd-01", "description": "TEST ONLY wall cladding", "quantity": "12", "unit": "m2"}],
                "materials": [{"variant_id": self.material.pk, "quantity": "6"}],
                "planned_hours": {"joinery": "10"}} | extra
        return create_order(actor=self.runner, business_unit=self.unit, data=data)

    def approved_job(self, **extra):
        row = self.job(**extra)
        order_action(actor=self.runner, business_unit=self.unit, order_id=row["id"], action="submit")
        return order_action(actor=self.manager, business_unit=self.unit, order_id=row["id"], action="approve")

    def hours(self, row, hours, **extra):
        return record_hours(actor=self.runner, business_unit=self.unit, order_id=row["id"],
                            data={"workshop": "joinery", "employee_id": self.worker.pk, "work_date": TODAY().isoformat(),
                                  "hours": hours} | extra)

    def take(self, row, quantity, **extra):
        return take_materials(actor=self.runner, business_unit=self.unit, order_id=row["id"],
                              data={"variant_id": self.material.pk, "location_id": self.store.pk,
                                    "deliver_to_id": self.factory.pk, "quantity": quantity} | extra)

    def approve_slip(self, row):
        """The Factory Manager, then the warehouse admin, approve the job's latest material request slip."""
        slip = row["material_requests"][0]
        if slip["status"] == "manager":
            row = decide_material_request(actor=self.manager, business_unit=self.unit, request_id=slip["id"],
                                          action="approve")
        request = StockTransferRequest.objects.get(pk=row["material_requests"][0]["transfer_id"])
        if request.status == "submitted":
            decide_transfer_request(actor=self.storekeeper, transfer_request=request,
                                    idempotency_key=f"ok-{request.pk}", approve=True)
        return self.row(row)

    def row(self, row):
        return next(order for order in factory_overview(actor=self.runner, business_unit=self.unit)["orders"]
                    if order["id"] == row["id"])

    def warehouse_sends(self, row):
        """The warehouse schedules and dispatches the job's latest material request, like any transfer."""
        row = self.approve_slip(row)
        request = StockTransferRequest.objects.get(pk=row["issues"][-1]["request_id"])
        schedule_transfer_request(actor=self.storekeeper, transfer_request=request, idempotency_key=f"plan-{request.pk}",
                                  transfer_date=TODAY().isoformat())
        dispatch_transfer_request(actor=self.storekeeper, transfer_request=request, idempotency_key=f"send-{request.pk}")
        return request

    def deliver(self, row):
        request = self.warehouse_sends(row)
        return receive_materials(actor=self.runner, business_unit=self.unit, request_id=request.pk)



@override_settings(APP_ENV="demo")
class FactoryTests(FactoryFixtureMixin, TestCase):
    def test_hidden_until_the_master_admin_switches_it_on(self):
        self.assertEqual(visibility(self.runner), {"can_see_factory": False, "factory_live": False})
        self.assertTrue(visibility(self.master)["can_see_factory"])
        with self.assertRaisesMessage(WorkflowPermissionDenied, "not live yet"):
            factory_overview(actor=self.runner, business_unit=self.unit)
        factory_overview(actor=self.master, business_unit=self.unit)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Only the Master Admin"):
            update_settings(actor=self.manager, business_unit=self.unit, data={"is_live": True})
        self.go_live()
        self.assertEqual(visibility(self.runner), {"can_see_factory": True, "factory_live": True})
        UserActionGrant.objects.filter(user=self.runner, action=A.RUN_FACTORY).delete()
        self.assertFalse(visibility(self.runner)["can_see_factory"])
        client = APIClient()
        client.force_login(self.runner)
        self.assertEqual(client.get(f"/api/factory/?unit={self.unit.pk}").status_code, 403)

    def test_rates_overtime_and_overhead_are_for_the_master_admin_only(self):
        self.go_live()
        row = update_settings(actor=self.master, business_unit=self.unit,
                              data={"rates": {"joinery": "40"}, "overhead_percent": "20", "overtime_percent": "150"})
        self.assertEqual((row["rates"]["joinery"], row["overhead_percent"], row["overtime_percent"]),
                         ("40.00", "20.00", "150.00"))
        client = APIClient()
        client.force_login(self.manager)
        response = client.put(f"/api/factory/settings/?unit={self.unit.pk}", {"rates": {"joinery": "1"}}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_a_job_needs_a_paid_sales_order_and_jd_boq_items(self):
        self.go_live()
        self.make_receipt(key="unpaid-stock")
        unpaid = SalesQuote.objects.get(pk=self.make_quote(key="unpaid")["quote_id"])
        with self.assertRaisesMessage(InvalidTransaction, "client has paid"):
            self.job(quote_id=unpaid.pk)
        with self.assertRaisesMessage(InvalidTransaction, "JD-00"):
            self.job(boq=[{"code": "D-1", "description": "door", "quantity": "1"}])
        with self.assertRaisesMessage(InvalidTransaction, "List the BOQ items"):
            self.job(boq=[])
        row = self.job()
        self.assertEqual([line["code"] for line in row["boq"]], ["JD-00", "JD-01"])

    def test_a_fitout_job_to_the_clients_site_and_the_ledger(self):
        self.go_live()
        row = self.job()
        self.assertEqual((row["number"], row["status"]), ("JOB-0001", S.DRAFT))
        row = order_action(actor=self.runner, business_unit=self.unit, order_id=row["id"], action="submit")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Approve factory jobs"):
            order_action(actor=self.runner, business_unit=self.unit, order_id=row["id"], action="approve")
        # Estimate: 6 x 50 materials + 10 h x 35 joinery = 650, plus 15% overhead 97.50 = 747.50; up to 822.25.
        self.assertEqual((row["estimate"]["total"], row["estimate"]["limit"]), ("747.50", "822.25"))
        row = order_action(actor=self.manager, business_unit=self.unit, order_id=row["id"], action="approve")
        self.assertEqual(row["status"], S.IN_PROGRESS)

        row = self.take(row, "6")
        self.assertEqual((row["issues"][0]["status"], row["material_requests"][0]["status"], row["actual"]["materials"]),
                         ("waiting", "manager", "0.00"))
        row = self.deliver(row)
        self.assertEqual((row["issues"][0]["status"], row["actual"]["materials"]), ("issued", "300.00"))
        store = StockBalance.objects.get(business_unit=self.unit, variant=self.material, location=self.store)
        self.assertEqual((store.on_hand_quantity, store.on_hand_value), (Decimal("14.00"), Decimal("700.00")))
        floor = StockBalance.objects.get(business_unit=self.unit, variant=self.material, location=self.factory)
        self.assertEqual((floor.on_hand_quantity, floor.on_hand_value), (Decimal("0.00"), Decimal("0.00")))
        self.assertTrue(StockMovement.objects.filter(balance=floor, kind=StockMovement.Kind.PRODUCTION_USE).exists())

        row = self.hours(row, "8")
        row = self.hours(row, "2", overtime=True)   # overtime at 125%: 2 x 43.75
        self.assertEqual(row["actual"]["labour"], "367.50")

        with self.assertRaisesMessage(InvalidTransaction, "stored in the warehouse or sent"):
            order_action(actor=self.runner, business_unit=self.unit, order_id=row["id"], action="finish")
        row = order_action(actor=self.runner, business_unit=self.unit, order_id=row["id"], action="finish",
                           destination="site")
        # 300 + 367.50 = 667.50, plus 100.13 overhead = 767.63, within the 822.25 allowed.
        self.assertEqual((row["status"], row["actual"]["total"], row["finish_location"]["code"]),
                         (S.ON_THE_WAY, "767.63", "VSITE-VILLA-FIT-OUT"))
        site = Location.objects.get(code="VSITE-VILLA-FIT-OUT")
        self.assertEqual((site.name, site.kind), ("Vsite=TEST ONLY Villa fit-out", "virtual"))
        row = order_action(actor=self.runner, business_unit=self.unit, order_id=row["id"], action="received",
                           date=TODAY().isoformat())
        self.assertEqual(row["status"], S.DELIVERED)
        lines = JournalLine.objects.filter(entry__kind="factory", entry__business_unit=self.unit)
        for code in ("TEST_WIP", "TEST_FINISHED_JOBS"):
            self.assertEqual(sum(line.debit - line.credit for line in lines.filter(account__code=code)), Decimal("0.00"))
        self.assertEqual(sum(line.debit for line in lines.filter(account__code="TEST_COGS")), Decimal("767.63"))
        self.assertEqual(sum(line.debit for line in lines), sum(line.credit for line in lines))

    def test_showroom_furniture_goes_into_stock_at_its_cost(self):
        self.go_live()
        row = self.approved_job(purpose="showroom", quote_id=None, variant_id=self.product.pk, quantity="2", boq=[])
        self.deliver(self.take(row, "6"))
        row = order_action(actor=self.runner, business_unit=self.unit, order_id=row["id"], action="finish",
                           location_id=self.warehouse.pk)
        self.assertEqual((row["status"], row["actual"]["total"]), (S.IN_STOCK, "345.00"))
        made = StockBalance.objects.get(business_unit=self.unit, variant=self.product, location=self.warehouse)
        self.assertEqual((made.on_hand_quantity, made.on_hand_value), (Decimal("2.00"), Decimal("345.00")))

    def test_nobody_approves_their_own_job_or_their_own_extra_materials(self):
        self.go_live()
        UserActionGrant.objects.get_or_create(user=self.runner, business_unit=self.unit, action=A.APPROVE_FACTORY)
        row = self.job()
        order_action(actor=self.runner, business_unit=self.unit, order_id=row["id"], action="submit")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "another Factory Manager"):
            order_action(actor=self.runner, business_unit=self.unit, order_id=row["id"], action="approve")
        order_action(actor=self.manager, business_unit=self.unit, order_id=row["id"], action="approve")
        with self.assertRaisesMessage(InvalidTransaction, "Say why"):
            self.take(row, "8")
        row = self.take(row, "8", note="TEST ONLY a panel split")
        issue, slip = row["issues"][0], row["material_requests"][0]
        self.assertEqual((issue["status"], issue["beyond_plan"], slip["number"]), ("waiting", True, "JOB-0001-M01"))
        store = StockBalance.objects.get(business_unit=self.unit, variant=self.material, location=self.store)
        self.assertEqual(store.on_hand_quantity, Decimal("20.00"))
        with self.assertRaisesMessage(WorkflowPermissionDenied, "another Factory Manager"):
            decide_material_request(actor=self.runner, business_unit=self.unit, request_id=slip["id"], action="approve")
        with self.assertRaisesMessage(InvalidTransaction, "waiting for the Factory Manager"):
            order_action(actor=self.runner, business_unit=self.unit, order_id=row["id"], action="finish",
                         destination="site")
        row = decide_material_request(actor=self.manager, business_unit=self.unit, request_id=slip["id"], action="approve")
        self.assertEqual((row["issues"][0]["status"], row["issues"][0]["status_label"]),
                         ("warehouse", "Waiting for the warehouse admin to approve"))
        request = StockTransferRequest.objects.get(pk=row["issues"][0]["request_id"])
        self.assertEqual((request.status, request.requested_by), ("submitted", self.runner))
        # The person who asked cannot approve it as the warehouse admin either.
        UserActionGrant.objects.get_or_create(user=self.runner, business_unit=self.unit, action=A.APPROVE_TRANSFER)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "different person"):
            decide_transfer_request(actor=self.runner, transfer_request=request, idempotency_key="own", approve=True)
        row = self.deliver(row)
        self.assertEqual((row["issues"][0]["status"], row["material_requests"][0]["status"]), ("issued", "delivered"))
        store.refresh_from_db()
        self.assertEqual(store.on_hand_quantity, Decimal("12.00"))

    def test_a_material_request_slip_is_approved_twice_then_printed(self):
        self.go_live()
        row = self.approved_job()
        row = self.take(row, "4")
        slip = row["material_requests"][0]
        self.assertEqual((slip["status_label"], slip["can_print"]), ("Waiting for the Factory Manager", False))
        with self.assertRaisesMessage(InvalidTransaction, "both approved"):
            material_request_slip(actor=self.runner, business_unit=self.unit, request_id=slip["id"], printing=True)
        row = decide_material_request(actor=self.manager, business_unit=self.unit, request_id=slip["id"], action="approve")
        self.assertEqual(row["material_requests"][0]["status"], "warehouse")
        row = self.approve_slip(row)
        printed = material_request_slip(actor=self.runner, business_unit=self.unit, request_id=slip["id"], printing=True)
        # Who asked, the Factory Manager and the warehouse admin: three different people, each with the time.
        self.assertEqual(len({step["name"] for step in printed["approvals"]}), 3)
        self.assertTrue(all(step["name"] and step["at"] for step in printed["approvals"]))
        self.assertEqual((printed["lines"][0]["quantity"], printed["printed_count"], printed["deliver_to"][:7]),
                         ("4.00", 1, "W41-FAC"))

        # The warehouse admin can say no; the slip and its materials are then rejected with the reason.
        row = self.take(row, "2")
        slip = row["material_requests"][0]
        row = decide_material_request(actor=self.manager, business_unit=self.unit, request_id=slip["id"], action="approve")
        request = StockTransferRequest.objects.get(pk=row["material_requests"][0]["transfer_id"])
        decide_transfer_request(actor=self.storekeeper, transfer_request=request, idempotency_key="no", approve=False,
                                reason="TEST ONLY count first")
        row = self.row(row)
        self.assertEqual((row["material_requests"][0]["status"], row["material_requests"][0]["rejection_reason"],
                          row["issues"][-1]["status"]), ("rejected", "Warehouse: TEST ONLY count first", "rejected"))
        # The Factory Manager can say no too.
        row = self.take(row, "1")
        row = decide_material_request(actor=self.manager, business_unit=self.unit,
                                      request_id=row["material_requests"][0]["id"], action="reject", note="TEST ONLY no")
        self.assertEqual(row["material_requests"][0]["rejection_reason"], "Factory Manager: TEST ONLY no")

    def test_the_warehouse_hands_out_materials_like_any_move_always_to_the_factory(self):
        self.go_live()
        row = self.approved_job()
        with self.assertRaisesMessage(InvalidTransaction, "factory location the warehouse delivers to"):
            self.take(row, "2", deliver_to_id=self.warehouse.pk)
        row = self.approve_slip(self.take(row, "4"))
        issue = row["issues"][0]
        request = StockTransferRequest.objects.get(pk=issue["request_id"])
        # A normal transfer request on the warehouse's list, from the raw materials store to the factory.
        self.assertEqual((request.status, request.source_location.code, request.destination_location.code),
                         ("approved", "W41-04.2", "W41-FAC"))
        self.assertEqual((issue["status_label"], issue["can_receive"]), ("Waiting for the warehouse to prepare it", False))
        with self.assertRaisesMessage(InvalidTransaction, "still with the warehouse"):
            order_action(actor=self.runner, business_unit=self.unit, order_id=row["id"], action="finish",
                         destination="site")
        with self.assertRaisesMessage(InvalidTransaction, "cannot be cancelled"):
            order_action(actor=self.runner, business_unit=self.unit, order_id=row["id"], action="cancel", note="x")
        with self.assertRaisesMessage(InvalidTransaction, "has not sent"):
            receive_materials(actor=self.runner, business_unit=self.unit, request_id=request.pk)
        self.warehouse_sends(row)
        row = self.row(row)
        self.assertTrue(row["issues"][0]["can_receive"])
        # The warehouse may confirm arrival from its own screen too; the materials are then used on the job.
        receive_transfer_request(actor=self.storekeeper, transfer_request=request, idempotency_key="arrived")
        row = self.row(row)
        self.assertEqual((row["issues"][0]["status"], row["actual"]["materials"]), ("issued", "200.00"))

        # Leftovers already at the factory are used straight away, without the warehouse.
        self.make_receipt(key="factory-leftover", variant=self.material, location=self.factory, quantity="1.00",
                          unit_cost="40.00", charges=[])
        row = self.take(row, "1", location_id=self.factory.pk, deliver_to_id=None)
        self.assertEqual((row["issues"][-1]["status"], row["actual"]["materials"]), ("issued", "240.00"))

    def test_a_boq_is_read_from_the_excel_sheet(self):
        from io import BytesIO

        from openpyxl import Workbook

        self.go_live()
        book = Workbook()
        sheet = book.active
        sheet.title = "Joinery"
        sheet.append(["TEST ONLY Villa 12 fit-out, joinery BOQ"])
        sheet.append([])
        sheet.append(["Item No.", "Description", "Size (mm)", "Finish", "Qty", "Unit", "Rate"])
        sheet.append(["", "GROUND FLOOR"])
        for number in range(300):
            sheet.append([f"JD-{number:02d}", f"TEST ONLY door type {number}", "900 x 2400", "Oak veneer", 2, "nos", 1500])
        sheet.append(["JD 300", "TEST ONLY wall cladding", "", "Walnut", "12.5", "m2", ""])
        sheet.append(["JD-301", "TEST ONLY shelf", "", "", "two", "nos", ""])
        sheet.append(["", "Total", "", "", "", "", 999])
        extra = book.create_sheet("Loose")
        extra.append(["Code", "Item description", "Qty"])
        extra.append(["JD-01", "TEST ONLY duplicate", 1])
        extra.append(["JD-302A", "TEST ONLY headboard", ""])
        buffer = BytesIO()
        book.save(buffer)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Run factory jobs"):
            read_boq_sheet(actor=self.manager, business_unit=self.unit, file_name="boq.xlsx", content=buffer.getvalue())
        found = read_boq_sheet(actor=self.runner, business_unit=self.unit, file_name="boq.xlsx", content=buffer.getvalue())
        self.assertEqual(found["sheets"], [{"name": "Joinery", "items": 301}, {"name": "Loose", "items": 1}])
        self.assertEqual(found["lines"][0], {"code": "JD-00", "description": "TEST ONLY door type 0", "quantity": "2.00",
                                             "unit": "nos", "details": {"Size (mm)": "900 x 2400", "Finish": "Oak veneer",
                                                                        "Rate": "1500"},
                                             "area": "", "floor": "", "zone": "", "tags": [], "materials": []})
        self.assertEqual((found["lines"][300]["code"], found["lines"][300]["quantity"]), ("JD-300", "12.50"))
        self.assertEqual((found["lines"][-1]["code"], found["lines"][-1]["quantity"]), ("JD-302A", "1.00"))
        self.assertEqual(len(found["problems"]), 2, found["problems"])
        self.assertTrue(any("JD-301" in text and "not a number" in text for text in found["problems"]), found["problems"])
        row = self.job(boq=found["lines"])
        self.assertEqual((len(row["boq"]), row["boq"][2]["code"], row["boq"][0]["details"]["Finish"]),
                         (302, "JD-02", "Oak veneer"))

    def test_a_special_purchase_is_approved_then_bought_by_card_onto_the_job(self):
        self.go_live()
        row = self.approved_job()
        row = request_purchase(actor=self.runner, business_unit=self.unit, order_id=row["id"], data={
            "description": "TEST ONLY brass handles", "quantity": "4", "estimated_cost": "120",
            "supplier_name": "TEST ONLY hardware shop", "reason": "Special finish chosen by the client"})
        purchase = row["purchases"][0]
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Buy special items"):
            purchase_action(actor=self.manager, business_unit=self.unit, purchase_id=purchase["id"],
                            data={"action": "buy"})
        purchase_action(actor=self.manager, business_unit=self.unit, purchase_id=purchase["id"], data={"action": "approve"})
        with self.assertRaisesMessage(InvalidTransaction, "special purchase is still open"):
            order_action(actor=self.runner, business_unit=self.unit, order_id=row["id"], action="finish",
                         destination="site")
        row = purchase_action(actor=self.finance, business_unit=self.unit, purchase_id=purchase["id"], data={
            "action": "buy", "account_id": self.cash.pk, "date": TODAY().isoformat(), "actual_cost": "130",
            "receipt_reference": "R-881"})
        self.assertEqual((row["purchases"][0]["status"], row["actual"]["purchases"]), ("bought", "130.00"))

    def test_a_job_over_its_estimate_waits_for_finance(self):
        self.go_live()
        row = self.approved_job()
        self.hours(row, "16")
        self.deliver(self.take(row, "6"))
        # 300 + 560 = 860, plus 129 overhead = 989 > 822.25 allowed.
        row = order_action(actor=self.runner, business_unit=self.unit, order_id=row["id"], action="finish",
                           destination="warehouse", location_id=self.warehouse.pk)
        self.assertEqual(row["status"], S.COST_CHECK)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Approve factory jobs that cost more"):
            order_action(actor=self.manager, business_unit=self.unit, order_id=row["id"], action="approve_cost")
        row = order_action(actor=self.finance, business_unit=self.unit, order_id=row["id"], action="approve_cost")
        self.assertEqual((row["status"], row["actual"]["total"], row["finish_location"]["code"]),
                         (S.STORED, "989.00", "W41-01"))
        row = order_action(actor=self.runner, business_unit=self.unit, order_id=row["id"], action="send")
        self.assertEqual(row["status"], S.ON_THE_WAY)

    def test_raw_materials_list_shows_stock_price_and_supplier(self):
        self.go_live()
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Approve factory jobs"):
            save_raw_material(actor=self.runner, business_unit=self.unit,
                              data={"variant_id": self.material.pk, "category": "boards"})
        rows = save_raw_material(actor=self.manager, business_unit=self.unit,
                                 data={"variant_id": self.material.pk, "category": "boards", "size": "2440 x 1220 x 18 mm"})
        row = rows["raw_materials"][0]
        self.assertEqual((row["code"], row["size"], row["unit_price"], row["on_hand"]),
                         (self.material.sku, "2440 x 1220 x 18 mm", "50.00", "20.00"))
        self.assertEqual(row["places"][0]["location"], "W41-04.2")

    def test_cancel_only_before_anything_is_booked(self):
        self.go_live()
        row = self.approved_job()
        self.hours(row, "2")
        with self.assertRaisesMessage(InvalidTransaction, "cannot be cancelled"):
            order_action(actor=self.runner, business_unit=self.unit, order_id=row["id"], action="cancel", note="x")
        other = self.job(quote_id=self.sales_order("second-order").pk)
        row = order_action(actor=self.runner, business_unit=self.unit, order_id=other["id"], action="cancel",
                           note="TEST ONLY client changed the design")
        self.assertEqual(row["status"], S.CANCELLED)

    def test_the_api_runs_a_job(self):
        self.go_live()
        client = APIClient()
        client.force_login(self.runner)
        response = client.post(f"/api/factory/jobs/?unit={self.unit.pk}", {
            "purpose": "studio", "quote_id": self.sales_order().pk,
            "due_date": (TODAY() + datetime.timedelta(days=3)).isoformat(),
            "boq": [{"code": "JD-00", "description": "TEST ONLY curtain box", "quantity": "3"}],
            "materials": [{"variant_id": self.material.pk, "quantity": "1"}]}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        job = response.data["id"]
        self.assertEqual(client.post(f"/api/factory/jobs/{job}/?unit={self.unit.pk}", {"action": "submit"},
                                     format="json").status_code, 200)
        overview = client.get(f"/api/factory/?unit={self.unit.pk}")
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.data["tiles"]["waiting"], 1)
        self.assertEqual(FactorySettings.objects.get().next_number, 2)
        self.assertTrue(client.get("/api/auth/me/").data["can_see_factory"])

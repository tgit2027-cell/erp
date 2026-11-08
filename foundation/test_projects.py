"""Projects & Fit-Out (Phase 2): the signed BOQ, budget control, schedule, money, the factory, tenders and bonds."""

import datetime
import io
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    CashAccount,
    DemoUserMarker,
    JournalLine,
    ProductionOrder,
    Project,
    ProjectBoqLine,
    SyntheticCounterparty,
    UserActionGrant,
)
from .projects import (
    claim_action,
    create_order,
    create_project,
    order_action,
    prepare_claim,
    project_action,
    project_detail,
    projects_overview,
    read_boq,
    record_receipt,
    send_to_factory,
    update_schedule,
    update_settings,
    visibility,
)
from .purchasing_services import WorkflowPermissionDenied
from .tenders import (
    add_bond,
    bonds_overview,
    create_tender,
    expiring_bonds,
    tenders_overview,
    update_bond,
    update_tender,
)
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action
TODAY = timezone.localdate


def signed_boq():
    """A small synthetic BOQ laid out like Tivoli's fit-out template (headings over rows 13 and 14)."""
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "BOQ"
    sheet["A2"] = "TEST ONLY fit-out BOQ"
    for column, text in {"H": "Item Number", "J": "Description", "M": "Quantity", "N": "Unit", "O": "Unit Price",
                         "Q": "Total Amount"}.items():
        sheet[f"{column}13"] = text
    for column, text in {"A": "CATEGORIES", "B": "SUB-CATEGORY", "C": "ITEM", "D": "AREA", "E": "FLOOR", "T": "EUR",
                         "U": "QAR", "V": "Installation", "AA": "RFQ Date", "AB": "Supplier/Contractor"}.items():
        sheet[f"{column}14"] = text
    sheet["H15"], sheet["I15"] = 1, "TEST ONLY Ground floor"
    items = [
        # category, sub-category, item, area, floor, code, description, qty, unit, price, supply, install, cost, supplier
        ("Local", "Marble", "Floor", "Lobby", "Ground", "F1", "TEST ONLY marble floor", 100, "m²", 500, 350, 50, 400,
         "TEST Marble Co"),
        ("Joinery", "Doors", "Door", "Lobby", "Ground", "JD1", "TEST ONLY oak door", 10, "No", 4000, 2500, 0, 2500,
         "Factory"),
        ("Local", "Gypsum", "Ceiling", "Lobby", "Ground", "C1", "TEST ONLY gypsum ceiling", 1, "Lot", 40000, 25000,
         5000, 30000, "TEST Ceilings"),
    ]
    for row, item in enumerate(items, start=16):
        (category, sub, kind, area, floor, code, text, qty, unit, price, supply, install, cost, supplier) = item
        for column, value in {"A": category, "B": sub, "C": kind, "D": area, "E": floor, "H": code, "I": text,
                              "M": qty, "N": unit, "O": price, "Q": qty * price, "U": supply, "V": install, "W": cost,
                              "X": price, "AA": datetime.datetime(2026, 6, 25), "AB": supplier}.items():
            sheet[f"{column}{row}"] = value
    sheet["J20"], sheet["Q20"] = "Sub Total", 130000
    sheet["J21"], sheet["Q21"], sheet["S21"] = "Total Discount", 6500, 0.05
    sheet["J22"], sheet["Q22"] = "Total Amount", 123500
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


class ProjectFixtureMixin(PurchasingFixtureMixin):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        cls.master = get_user_model().objects.create_user(
            username="test_projects_master", password="unused-test-password", is_staff=True, is_superuser=True)
        DemoUserMarker.objects.create(user=cls.master)
        # The Project Manager, the Construction Manager and Finance.
        cls.runner, cls.manager, cls.finance = cls.sales, cls.admin, cls.roche_user
        UserActionGrant.objects.get_or_create(user=cls.runner, business_unit=cls.unit, action=A.RUN_PROJECTS)
        UserActionGrant.objects.get_or_create(user=cls.manager, business_unit=cls.unit, action=A.APPROVE_PROJECTS)
        UserActionGrant.objects.get_or_create(user=cls.finance, business_unit=cls.unit, action=A.INVOICE_PROJECTS)
        UserActionGrant.objects.get_or_create(user=cls.runner, business_unit=cls.unit, action=A.RUN_FACTORY)
        cls.bank = CashAccount.objects.create(business_unit=cls.unit, name="TEST ONLY project bank", kind="bank",
                                              account_type="other", opening_date=datetime.date(2024, 1, 1),
                                              created_by=cls.admin)
        cls.client_party = SyntheticCounterparty.objects.create(
            party_type=SyntheticCounterparty.Type.CUSTOMER, code="TEST-CUST-PRJ-01",
            name="TEST ONLY Villa owner", qid="29963400077")

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)

    def go_live(self):
        update_settings(actor=self.master, business_unit=self.unit, data={"is_live": True})

    def preview(self):
        return read_boq(actor=self.runner, business_unit=self.unit, file_name="TEST-signed.xlsx", content=signed_boq())

    def open_project(self, **extra):
        preview = self.preview()
        data = {"name": "TEST ONLY Villa 12 fit-out", "customer_id": self.client_party.pk, "site": "West Bay",
                "contract_reference": "TEST-CTR-1", "signed_on": TODAY().isoformat(), "boq": preview["lines"],
                "discount_percent": preview["totals"]["discount_percent"], "boq_file_name": "TEST-signed.xlsx"} | extra
        return create_project(actor=self.runner, business_unit=self.unit, data=data)

    def running(self):
        row = self.open_project()
        return project_action(actor=self.manager, business_unit=self.unit, project_id=row["id"],
                              data={"action": "approve"})

    def line(self, row, code):
        return next(line for line in row["boq"] if line["code"] == code)

    def order(self, row, code, amount, **extra):
        return create_order(actor=self.runner, business_unit=self.unit, project_id=row["id"],
                            data={"kind": "supply", "line_ids": [self.line(row, code)["id"]], "amount": amount,
                                  "supplier": "TEST Marble Co", "description": f"TEST ONLY {code}"} | extra)

    def order_act(self, order_id, actor, **data):
        return order_action(actor=actor, business_unit=self.unit, order_id=order_id, data=data)

    def money(self, **extra):
        return {"date": TODAY().isoformat(), "reference": "TEST-REF", "account_id": self.bank.pk} | extra


class ProjectTests(ProjectFixtureMixin, TestCase):
    def test_hidden_until_live_except_for_the_master_admin(self):
        self.assertEqual(visibility(self.runner), {"can_see_projects": False, "projects_live": False})
        self.assertTrue(visibility(self.master)["can_see_projects"])
        with self.assertRaises(WorkflowPermissionDenied):
            projects_overview(actor=self.runner, business_unit=self.unit)
        self.go_live()
        self.assertTrue(visibility(self.runner)["can_see_projects"])
        with self.assertRaises(WorkflowPermissionDenied):
            update_settings(actor=self.manager, business_unit=self.unit, data={"is_live": False})

    def test_the_signed_boq_gives_items_costs_and_the_discount_on_its_own(self):
        self.go_live()
        preview = self.preview()
        self.assertEqual(preview["totals"]["value"], "130000.00")
        self.assertEqual(preview["totals"]["discount_percent"], "5")
        self.assertEqual(preview["totals"]["discount"], "6500.00")
        self.assertEqual(preview["totals"]["contract_value"], "123500.00")
        self.assertEqual(preview["totals"]["cost"], "95000.00")
        self.assertEqual(preview["totals"]["factory_items"], 1)
        floor = preview["lines"][0]
        self.assertEqual((floor["code"], floor["area"], floor["price_group"], floor["category"], floor["supplier"],
                          floor["rfq_date"], floor["cost_rate"]),
                         ("F1", "Lobby", "Local", "Marble", "TEST Marble Co", "2026-06-25", "400.0000"))
        self.assertEqual(preview["problems"], [])
        row = self.open_project()
        self.client_party.refresh_from_db()
        self.assertRegex(self.client_party.project_number, r"^P-\d{6}$")
        self.assertEqual(row["status"], "setup")
        self.assertEqual(row["totals"]["boq_value"], "130000.00")      # selling prices as signed
        self.assertEqual(row["totals"]["discount"], "6500.00")         # then the discount
        self.assertEqual(row["totals"]["contract_value"], "123500.00")  # then the final price
        self.assertEqual(self.line(row, "F1")["value"], "50000.00")
        with self.assertRaises(InvalidTransaction):
            self.open_project(contract_reference="")

    def test_the_construction_manager_approves_the_budget_and_may_change_the_discount(self):
        self.go_live()
        row = self.open_project()
        with self.assertRaises(WorkflowPermissionDenied):
            project_action(actor=self.runner, business_unit=self.unit, project_id=row["id"], data={"action": "approve"})
        row = project_action(actor=self.manager, business_unit=self.unit, project_id=row["id"],
                             data={"action": "approve"})
        self.assertEqual((row["status"], row["totals"]["budget"]), ("running", "95000.00"))
        with self.assertRaises(WorkflowPermissionDenied):
            project_action(actor=self.runner, business_unit=self.unit, project_id=row["id"],
                           data={"action": "discount", "discount_percent": "10"})
        row = project_action(actor=self.manager, business_unit=self.unit, project_id=row["id"],
                             data={"action": "discount", "discount_percent": "10"})
        self.assertEqual((row["totals"]["boq_value"], row["totals"]["contract_value"]), ("130000.00", "117000.00"))

    def test_over_budget_goes_to_the_construction_manager_then_finance_who_may_ask_for_details(self):
        self.go_live()
        row = self.running()
        row = self.order(row, "F1", "38000")                 # F1's budget is 40,000
        first = row["orders"][0]
        self.assertEqual(first["status"], "approved")
        self.assertTrue(self.line(row, "F1")["steps"][0]["done_on"])
        with self.assertRaises(InvalidTransaction):
            self.order(row, "F1", "5000")                    # 3,000 over budget needs a reason
        row = self.order(row, "F1", "5000", over_reason="TEST ONLY extra marble for waste")
        second = row["orders"][-1]
        self.assertEqual((second["status"], second["over_budget"]), ("manager", "3000.00"))
        with self.assertRaises(WorkflowPermissionDenied):
            self.order_act(second["id"], self.runner, action="approve")
        self.order_act(second["id"], self.manager, action="approve")
        with self.assertRaises(WorkflowPermissionDenied):
            self.order_act(second["id"], self.manager, action="approve")   # not Finance
        row = self.order_act(second["id"], self.finance, action="ask", note="Send the supplier's quotation")
        self.assertEqual(row["orders"][-1]["status"], "details")
        with self.assertRaises(InvalidTransaction):
            self.order_act(second["id"], self.finance, action="pay", amount="5000", **self.money())
        self.order_act(second["id"], self.runner, action="answer", note="TEST ONLY quotation Q-1 attached")
        row = self.order_act(second["id"], self.finance, action="approve")
        self.assertEqual(row["orders"][-1]["status"], "approved")
        self.assertEqual(row["totals"]["over_budget_approved"], "3000.00")
        # Payments never exceed the approved order.
        with self.assertRaises(InvalidTransaction):
            self.order_act(first["id"], self.finance, action="pay", amount="38000.01", **self.money())
        row = self.order_act(first["id"], self.finance, action="pay", amount="38000", **self.money())
        self.assertEqual(row["totals"]["paid_out"], "38000.00")

    def test_joinery_items_go_to_the_factory_and_the_schedule_tracks_each_item(self):
        self.go_live()
        row = self.running()
        due = (TODAY() + datetime.timedelta(days=30)).isoformat()
        with self.assertRaises(InvalidTransaction):
            send_to_factory(actor=self.runner, business_unit=self.unit, project_id=row["id"],
                            data={"line_ids": [self.line(row, "F1")["id"]], "due_date": due})
        row = send_to_factory(actor=self.runner, business_unit=self.unit, project_id=row["id"],
                              data={"line_ids": [self.line(row, "JD1")["id"]], "due_date": due})
        job = ProductionOrder.objects.get(project_id=row["id"])
        self.assertEqual(job.boq_lines.count(), 1)
        self.assertEqual(ProjectBoqLine.objects.get(code="JD1").factory_job, job)
        ceiling = self.line(row, "C1")["id"]
        row = update_schedule(actor=self.runner, business_unit=self.unit, project_id=row["id"], data={"steps": [
            {"line_id": ceiling, "step": "delivered", "planned_on": due},
            {"line_id": ceiling, "step": "installed", "done_on": TODAY().isoformat()}]})
        line = self.line(row, "C1")
        self.assertEqual(line["done_percent"], "100.00")
        self.assertEqual({step["step"]: step["planned_on"] for step in line["steps"]}["delivered"], due)

    def test_cash_in_and_out_claims_retention_and_the_ledger(self):
        self.go_live()
        row = self.running()
        pid = row["id"]
        with self.assertRaises(WorkflowPermissionDenied):
            record_receipt(actor=self.runner, business_unit=self.unit, project_id=pid,
                           data=self.money(kind="advance", amount="1000"))
        self.assertEqual(Decimal(row["totals"]["advance_expected"]), Decimal("24700.00"))   # 20% of 123,500
        with self.assertRaises(InvalidTransaction):
            record_receipt(actor=self.finance, business_unit=self.unit, project_id=pid,
                           data=self.money(kind="advance", amount="24700.01"))
        record_receipt(actor=self.finance, business_unit=self.unit, project_id=pid,
                       data=self.money(kind="advance", amount="24700"))
        update_schedule(actor=self.runner, business_unit=self.unit, project_id=pid,
                        data={"progress": {str(self.line(row, "F1")["id"]): "50"}})
        row = prepare_claim(actor=self.runner, business_unit=self.unit, project_id=pid,
                            data={"period_end": TODAY().isoformat()})
        claim = row["claims"][0]
        # 50% of 50,000 less the 5% discount = 23,750; 5% retention; 20% of the down payment taken back.
        self.assertEqual((claim["gross"], claim["retention"], claim["advance_recovery"], claim["net"]),
                         ("23750.00", "1187.50", "4750.00", "17812.50"))
        act = lambda actor, **data: claim_action(actor=actor, business_unit=self.unit, claim_id=claim["id"], data=data)
        act(self.manager, action="approve")
        with self.assertRaises(InvalidTransaction):
            act(self.finance, action="invoice", date=TODAY().isoformat())       # not certified yet
        act(self.runner, action="submit", date=TODAY().isoformat(), consultant_reference="TEST-PSR-1")
        with self.assertRaises(InvalidTransaction):
            act(self.runner, action="certify", date=TODAY().isoformat(), certified="30000")
        # The consultant certifies less than asked: retention and the down payment share follow.
        row = act(self.runner, action="certify", date=TODAY().isoformat(), certified="20000",
                  consultant_reference="TEST-IPC-1")
        claim = row["claims"][0]
        self.assertEqual((claim["status"], claim["requested_gross"], claim["gross"], claim["retention"],
                          claim["advance_recovery"], claim["net"]),
                         ("certified", "23750.00", "20000.00", "1000.00", "4000.00", "15000.00"))
        row = act(self.finance, action="invoice", date=TODAY().isoformat())
        self.assertTrue(row["claims"][0]["invoice_number"].startswith("PINV-"))
        row = record_receipt(actor=self.finance, business_unit=self.unit, project_id=pid,
                             data=self.money(kind="claim", claim_id=claim["id"], amount="15000"))
        self.assertEqual(row["totals"]["retention_held"], "1000.00")
        # The ceiling subcontractor: 30% on order, 70% on delivery in 20 days.
        delivery = TODAY() + datetime.timedelta(days=20)
        row = self.order(row, "C1", "30000", supplier="TEST Ceilings", delivery="production",
                         expected_delivery=delivery.isoformat(),
                         terms=[{"percent": "30", "event": "order"}, {"percent": "70", "event": "delivered"}])
        order = row["orders"][0]
        self.assertEqual([(row["amount"], row["due_on"]) for row in order["instalments"]],
                         [("9000.00", TODAY().isoformat()), ("21000.00", delivery.isoformat())])
        self.assertEqual(self.line(row, "C1")["steps"][3]["planned_on"], delivery.isoformat())
        with self.assertRaises(InvalidTransaction):
            self.order(row, "C1", "1", over_reason="x", terms=[{"percent": "50", "event": "order"}])
        row = self.order_act(order["id"], self.finance, action="pay", amount="10000", **self.money())
        self.assertEqual([row["paid"] for row in row["orders"][0]["instalments"]], ["9000.00", "1000.00"])
        weeks = row["cash"]["weeks"]
        self.assertEqual(sum(Decimal(week["in_actual"]) for week in weeks), Decimal("39700.00"))
        self.assertEqual(sum(Decimal(week["out_actual"]) for week in weeks), Decimal("10000.00"))
        self.assertIn(Decimal("20000.00"), [Decimal(week["out_planned"]) for week in weeks])
        sums = JournalLine.objects.filter(entry__kind="project").aggregate(debit=Sum("debit"), credit=Sum("credit"))
        self.assertEqual(sums["debit"], sums["credit"])
        # The retention is paid after the 400-day warranty unless the project says at handover.
        project_action(actor=self.runner, business_unit=self.unit, project_id=pid,
                       data={"action": "handover", "date": TODAY().isoformat()})
        with self.assertRaises(InvalidTransaction):
            prepare_claim(actor=self.runner, business_unit=self.unit, project_id=pid,
                          data={"kind": "retention", "period_end": TODAY().isoformat()})
        Project.objects.filter(pk=pid).update(retention_release="handover")
        row = prepare_claim(actor=self.runner, business_unit=self.unit, project_id=pid,
                            data={"kind": "retention", "period_end": TODAY().isoformat()})
        self.assertEqual(row["claims"][-1]["net"], "1000.00")
        self.assertEqual(projects_overview(actor=self.manager, business_unit=self.unit)["tiles"]["running"], 1)

    def test_the_api_opens_and_approves_a_project(self):
        self.go_live()
        client = APIClient()
        client.force_login(self.runner)
        upload = SimpleUploadedFile("TEST-signed.xlsx", signed_boq())
        preview = client.post(f"/api/projects/boq-upload/?unit={self.unit.pk}", {"file": upload}, format="multipart")
        self.assertEqual(preview.status_code, 200, preview.data)
        response = client.post(f"/api/projects/?unit={self.unit.pk}", {
            "name": "TEST ONLY API project", "customer_id": self.client_party.pk, "contract_reference": "TEST-CTR-9",
            "signed_on": TODAY().isoformat(), "boq": preview.data["lines"], "discount_percent": "5"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        client.force_login(self.manager)
        approved = client.post(f"/api/projects/{response.data['id']}/?unit={self.unit.pk}", {"action": "approve"},
                               format="json")
        self.assertEqual(approved.data["status"], "running")
        self.assertTrue(client.get("/api/auth/me/").data["can_see_projects"])
        self.assertEqual(client.get(f"/api/projects/tenders/?unit={self.unit.pk}").status_code, 200)
        self.assertEqual(client.get(f"/api/projects/bonds/?unit={self.unit.pk}").status_code, 200)


class TenderTests(ProjectFixtureMixin, TestCase):
    def tender(self, **extra):
        data = {"client_name": "TEST ONLY Client", "project_name": "TEST ONLY Majlis", "contact_person": "TEST Person",
                "email": "test@example.invalid", "phone": "+974 0000 0000", "assignee_id": self.runner.pk,
                "start_date": TODAY().isoformat(), "due_date": TODAY().isoformat(), "quote_reference": "TEST-Q-1",
                "revision": "R2", "brands": "TEST Brand", "exworks": "10000", "rate": "4.1", "total": "250000",
                "remarks": "TEST ONLY follow up"} | extra
        return create_tender(actor=self.runner, business_unit=self.unit, data=data)

    def test_tenders_are_listed_by_status_with_the_win_rate(self):
        self.go_live()
        board = self.tender()
        first = board["groups"][1]["rows"][0]
        self.assertEqual((first["number"], first["status"], first["rate"]), ("TND-0001", "submitted", "4.1"))
        self.tender(project_name="TEST ONLY Lost one", total="100000")
        with self.assertRaises(WorkflowPermissionDenied):
            create_tender(actor=self.finance, business_unit=self.unit, data={"client_name": "x"})
        update_tender(actor=self.runner, business_unit=self.unit, tender_id=first["id"],
                      data={"status": "won", "client_order": "TEST-LOA-1", "progress": "production"})
        lost = tenders_overview(actor=self.manager, business_unit=self.unit)["groups"][1]["rows"][0]
        board = update_tender(actor=self.runner, business_unit=self.unit, tender_id=lost["id"],
                              data={"status": "lost", "remarks": "TEST ONLY price"})
        self.assertEqual(board["tiles"]["win_rate"], 50)
        self.assertEqual(board["tiles"]["won_this_year"], "250000.00")
        self.assertEqual([len(group["rows"]) for group in board["groups"]], [0, 0, 1, 1, 0])
        with self.assertRaises(InvalidTransaction):
            update_tender(actor=self.runner, business_unit=self.unit, tender_id=lost["id"], data={"progress": "shipping"})
        # A won tender opens a project.
        project = self.open_project(tender_id=first["id"])
        self.assertEqual(project["tender"]["number"], "TND-0001")
        with self.assertRaises(InvalidTransaction):
            self.open_project(tender_id=lost["id"])

    def test_bonds_are_kept_with_their_validity_and_flagged_before_expiry(self):
        self.go_live()
        tender = self.tender()["groups"][1]["rows"][0]
        soon = (TODAY() + datetime.timedelta(days=10)).isoformat()
        later = (TODAY() + datetime.timedelta(days=200)).isoformat()
        with self.assertRaises(InvalidTransaction):
            add_bond(actor=self.finance, business_unit=self.unit,
                     data={"tender_id": tender["id"], "kind": "bid_bond", "reference": "TEST-BB-1", "amount": "5000"})
        add_bond(actor=self.finance, business_unit=self.unit,
                 data={"tender_id": tender["id"], "kind": "bid_bond", "reference": "TEST-BB-1", "amount": "5000",
                       "issuer": "TEST Bank", "expires_on": soon})
        board = add_bond(actor=self.runner, business_unit=self.unit,
                         data={"tender_id": tender["id"], "kind": "cheque", "reference": "TEST-CHQ-7",
                               "amount": "12500", "expires_on": later})
        self.assertEqual(board["tiles"], {"active": 2, "active_amount": "17500.00", "expiring": 1, "expired": 0,
                                          "claimed": 0})
        self.assertEqual(len(expiring_bonds(self.unit)), 1)
        bond = next(row for row in board["bonds"] if row["reference"] == "TEST-BB-1")
        self.assertEqual(bond["for"], f"{tender['number']} TEST ONLY Majlis")
        board = update_bond(actor=self.finance, business_unit=self.unit, bond_id=bond["id"],
                            data={"status": "released"})
        self.assertEqual(board["tiles"]["active"], 1)
        with self.assertRaises(InvalidTransaction):
            update_bond(actor=self.finance, business_unit=self.unit, bond_id=bond["id"], data={"status": "claimed"})
        with self.assertRaises(WorkflowPermissionDenied):
            add_bond(actor=self.manager, business_unit=self.unit,
                     data={"tender_id": tender["id"], "kind": "warranty", "reference": "TEST-W"})
        self.assertEqual(bonds_overview(actor=self.manager, business_unit=self.unit)["tiles"]["active"], 1)
        self.assertEqual(tenders_overview(actor=self.manager, business_unit=self.unit)["tiles"]["bonds_expiring"], 0)

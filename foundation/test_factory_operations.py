"""Factory operations: BOQ materials against stock, the factory team and workload, machines, and learning."""

import datetime
from io import BytesIO

from django.test import TestCase, override_settings
from django.utils import timezone
from openpyxl import Workbook
from rest_framework.test import APIClient

from .factory import factory_overview, read_boq_sheet, record_hours, save_raw_material
from .factory_learning import apply_suggestion, learned_overview, suggest_for_line
from .factory_machines import link_asset, machines_overview, record_service, save_machine, save_part
from .factory_planning import factory_dashboard, planning_overview, save_tag, update_stage
from .factory_team import save_worker, team_workload
from .models import Employee, FixedAsset, ProductionStage
from .purchasing_services import WorkflowPermissionDenied
from .test_factory import FactoryFixtureMixin
from .transaction_services import InvalidTransaction

TODAY = timezone.localdate


@override_settings(APP_ENV="demo")
class FactoryOperationsTests(FactoryFixtureMixin, TestCase):
    def step(self, row, code, kind, **data):
        stage = ProductionStage.objects.get(line__order_id=row["id"], line__code=code, kind=kind)
        return update_stage(actor=self.runner, business_unit=self.unit, stage_id=stage.pk, data=data)

    def workbook(self, materials):
        book = Workbook()
        sheet = book.active
        sheet.title = "BOQ"
        sheet.append(["Code", "Description", "Tags", "Qty"])
        sheet.append(["JD-01", "TEST ONLY flush door", "Door", 2])
        sheet.append(["JD-02", "TEST ONLY wall cladding", "", 12])
        found = book.create_sheet("Materials")
        found.append(["JD", "Material", "Qty", "Unit"])
        for row in materials:
            found.append(row)
        buffer = BytesIO()
        book.save(buffer)
        return buffer.getvalue()

    def test_boq_materials_are_read_per_jd_and_checked_against_stock(self):
        self.go_live()
        save_tag(actor=self.manager, business_unit=self.unit, data={"name": "Door"})
        save_raw_material(actor=self.manager, business_unit=self.unit,
                          data={"variant_id": self.material.pk, "category": "boards"})
        # 20 sheets are in the store. The BOQ needs 18 + 7 = 25.
        content = self.workbook([["JD-01", self.material.sku, 18, "sheet"], ["JD-02", self.material.sku, 7, "sheet"],
                                 ["JD-02", "TEST ONLY unobtainium", 1, "kg"], ["JD-09", self.material.sku, 1, "sheet"]])
        found = read_boq_sheet(actor=self.runner, business_unit=self.unit, file_name="boq.xlsx", content=content)
        self.assertEqual([line["materials"][0]["quantity"] for line in found["lines"]], ["18.00", "7.00"])
        self.assertEqual(found["materials"][0]["quantity"], "26.00")
        self.assertTrue(any("unobtainium" in text for text in found["problems"]))
        self.assertTrue(any("JD-09" in text for text in found["problems"]))
        check = found["stock_check"][0]
        self.assertEqual((check["free"], check["to_order"], check["short"]), ("20.00", "6.00", True))

        # Saved on a job, it shows as materials to order, on the job and the dashboard.
        row = self.job(boq=found["lines"], materials=[{"variant_id": self.material.pk, "quantity": "26"}])
        overview = factory_overview(actor=self.runner, business_unit=self.unit)
        self.assertEqual((overview["tiles"]["to_order"], overview["to_order"][0]["to_order"]), (1, "6.00"))
        job = next(order for order in overview["orders"] if order["id"] == row["id"])
        self.assertEqual(job["short_materials"][0]["short"], "6.00")
        self.assertEqual(job["boq"][0]["materials"][0]["sku"], self.material.sku)
        board = factory_dashboard(actor=self.runner, business_unit=self.unit)
        self.assertEqual(board["to_order"][0]["jobs"][0]["job"], row["number"])

    def test_the_team_comes_from_factory_staff_by_section_with_workload(self):
        self.go_live()
        office = Employee.objects.create(
            business_unit=self.unit, staff_number="TEST-O-001", full_name="TEST ONLY Accountant", job_title="Accountant",
            department="Finance", nationality="Testland", joined_on=datetime.date(2024, 1, 1), created_by=self.admin)
        painter = Employee.objects.create(
            business_unit=self.unit, staff_number="TEST-F-002", full_name="TEST ONLY Painter", job_title="Painter",
            department="Finance", nationality="Testland", joined_on=datetime.date(2024, 1, 1), created_by=self.admin)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Approve factory jobs"):
            save_worker(actor=self.runner, business_unit=self.unit, data={"employee_id": painter.pk, "section": "paint"})
        save_worker(actor=self.manager, business_unit=self.unit, data={"employee_id": painter.pk, "section": "paint"})
        save_worker(actor=self.manager, business_unit=self.unit, data={"employee_id": self.worker.pk, "section": "joinery"})
        people = {person["name"]: person for person in planning_overview(actor=self.runner, business_unit=self.unit)["people"]}
        self.assertEqual(set(people), {"TEST ONLY Carpenter", "TEST ONLY Painter"})
        self.assertEqual(people["TEST ONLY Painter"]["section_label"], "Paint")

        row = self.approved_job()
        with self.assertRaisesMessage(InvalidTransaction, "factory's staff"):
            self.step(row, "JD-00", "joinery", lead_id=office.pk)
        self.step(row, "JD-00", "joinery", action="start", lead_id=self.worker.pk)
        self.step(row, "JD-01", "joinery", action="start", lead_id=self.worker.pk)
        late = (TODAY() - datetime.timedelta(days=2)).isoformat()
        self.step(row, "JD-01", "paint", lead_id=self.worker.pk, planned_start=late, planned_end=late)
        line = next(line for line in factory_overview(actor=self.runner, business_unit=self.unit)["orders"][0]["boq"])
        jd = ProductionStage.objects.get(line__order_id=row["id"], line__code="JD-00", kind="joinery").line
        record_hours(actor=self.runner, business_unit=self.unit, order_id=row["id"],
                     data={"workshop": "joinery", "employee_id": self.worker.pk, "work_date": TODAY().isoformat(),
                           "hours": "6", "line_id": jd.pk})
        self.assertEqual(line["code"], "JD-00")
        team = {person["name"]: person for person in team_workload(actor=self.runner, business_unit=self.unit)["people"]}
        carpenter, painter_row = team["TEST ONLY Carpenter"], team["TEST ONLY Painter"]
        self.assertEqual((carpenter["load"], carpenter["active"], carpenter["late"], carpenter["hours_week"]),
                         ("busy", 2, 1, "6.00"))
        self.assertEqual(painter_row["load"], "free")
        record = team_workload(actor=self.runner, business_unit=self.unit, person_id=self.worker.pk)["people"][0]
        self.assertEqual((record["hours_by_jd"][0]["code"], record["hours_by_jd"][0]["hours"]), ("JD-00", "6.00"))
        board = factory_dashboard(actor=self.runner, business_unit=self.unit)
        self.assertEqual(board["workload"]["summary"]["busy"], 1)

    def test_machines_maintenance_parts_and_finance_treatment(self):
        self.go_live()
        long_ago = (TODAY() - datetime.timedelta(days=95)).isoformat()
        data = {"code": "cnc-1", "name": "TEST ONLY CNC router", "kind": "cnc", "section": "cnc", "cost": "185000",
                "service_every_days": 90, "last_service_on": long_ago, "bought_on": "2024-03-01"}
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Run factory jobs"):
            save_machine(actor=self.finance, business_unit=self.unit, data=data)
        board = save_machine(actor=self.runner, business_unit=self.unit, data=data)
        with self.assertRaisesMessage(InvalidTransaction, "already a machine CNC-1"):
            save_machine(actor=self.runner, business_unit=self.unit, data=data)
        save_machine(actor=self.runner, business_unit=self.unit,
                     data={"code": "DRILL", "name": "TEST ONLY drills", "kind": "tool", "section": "joinery",
                           "cost": "450", "quantity": 4, "condition": "replace"})
        board = machines_overview(actor=self.runner, business_unit=self.unit)
        cnc = next(row for row in board["machines"] if row["code"] == "CNC-1")
        drill = next(row for row in board["machines"] if row["code"] == "DRILL")
        self.assertEqual((cnc["service"], cnc["treatment"], cnc["finance_missing"]), ("overdue", "asset", True))
        self.assertEqual((drill["treatment"], drill["finance_missing"]), ("expense", False))
        self.assertEqual((board["summary"]["machines"], board["summary"]["need_action"], board["summary"]["maintenance_due"]),
                         (5, 1, 1))
        self.assertEqual({alert["kind"] for alert in board["alerts"]}, {"maintenance", "condition", "finance"})

        board = record_service(actor=self.runner, business_unit=self.unit, machine_id=cnc["id"],
                               data={"kind": "routine", "done_by": "TEST ONLY supplier", "cost": "800"})
        cnc = next(row for row in board["machines"] if row["code"] == "CNC-1")
        self.assertEqual((cnc["service"], cnc["last_service_on"]), ("ok", TODAY().isoformat()))

        asset = FixedAsset.objects.create(business_unit=self.unit, number="FA-TEST-1", name="TEST ONLY CNC", category="machinery",
                                          purchase_date=datetime.date(2024, 3, 1), cost="185000", life_months=60,
                                          paid_from=self.cash, submitted_by=self.admin)
        board = link_asset(actor=self.runner, business_unit=self.unit, machine_id=cnc["id"], data={"asset_number": asset.number})
        cnc = next(row for row in board["machines"] if row["code"] == "CNC-1")
        self.assertEqual((cnc["finance_missing"], cnc["fixed_asset"]["number"]), (False, "FA-TEST-1"))

        board = save_part(actor=self.runner, business_unit=self.unit,
                          data={"name": "TEST ONLY CNC bit 6mm", "kind": "consumable", "machine_id": cnc["id"],
                                "on_hand": "5", "reorder_at": "4", "unit_cost": "60"})
        part = board["parts"][0]
        self.assertFalse(part["order_needed"])
        board = save_part(actor=self.runner, business_unit=self.unit, data={"id": part["id"], "change": "use", "quantity": "2"})
        self.assertEqual((board["parts"][0]["on_hand"], board["parts"][0]["order_needed"]), ("3.00", True))
        with self.assertRaisesMessage(InvalidTransaction, "Only 3.00"):
            save_part(actor=self.runner, business_unit=self.unit, data={"id": part["id"], "change": "use", "quantity": "9"})
        self.assertEqual(factory_dashboard(actor=self.runner, business_unit=self.unit)["machines"]["parts"][0]["on_hand"],
                         "3.00")

    def test_the_factory_learns_from_finished_doors_and_fills_a_new_one(self):
        self.go_live()
        save_tag(actor=self.manager, business_unit=self.unit, data={"name": "Door"})
        save_raw_material(actor=self.manager, business_unit=self.unit,
                          data={"variant_id": self.material.pk, "category": "boards"})
        save_worker(actor=self.manager, business_unit=self.unit, data={"employee_id": self.worker.pk, "section": "joinery"})
        door = {"code": "JD-00", "description": "TEST ONLY flush door", "quantity": "2", "tags": ["Door"],
                "materials": [{"variant_id": self.material.pk, "quantity": "3"}]}
        for number in range(2):
            row = self.approved_job(quote_id=self.sales_order(f"door-{number}").pk, boq=[door])
            for kind in ("drawings", "materials", "saw", "joinery", "paint", "ready"):
                self.step(row, "JD-00", kind, action="done", lead_id=self.worker.pk)
        learned = learned_overview(actor=self.runner, business_unit=self.unit)
        profile = learned["tags"][0]["profile"]
        self.assertEqual((profile["samples"], profile["stages"]["joinery"]["days"], profile["stages"]["metal"]["usually_skipped"]),
                         (2, 1, True))
        self.assertEqual((profile["materials"][0]["per_unit"], profile["leads"]["joinery"]["name"]),
                         ("1.50", "TEST ONLY Carpenter"))

        new = self.job(quote_id=self.sales_order("door-new").pk,
                       boq=[{"code": "JD-00", "description": "TEST ONLY flush door", "quantity": "4", "tags": ["Door"]}],
                       materials=[])
        line = ProductionStage.objects.filter(line__order_id=new["id"]).first().line
        suggestion = suggest_for_line(actor=self.runner, business_unit=self.unit, line_id=line.pk)
        self.assertEqual((suggestion["basis"], suggestion["suggestion"]["materials"][0]["quantity"]), ("same tags", "6.00"))
        job = apply_suggestion(actor=self.runner, business_unit=self.unit, line_id=line.pk, data={"start": "2026-11-09"})
        joinery = next(stage for stage in job["lines"][0]["stages"] if stage["kind"] == "joinery")
        self.assertEqual((joinery["lead"]["name"], bool(joinery["planned_start"])), ("TEST ONLY Carpenter", True))
        line.refresh_from_db()
        self.assertEqual(line.materials[0]["quantity"], "6.00")
        cabinet = self.job(quote_id=self.sales_order("cabinet").pk,
                           boq=[{"code": "JD-00", "description": "TEST ONLY cabinet", "quantity": "1"}])
        untagged = ProductionStage.objects.filter(line__order_id=cabinet["id"]).first().line
        self.assertIsNone(suggest_for_line(actor=self.runner, business_unit=self.unit, line_id=untagged.pk)["suggestion"])

    def test_the_new_api_routes(self):
        self.go_live()
        client = APIClient()
        client.force_login(self.runner)
        for path in ("team", "machines", "learning"):
            response = client.get(f"/api/factory/{path}/?unit={self.unit.pk}")
            self.assertEqual(response.status_code, 200, (path, response.data))
        response = client.get(f"/api/factory/team/?unit={self.unit.pk}&person={self.worker.pk}")
        self.assertEqual((response.status_code, response.data["people"][0]["name"]), (200, "TEST ONLY Carpenter"))
        row = self.approved_job()
        client.post(f"/api/factory/jobs/{row['id']}/materials/?unit={self.unit.pk}",
                    {"variant_id": self.material.pk, "location_id": self.store.pk, "deliver_to_id": self.factory.pk,
                     "quantity": "2"}, format="json")
        slip = factory_overview(actor=self.runner, business_unit=self.unit)["orders"][0]["material_requests"][0]
        response = client.post(f"/api/factory/material-requests/{slip['id']}/?unit={self.unit.pk}", {"action": "print"},
                               format="json")
        self.assertEqual(response.status_code, 400)
        client.force_login(self.manager)
        response = client.post(f"/api/factory/material-requests/{slip['id']}/?unit={self.unit.pk}", {"action": "approve"},
                               format="json")
        self.assertEqual((response.status_code, response.data["material_requests"][0]["status"]), (200, "warehouse"))

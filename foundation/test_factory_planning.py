"""Factory planning: JD stages, the schedule, tags and the dashboard."""

import datetime
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from openpyxl import Workbook
from rest_framework.test import APIClient

from .factory import order_action, read_boq_sheet, update_order
from .factory_planning import factory_dashboard, plan_line, planning_overview, save_tag, update_line, update_stage
from .models import ProductionStage
from .purchasing_services import WorkflowPermissionDenied
from .test_factory import FactoryFixtureMixin
from .transaction_services import InvalidTransaction

TODAY = timezone.localdate


@override_settings(APP_ENV="demo")
class FactoryPlanningTests(FactoryFixtureMixin, TestCase):
    def stage(self, row, code, kind):
        line = next(line for line in row["lines"] if line["code"] == code) if "lines" in row else None
        if line is None:
            return ProductionStage.objects.get(line__order_id=row["id"], line__code=code, kind=kind)
        return next(stage for stage in line["stages"] if stage["kind"] == kind)

    def step(self, row, code, kind, actor=None, **data):
        stage = ProductionStage.objects.get(line__order_id=row["id"], line__code=code, kind=kind)
        return update_stage(actor=actor or self.runner, business_unit=self.unit, stage_id=stage.pk, data=data)

    def planned(self, job_id):
        return next(job for job in planning_overview(actor=self.runner, business_unit=self.unit)["jobs"]
                    if job["id"] == job_id)

    def test_every_jd_gets_its_stages_and_one_status(self):
        self.go_live()
        row = self.job(planned_hours={"joinery": "10", "metal": "4"})
        job = self.planned(row["id"])
        line = job["lines"][0]
        self.assertEqual([stage["kind"] for stage in line["stages"]],
                         ["drawings", "materials", "saw", "joinery", "paint", "metal", "ready"])
        self.assertEqual((line["status_label"], line["current"]["label"]), ("Not started", "Drawings and cutting list"))
        other = self.job(quote_id=self.sales_order("second").pk)
        metal = self.stage(self.planned(other["id"]), "JD-00", "metal")
        self.assertEqual(metal["status"], "skipped", "metal is off unless the job plans metal hours")
        showroom = self.job(purpose="showroom", quote_id=None, variant_id=self.product.pk, quantity="2", boq=[])
        self.assertEqual([line["code"] for line in self.planned(showroom["id"])["lines"]], ["JD-00"])

    def test_auto_plan_skips_fridays_and_keeps_the_plan_when_the_draft_changes(self):
        self.go_live()
        row = self.job()
        line = self.planned(row["id"])["lines"][0]
        job = plan_line(actor=self.runner, business_unit=self.unit, line_id=line["id"], data={"start": "2026-11-05"})
        line = job["lines"][0]
        ends = {stage["kind"]: (stage["planned_start"], stage["planned_end"]) for stage in line["stages"]}
        self.assertEqual(ends["drawings"], ("2026-11-05", "2026-11-08"))   # Thu, Sat, Sun
        self.assertEqual(ends["joinery"], ("2026-11-14", "2026-11-19"))    # Friday the 13th skipped
        self.assertEqual(ends["metal"], ("", ""))
        self.assertEqual((line["start"], line["end"]), ("2026-11-05", "2026-11-28"))
        self.step(row, "JD-00", "drawings", action="start")
        data = {"purpose": "fitout", "quote_id": row["quote"]["id"], "due_date": row["due_date"],
                "boq": [{"code": "JD-00", "description": "TEST ONLY oak door, revised", "quantity": "3"}],
                "planned_hours": {"joinery": "10"}}
        update_order(actor=self.runner, business_unit=self.unit, order_id=row["id"], data=data)
        drawings = self.stage(self.planned(row["id"]), "JD-00", "drawings")
        self.assertEqual((drawings["status"], drawings["planned_end"]), ("in_progress", "2026-11-08"))

    def test_progress_from_drawings_to_completed_with_stuck_in_between(self):
        self.go_live()
        row = self.job(boq=[{"code": "JD-00", "description": "TEST ONLY oak door", "quantity": "2"}])
        self.step(row, "JD-00", "drawings", action="done")
        with self.assertRaisesMessage(InvalidTransaction, "approved and in production"):
            self.step(row, "JD-00", "saw", action="start")
        order_action(actor=self.runner, business_unit=self.unit, order_id=row["id"], action="submit")
        order_action(actor=self.manager, business_unit=self.unit, order_id=row["id"], action="approve")
        self.step(row, "JD-00", "materials", action="done")
        job = self.step(row, "JD-00", "saw", action="start", lead_id=self.worker.pk)
        self.assertEqual((job["lines"][0]["status_label"], job["lines"][0]["team"][0]["name"]),
                         ("In progress", "TEST ONLY Carpenter"))
        with self.assertRaisesMessage(InvalidTransaction, "reason it is stuck"):
            self.step(row, "JD-00", "saw", action="stuck")
        job = self.step(row, "JD-00", "saw", action="stuck", reason="TEST ONLY CNC spindle broken")
        self.assertEqual(job["lines"][0]["status_label"], "Stuck")
        with self.assertRaisesMessage(InvalidTransaction, "future"):
            self.step(row, "JD-00", "saw", action="done", date=(TODAY() + datetime.timedelta(days=1)).isoformat())
        for kind in ("saw", "joinery", "paint", "ready"):
            job = self.step(row, "JD-00", kind, action="done")
        line = job["lines"][0]
        self.assertEqual((line["status"], line["status_label"], line["current"], line["completed_on"]),
                         ("done", "Completed", None, TODAY().isoformat()))
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Run factory jobs"):
            self.step(row, "JD-00", "paint", actor=self.finance, action="reopen")

    def test_tags_are_kept_by_the_factory_manager_and_put_on_each_jd(self):
        self.go_live()
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Approve factory jobs"):
            save_tag(actor=self.runner, business_unit=self.unit, data={"name": "Door"})
        save_tag(actor=self.manager, business_unit=self.unit, data={"name": "Door", "color": "#aa3300"})
        tags = save_tag(actor=self.manager, business_unit=self.unit, data={"name": "Cladding"})["tags"]
        with self.assertRaisesMessage(InvalidTransaction, "already a 'door' tag"):
            save_tag(actor=self.manager, business_unit=self.unit, data={"name": "door"})
        row = self.job(boq=[{"code": "JD-00", "description": "TEST ONLY oak door", "quantity": "2", "tags": ["door"],
                             "area": "Majlis 1", "floor": "Ground floor", "zone": "Zone 1"}])
        line = self.planned(row["id"])["lines"][0]
        self.assertEqual(([tag["name"] for tag in line["tags"]], line["area"], line["zone"]),
                         (["Door"], "Majlis 1", "Zone 1"))
        cladding = next(tag for tag in tags if tag["name"] == "Cladding")
        job = update_line(actor=self.runner, business_unit=self.unit, line_id=line["id"],
                          data={"tag_ids": [cladding["id"]], "area": "Majlis 2"})
        self.assertEqual(([tag["name"] for tag in job["lines"][0]["tags"]], job["lines"][0]["area"]), (["Cladding"], "Majlis 2"))
        with self.assertRaisesMessage(InvalidTransaction, "not on the Factory Manager's tag list"):
            update_line(actor=self.runner, business_unit=self.unit, line_id=line["id"], data={"tag_ids": ["Glass"]})

    def test_the_dashboard_shows_status_late_work_bottlenecks_cost_payments_and_tag_history(self):
        self.go_live()
        save_tag(actor=self.manager, business_unit=self.unit, data={"name": "Door"})
        row = self.approved_job(boq=[{"code": "JD-00", "description": "TEST ONLY oak door", "quantity": "2", "tags": ["Door"]},
                                     {"code": "JD-01", "description": "TEST ONLY cladding", "quantity": "12"}])
        for kind in ("drawings", "materials", "saw", "joinery", "paint", "ready"):
            self.step(row, "JD-00", kind, action="done")
        self.step(row, "JD-01", "drawings", action="done")
        self.step(row, "JD-01", "materials", action="stuck", reason="TEST ONLY veneer not in stock")
        late = (TODAY() - datetime.timedelta(days=5)).isoformat()
        self.step(row, "JD-01", "saw", planned_start=late, planned_end=late)
        self.hours(row, "4")
        board = factory_dashboard(actor=self.runner, business_unit=self.unit)
        self.assertEqual({key: board["items"][key] for key in ("total", "done", "stuck", "in_progress")},
                         {"total": 2, "done": 1, "stuck": 1, "in_progress": 0})
        self.assertEqual(board["bottleneck"], "materials")
        self.assertEqual((board["late"][0]["code"], board["late"][0]["days_late"]), ("JD-01", 5))
        self.assertEqual(board["stuck"][0]["reason"], "TEST ONLY veneer not in stock")
        self.assertEqual(board["costs"][0]["spent"], "161.00")   # 4 h x 35 + 15% overhead
        payment = board["payments"][0]
        self.assertEqual((payment["paid"], payment["issue"]), ("1000.00", False))
        door = board["tags"]["rows"][0]
        self.assertEqual((door["name"], door["years"][-1], door["in_production"]),
                         ("Door", {"year": TODAY().year, "items": 1, "quantity": "2.00"}, 0))

    def test_area_floor_zone_and_tags_come_from_the_excel_boq(self):
        self.go_live()
        save_tag(actor=self.manager, business_unit=self.unit, data={"name": "Door"})
        book = Workbook()
        sheet = book.active
        sheet.append(["Code", "Description", "Area", "Floor", "Zone", "Tags", "Qty"])
        sheet.append(["JD-01", "TEST ONLY entrance door", "Entrance lobby", "Ground floor", "Zone 1", "Door, Glass", 1])
        buffer = BytesIO()
        book.save(buffer)
        found = read_boq_sheet(actor=self.runner, business_unit=self.unit, file_name="boq.xlsx", content=buffer.getvalue())
        line = found["lines"][0]
        self.assertEqual((line["area"], line["floor"], line["zone"], len(line["tags"])),
                         ("Entrance lobby", "Ground floor", "Zone 1", 1))
        self.assertIn("Glass", found["problems"][0])
        row = self.job(boq=found["lines"])
        self.assertEqual(self.planned(row["id"])["lines"][0]["tags"][0]["name"], "Door")

    def test_the_planning_api(self):
        self.go_live()
        row = self.job()
        client = APIClient()
        client.force_login(self.runner)
        planning = client.get(f"/api/factory/planning/?unit={self.unit.pk}")
        self.assertEqual(planning.status_code, 200)
        stage = planning.data["jobs"][0]["lines"][0]["stages"][0]
        response = client.post(f"/api/factory/stages/{stage['id']}/?unit={self.unit.pk}",
                               {"action": "start", "planned_start": TODAY().isoformat(), "planned_end": TODAY().isoformat()},
                               format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["lines"][0]["status"], "in_progress")
        self.assertEqual(client.get(f"/api/factory/dashboard/?unit={self.unit.pk}").data["items"]["in_progress"], 1)
        self.assertEqual(row["number"], "JOB-0001")


@override_settings(APP_ENV="demo")
class FactoryPlanningLiveSwitchTests(FactoryFixtureMixin, TestCase):
    def test_planning_is_hidden_until_live(self):
        with patch.dict("os.environ", {"APP_ENV": "demo"}):
            with self.assertRaises(WorkflowPermissionDenied):
                planning_overview(actor=self.runner, business_unit=self.unit)
        self.assertTrue(Decimal("1"))

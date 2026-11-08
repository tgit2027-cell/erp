"""The factory planner: materials from stock and purchase orders, the team's load, and dates set by hand."""

import datetime
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .factory import update_settings
from .factory_autoplan import (
    build_plan,
    plan_overview,
    replan_if_due,
    replan_now,
    save_brief,
    set_arrival,
)
from .factory_planning import update_stage
from .factory_team import save_worker
from .models import FactoryPlanRun, ProductionBoqLine, ProductionStage
from .purchasing_services import WorkflowPermissionDenied, create_purchase_commitment
from .test_factory import FactoryFixtureMixin
from .transaction_services import InvalidTransaction

TODAY = timezone.localdate
DAY = datetime.timedelta(days=1)


def _work_day(day):
    while day.weekday() == 4:
        day += DAY
    return day


@override_settings(APP_ENV="demo")
class FactoryPlannerTests(FactoryFixtureMixin, TestCase):
    def stage(self, row, code, kind):
        return ProductionStage.objects.get(line__order_id=row["id"], line__code=code, kind=kind)

    def planned(self, plan, row, code, kind):
        job = next(job for job in plan["jobs"] if job["id"] == row["id"])
        line = next(line for line in job["lines"] if line["code"] == code)
        return next(stage for stage in line["stages"] if stage["kind"] == kind)

    def order(self, quantity, key="planner-order"):
        return create_purchase_commitment(
            actor=self.sales, idempotency_key=key, business_unit=self.unit, supplier=self.supplier,
            lines=[{"variant": self.material, "ordered_quantity": Decimal(quantity), "unit_cost": Decimal(50)}])

    def test_workshops_wait_for_materials_on_order_and_purchasing_can_give_the_arrival(self):
        self.go_live()
        # The job needs 30 sheets; 20 are in the store and 10 are ordered today.
        row = self.approved_job(materials=[{"variant_id": self.material.pk, "quantity": "30"}])
        found = self.order("10")
        order_id = found["purchase_commitment_id"]
        plan = build_plan(self.unit)
        job = next(job for job in plan["jobs"] if job["id"] == row["id"])
        expected = TODAY() + datetime.timedelta(days=21)
        self.assertEqual(job["materials_ready_on"], expected.isoformat())
        self.assertIn("Ordered", job["waiting_for"][0]["source"])
        material = plan["materials"][0]
        self.assertEqual((material["free"], material["not_ordered"]), ("20.00", "0.00"))
        self.assertEqual((material["coming"][0]["status_label"], material["coming"][0]["payment_label"]),
                         ("Ordered", "Not paid yet"))
        saw = self.planned(plan, row, "JD-00", "saw")
        self.assertGreater(saw["plan"]["start"], expected.isoformat())
        waits = self.planned(plan, row, "JD-00", "materials")
        self.assertTrue(any("last materials" in reason for reason in waits["reasons"]))

        # Purchasing says the goods come in 5 days: the workshops move forward.
        sooner = TODAY() + datetime.timedelta(days=5)
        with self.assertRaisesMessage(InvalidTransaction, "before they were ordered"):
            set_arrival(actor=self.runner, business_unit=self.unit,
                        data={"order_id": order_id, "expected_on": (TODAY() - DAY * 3).isoformat()})
        plan = set_arrival(actor=self.sales, business_unit=self.unit,
                           data={"order_id": order_id, "expected_on": sooner.isoformat()})
        job = next(job for job in plan["jobs"] if job["id"] == row["id"])
        self.assertEqual(job["materials_ready_on"], sooner.isoformat())
        self.assertTrue(plan["materials"][0]["coming"][0]["expected_given"])

        # 15 more sheets needed than are free or ordered: they are flagged as not ordered yet.
        other = self.approved_job(materials=[{"variant_id": self.material.pk, "quantity": "15"}])
        plan = build_plan(self.unit)
        late = next(job for job in plan["jobs"] if job["id"] == other["id"])
        self.assertEqual(late["not_ordered"][0]["quantity"], "15.00")
        self.assertEqual(late["materials_ready_on"], (TODAY() + datetime.timedelta(days=24)).isoformat())

    def test_the_planner_asks_first_then_plans_floor_by_floor_without_overbooking(self):
        self.go_live()
        save_worker(actor=self.manager, business_unit=self.unit, data={"employee_id": self.worker.pk, "section": "joinery"})
        update_settings(actor=self.master, business_unit=self.unit, data={"stages_per_person": 1})
        row = self.approved_job()
        ProductionBoqLine.objects.filter(order_id=row["id"], code="JD-00").update(floor="Ground floor")
        ProductionBoqLine.objects.filter(order_id=row["id"], code="JD-01").update(floor="First floor")

        # Before planning, the planner asks: which floor first, from when, anything else; with suggestions.
        job = next(job for job in build_plan(self.unit)["jobs"] if job["id"] == row["id"])
        self.assertFalse(job["planned"])
        self.assertEqual(job["questions"]["floors"], ["Ground floor", "First floor"])
        self.assertEqual(job["questions"]["suggested"]["finish_by"], row["due_date"])
        self.assertTrue(job["questions"]["suggested"]["start_on"])
        self.assertIsNone(self.stage(row, "JD-00", "joinery").planned_start)

        start = _work_day(TODAY() + DAY * 2)
        brief = {"start_on": start.isoformat(), "floor_order": ["first floor", "Ground floor"], "notes": "TEST ONLY lobby"}
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Run factory jobs"):
            save_brief(actor=self.finance, business_unit=self.unit, order_id=row["id"], data=brief)
        with self.assertRaisesMessage(InvalidTransaction, "Roof is not one of this job's floors"):
            save_brief(actor=self.runner, business_unit=self.unit, order_id=row["id"], data=brief | {"floor_order": ["Roof"]})
        plan = save_brief(actor=self.runner, business_unit=self.unit, order_id=row["id"], data=brief)
        self.assertGreater(plan["applied"], 0)
        job = next(job for job in plan["jobs"] if job["id"] == row["id"])
        self.assertEqual((job["planned"], job["brief"]["floor_order"]), (True, ["First floor", "Ground floor"]))
        self.assertEqual([line["code"] for line in job["lines"]], ["JD-01", "JD-00"])
        first, second = self.planned(plan, row, "JD-01", "joinery"), self.planned(plan, row, "JD-00", "joinery")
        self.assertEqual((first["plan"]["lead"], second["plan"]["lead"]), ("TEST ONLY Carpenter", "TEST ONLY Carpenter"))
        self.assertGreater(second["plan"]["start"], first["plan"]["end"])
        self.assertTrue(any("on other work until" in reason for reason in second["reasons"]))
        # Saved straight away: the planner builds the schedule by itself.
        self.assertEqual(self.stage(row, "JD-01", "drawings").planned_start, start)
        joinery = self.stage(row, "JD-00", "joinery")
        self.assertEqual((joinery.planned_start.isoformat(), joinery.lead_id), (second["plan"]["start"], self.worker.pk))
        self.assertEqual(plan["summary"]["changes"], 0)

        # A factory admin moves JD-00's paint by hand: the planner keeps it and plans around it.
        paint_on = _work_day(TODAY() + datetime.timedelta(days=60))
        stage = self.stage(row, "JD-00", "paint")
        update_stage(actor=self.runner, business_unit=self.unit, stage_id=stage.pk,
                     data={"planned_start": paint_on.isoformat(), "planned_end": (paint_on + DAY * 2).isoformat()})
        plan = replan_now(actor=self.runner, business_unit=self.unit)
        paint = self.planned(plan, row, "JD-00", "paint")
        self.assertEqual((paint["kept"], paint["plan"]["start"]), (True, paint_on.isoformat()))
        self.assertGreater(self.planned(plan, row, "JD-00", "ready")["plan"]["start"], (paint_on + DAY * 2).isoformat())
        stage.refresh_from_db()
        self.assertEqual((stage.planned_start, stage.planned_by_hand), (paint_on, True))
        self.assertTrue(any(move["code"] == "JD-00" and move["stage"] == "Ready for delivery" for move in plan["runs"][0]["moves"]))

        # Each morning it re-plans by itself, once: the first time anyone opens the schedule that day.
        self.assertIsNone(replan_if_due(self.unit))
        self.assertIn("Morning re-plan", [found["reason"] for found in plan["runs"]])
        self.assertIsNotNone(replan_if_due(self.unit, today=TODAY() + DAY))
        self.assertEqual(FactoryPlanRun.objects.filter(business_unit=self.unit, ran_by__isnull=True).count(), 2)

        # Letting the planner move the paint again.
        update_stage(actor=self.runner, business_unit=self.unit, stage_id=stage.pk, data={"planned_by_hand": False})
        self.assertFalse(self.planned(build_plan(self.unit), row, "JD-00", "paint")["kept"])

    def test_the_planner_api(self):
        self.go_live()
        row = self.approved_job()
        client = APIClient()
        client.force_authenticate(self.runner)
        headers = {"HTTP_X_BUSINESS_UNIT": str(self.unit.pk)}
        found = client.get("/api/factory/plan/", **headers)
        self.assertEqual(found.status_code, 200, found.data)
        self.assertTrue(found.data["can_plan"])
        job = next(job for job in found.data["jobs"] if job["id"] == row["id"])
        answer = {"start_on": job["questions"]["suggested"]["start_on"], "first_codes": ["JD-01"]}
        saved = client.post(f"/api/factory/orders/{row['id']}/plan-brief/", answer, format="json", **headers)
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(next(job for job in saved.data["jobs"] if job["id"] == row["id"])["brief"]["first_codes"], ["JD-01"])
        again = client.post("/api/factory/plan/", {}, format="json", **headers)
        self.assertEqual((again.status_code, again.data["applied"]), (200, 0))
        self.assertEqual(plan_overview(actor=self.runner, business_unit=self.unit)["rules"]["order_lead_days"], 21)

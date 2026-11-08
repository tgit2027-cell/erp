"""Areas: each person sees only the parts of the ERP allocated to them (Sam, 5 Nov 2026)."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .areas import areas_for, my_areas
from .models import AccessLevelAssignment, BusinessUnit, DemoUserMarker, LegalEntity, UserActionGrant, UserArea
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import InvalidTransaction
from .user_admin import change_person, person_detail

A = UserActionGrant.Action


@override_settings(APP_ENV="demo")
class AreaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = LegalEntity.objects.create(name="Tivoli", code="tivoli")
        cls.tvl = BusinessUnit.objects.create(legal_entity=company, name="Tivoli", code="TVL")
        cls.rbb = BusinessUnit.objects.create(legal_entity=company, name="Roche Bobois", code="RBB")
        cls.owner = cls._person("test_owner", superuser=True)
        cls.sales = cls._person("test_rbb_sales", (cls.rbb, A.READ_STOCK), (cls.rbb, A.CREATE_ORDER))
        cls.accountant = cls._person("test_accountant", (cls.tvl, A.VIEW_LEDGER), (cls.tvl, A.PAY_SUPPLIER))
        cls.storekeeper = cls._person("test_storekeeper", (cls.tvl, A.READ_STOCK), (cls.tvl, A.EXECUTE_TRANSFER))
        cls.newcomer = cls._person("test_newcomer", (cls.tvl, A.CREATE_ORDER))
        AccessLevelAssignment.objects.create(user=cls.newcomer, level=AccessLevelAssignment.Level.SALES, assigned_by=cls.owner)
        cls.general_admin = cls._person("test_general_admin")
        AccessLevelAssignment.objects.create(user=cls.general_admin, level=AccessLevelAssignment.Level.GENERAL_ADMIN,
                                             assigned_by=cls.owner)

    @classmethod
    def _person(cls, username, *grants, superuser=False):
        user = get_user_model().objects.create_user(username=username, password="unused-test-password",
                                                    is_superuser=superuser, is_staff=superuser)
        DemoUserMarker.objects.create(user=user)
        for unit, action in grants:
            UserActionGrant.objects.create(user=user, business_unit=unit, action=action)
        return user

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)

    def area(self, person, area, enabled, actor=None):
        return change_person(actor=actor or self.owner, user_id=person.pk,
                             change={"kind": "area", "area": area, "enabled": enabled})

    def test_the_master_admin_sees_every_area(self):
        keys, source = areas_for(self.owner)
        self.assertEqual(source, "all")
        self.assertIn("factory", keys)
        self.assertIn("governance", my_areas(self.owner)["sections"])

    def test_without_chosen_areas_permissions_decide_and_sales_do_not_see_the_warehouse(self):
        mine = my_areas(self.sales)
        self.assertEqual([row["key"] for row in mine["areas"]], ["roche-bobois"])
        self.assertEqual(mine["areas"][0]["unit"]["code"], "RBB")
        self.assertEqual(mine["source"], "from_permissions")
        self.assertIn("retail", mine["sections"])
        for hidden in ("inventory", "finance", "factory", "governance", "hr"):
            self.assertNotIn(hidden, mine["sections"])
        self.assertEqual(areas_for(self.accountant)[0], {"finance"})
        self.assertEqual(areas_for(self.storekeeper)[0], {"warehouse", "logistics"})

    def test_someone_who_only_reads_stock_keeps_their_showroom_and_the_stock_pages(self):
        viewer = self._person("test_viewer", (self.rbb, A.READ_STOCK))
        self.assertEqual(areas_for(viewer)[0], {"roche-bobois", "warehouse"})

    def test_the_first_tick_keeps_what_the_person_already_sees(self):
        detail = self.area(self.sales, "natuzzi", True)
        self.assertEqual(detail["areas"]["chosen"], ["natuzzi", "roche-bobois"])
        self.assertEqual(detail["areas"]["source"], "chosen")
        detail = self.area(self.sales, "roche-bobois", False)
        self.assertEqual(areas_for(self.sales)[0], {"natuzzi"})

    def test_the_last_area_cannot_be_unticked(self):
        self.area(self.sales, "roche-bobois", True)
        with self.assertRaises(InvalidTransaction):
            self.area(self.sales, "roche-bobois", False)

    def test_all_areas(self):
        self.area(self.storekeeper, "all", True)
        self.assertEqual(areas_for(self.storekeeper)[1], "all")
        self.area(self.storekeeper, "all", False)
        self.assertEqual(areas_for(self.storekeeper), ({"warehouse", "logistics"}, "from_permissions"))

    def test_who_may_change_areas(self):
        self.area(self.newcomer, "factory", True, actor=self.general_admin)
        self.assertEqual(areas_for(self.newcomer)[0], {"furniture", "factory"})
        with self.assertRaises(WorkflowPermissionDenied):
            self.area(self.sales, "furniture", True, actor=self.general_admin)  # a custom user: Master Admin only
        with self.assertRaises(WorkflowPermissionDenied):
            self.area(self.sales, "finance", True, actor=self.accountant)
        with self.assertRaises(WorkflowPermissionDenied):
            self.area(self.owner, "finance", False)
        with self.assertRaises(InvalidTransaction):
            self.area(self.sales, "moon", True)

    def test_areas_are_on_the_person_screen_and_the_sign_in_details(self):
        detail = person_detail(actor=self.owner, user_id=self.sales.pk)
        self.assertEqual(detail["areas"]["seen"], ["roche-bobois"])
        self.assertIn("projects", [row["key"] for row in detail["areas"]["choices"]])
        client = APIClient()
        client.force_login(self.sales)
        me = client.get("/api/auth/me/").json()
        self.assertEqual([row["key"] for row in me["areas"]["areas"]], ["roche-bobois"])

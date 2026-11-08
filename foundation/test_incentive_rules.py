"""The incentive brief's calculation checks, rebuilt as tests of incentive_rules (no database)."""

from decimal import Decimal as D

from django.test import SimpleTestCase

from . import incentive_rules as rules

P = rules.default_policy()
OPS = {"collections": "100", "gp_margin": "90", "inventory": "80", "strategic": "110"}


def company(**changes):
    figures = {"revenue_target": "1000", "revenue_actual": "1050", "ebitda_target": "100", "ebitda_actual": "95", "operational": OPS}
    figures.update(changes)
    return rules.company_index(P, **figures)


class StepBandTests(SimpleTestCase):
    def test_each_threshold_applies_until_the_next_without_interpolation(self):
        bands = P["revenue_score"]
        self.assertEqual(rules.band(D("84.99"), bands), 0)
        self.assertEqual(rules.band(D("85"), bands), 40)
        self.assertEqual(rules.band(D("89.999"), bands), 40)
        self.assertEqual(rules.band(D("100"), bands), 100)
        self.assertEqual(rules.band(D("119.99"), bands), 125)
        self.assertEqual(rules.band(D("250"), bands), 150)

    def test_ebitda_and_factor_bands(self):
        self.assertEqual(rules.band(D("59.9"), P["ebitda_score"]), 0)
        self.assertEqual(rules.band(D("130"), P["ebitda_score"]), 150)
        self.assertEqual(rules.band(D("69.99"), P["company_factor"]), 0)
        self.assertEqual(rules.band(D("95"), P["company_factor"]), 90)


class CompanyIndexTests(SimpleTestCase):
    def test_cpi_is_weighted_scores_and_operational_is_the_average_of_four(self):
        result = company()
        self.assertFalse(result.is_pending)
        # revenue 105% -> 110, EBITDA 95% -> 90, operational (100, 80, 50, 125) -> 88.75
        self.assertEqual(result.figures["operational_score"], D("88.75"))
        self.assertEqual(result.figures["cpi"], D("97.75"))
        self.assertEqual(result.amount, 90)

    def test_negative_ebitda_eliminates_the_company_factor(self):
        result = company(ebitda_actual="-5", revenue_actual="1300")
        self.assertTrue(result.figures["negative_ebitda"])
        self.assertEqual(result.amount, 0)

    def test_missing_inputs_and_zero_targets_are_pending_not_zero(self):
        self.assertIn("Company EBITDA: the actual figure is missing.", company(ebitda_actual=None).pending)
        self.assertTrue(any("zero or less" in reason for reason in company(revenue_target="0").pending))
        missing = company(operational={"collections": "100"})
        self.assertEqual(len(missing.pending), 3)
        self.assertIsNone(missing.amount)

    def test_an_actual_zero_is_a_result_not_missing(self):
        result = company(revenue_actual="0")
        self.assertFalse(result.is_pending)
        self.assertEqual(result.figures["revenue_score"], 0)


class CommissionTests(SimpleTestCase):
    base = {"channel": "retail", "revenue_target": "1000000", "gp_target": "300000", "gp_actual": "288000",
            "collection_fraction": "0.8"}

    def commission(self, revenue, cpi_company=None, **changes):
        args = {**self.base, "revenue_actual": revenue, **changes}
        return rules.sales_commission(P, company=cpi_company or company(), **args)

    def test_below_the_annual_target_there_is_no_commission(self):
        result = self.commission("999999.99")
        self.assertEqual(result.amount, 0)
        self.assertFalse(result.figures["achieved"])

    def test_at_target_the_first_tranche_applies_to_all_of_it(self):
        result = self.commission("1000000", gp_actual="300000", collection_fraction="1")
        self.assertEqual(result.amount, D("7000.00"))

    def test_each_rate_applies_only_to_its_own_tranche(self):
        result = self.commission("1250000")
        # 7,000 + 850 + 1,000 + 575 = 9,425; GP 96% -> 90%; collection 0.8
        self.assertEqual(result.figures["progressive"], D("9425"))
        self.assertEqual(result.figures["gp_factor"], 90)
        self.assertEqual(result.amount, D("6786.00"))

    def test_top_tranche_has_no_ceiling(self):
        amount, _ = rules.progressive(D("2000000"), D("1000000"), P["commission_schedules"]["retail"])
        # 7,000 + 850 + 1,000 + 1,150 + 1,300 + 1,400 + 500,000 x 1.5% = 20,200
        self.assertEqual(amount, D("20200"))

    def test_gp_factor_boundaries(self):
        for gp, factor in (("269999", 0), ("270000", 75), ("285000", 90), ("300000", 100)):
            with self.subTest(gp=gp):
                self.assertEqual(self.commission("1000000", gp_actual=gp).figures["gp_factor"], factor)

    def test_collection_fraction_is_an_explicit_input_between_0_and_1(self):
        self.assertTrue(self.commission("1100000", collection_fraction=None).is_pending)
        self.assertTrue(self.commission("1100000", collection_fraction="1.2").is_pending)
        self.assertEqual(self.commission("1000000", gp_actual="300000", collection_fraction="0").amount, 0)

    def test_cpi_bonus_only_when_target_met_and_ebitda_not_negative(self):
        strong = company(revenue_actual="1120", ebitda_actual="110", operational={k: "110" for k in OPS})
        self.assertGreaterEqual(strong.figures["cpi"], 105)
        with_bonus = self.commission("1250000", cpi_company=strong)
        self.assertEqual(with_bonus.figures["cpi_bonus"], D("6786") * rules.band(strong.figures["cpi"], P["sales_cpi_bonus"]) / 100)
        self.assertEqual(with_bonus.figures["referral_base"], D("6786"))
        negative = company(revenue_actual="1300", ebitda_actual="-1", operational={k: "120" for k in OPS})
        self.assertEqual(self.commission("1250000", cpi_company=negative).figures["cpi_bonus"], 0)
        self.assertEqual(self.commission("1250000", cpi_company=negative).amount, D("6786.00"))

    def test_other_channels_wait_for_their_schedule(self):
        result = self.commission("1250000", channel="wholesale")
        self.assertTrue(result.is_pending)
        self.assertIn("Wholesale", result.pending[0])

    def test_pending_cpi_blocks_a_payable_commission_but_not_a_zero_one(self):
        pending = company(ebitda_actual=None)
        self.assertTrue(self.commission("1250000", cpi_company=pending).is_pending)
        self.assertEqual(self.commission("900000", cpi_company=pending).amount, 0)


class SupervisorTests(SimpleTestCase):
    personal = {"revenue_target": "500000", "revenue_actual": "400000", "gp_target": "150000", "gp_actual": "150000",
                "collection_fraction": "1"}
    team = {"revenue_target": "2000000", "revenue_actual": "2200000", "gp_target": "600000", "gp_actual": "600000",
            "collection_fraction": "1"}

    def test_personal_underachievement_does_not_remove_the_team_part(self):
        result = rules.supervisor_hybrid(P, company=company(), channel="retail", personal=self.personal, team=self.team,
                                         team_share="50")
        # team: 14,000 + 200,000 x 0.85% = 15,700; 60% x 15,700 x 50% = 4,710
        self.assertEqual(result.figures["personal"], 0)
        self.assertEqual(result.amount, D("4710.00"))

    def test_personal_part_is_weighted_and_gets_the_cpi_bonus_alone(self):
        strong = company(revenue_actual="1120", ebitda_actual="110", operational={k: "110" for k in OPS})
        personal = {**self.personal, "revenue_actual": "500000"}
        result = rules.supervisor_hybrid(P, company=strong, channel="retail", personal=personal, team=self.team, team_share="100")
        weighted = D("3500") * D("0.4")
        bonus = weighted * rules.band(strong.figures["cpi"], P["sales_cpi_bonus"]) / 100
        self.assertEqual(result.amount, rules.money(weighted + bonus + D("15700") * D("0.6")))

    def test_missing_team_or_share_is_pending(self):
        self.assertTrue(rules.supervisor_hybrid(P, company=company(), channel="retail", personal=self.personal, team=None,
                                                team_share="50").is_pending)
        self.assertTrue(rules.supervisor_hybrid(P, company=company(), channel="retail", personal=self.personal, team=self.team,
                                                team_share=None).is_pending)


class FixedRewardTests(SimpleTestCase):
    def unit(self, factor="100"):
        return rules.Result(amount=D(factor))

    def reward(self, **changes):
        args = {"company": company(), "unit": self.unit(), "band_code": "PR3", "category": "hub_admin", "rating": "meets",
                "proration": "1"}
        args.update(changes)
        return rules.fixed_reward(P, **args)

    def test_fixed_qar_target_times_weighted_factor_times_proration(self):
        # 50% x 90 + 25% x 100 + 25% x 100 = 95%; 3,000 x 95% = 2,850
        self.assertEqual(self.reward().amount, D("2850.00"))
        self.assertEqual(self.reward(proration=D(6) / 12).amount, D("1425.00"))

    def test_company_gateway_zero_means_no_reward(self):
        weak = company(ebitda_actual="-1")
        self.assertEqual(self.reward(company=weak, unit=self.unit("150"), rating="exceptional").amount, 0)

    def test_weighted_factor_is_capped(self):
        strong = company(revenue_actual="1300", ebitda_actual="140", operational={k: "120" for k in OPS})
        self.assertEqual(strong.amount, 150)
        result = self.reward(company=strong, unit=self.unit("200"), rating="exceptional", band_code="M1")
        self.assertEqual(result.figures["weighted"], 150)
        self.assertEqual(result.amount, D("27000.00"))

    def test_missing_rating_band_category_or_unconfirmed_category_is_pending(self):
        self.assertTrue(self.reward(rating="").is_pending)
        self.assertTrue(self.reward(band_code="").is_pending)
        self.assertTrue(self.reward(category="").is_pending)
        self.assertIn("not confirmed", self.reward(category="sales_floor").pending[0])

    def test_rewards_are_not_a_share_of_salary(self):
        self.assertNotIn("salary", str(P).lower())


class ScheduleAndPolicyTests(SimpleTestCase):
    def test_instalments_round_april_and_august_and_december_takes_the_rest(self):
        self.assertEqual(rules.instalments(P, D("1000.01")), [(4, D("400.00")), (8, D("300.00")), (12, D("300.01"))])
        parts = rules.instalments(P, D("333.33"))
        self.assertEqual(sum(amount for _, amount in parts), D("333.33"))

    def test_default_policy_is_consistent_and_bad_weights_are_refused(self):
        self.assertEqual(rules.check_policy(P), [])
        broken = rules.default_policy()
        broken["cpi_weights"]["revenue"] = "50"
        broken["instalments"][0]["percent"] = "50"
        self.assertEqual(len(rules.check_policy(broken)), 2)

    def test_default_policy_is_a_copy(self):
        copy = rules.default_policy()
        copy["reward_bands"]["PR1"] = "1"
        self.assertEqual(rules.DEFAULT_POLICY["reward_bands"]["PR1"], "1500")

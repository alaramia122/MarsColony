import unittest
from pathlib import Path

from tools.scenario_sim import load_scenario, simulate_campaign, simulate_period, simulate_reactor_service


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = load_scenario(ROOT / "data" / "scenarios" / "ares-01.json")


class Ares01ScenarioTests(unittest.TestCase):
    def test_period_duration_and_population_allocation(self):
        self.assertEqual(SCENARIO["time"]["period_count"] * SCENARIO["time"]["period_sols"], 120)
        self.assertEqual(sum(SCENARIO["population"]["fte_allocation"].values()), SCENARIO["population"]["available_fte"])
        self.assertLessEqual(SCENARIO["population"]["available_fte"], SCENARIO["population"]["total"])

    def test_nominal_period_balances(self):
        result = simulate_period(SCENARIO)
        self.assertAlmostEqual(result["period_hours"], 739.791, places=5)
        self.assertAlmostEqual(result["oxygen_demand_kg"], 621.42444, places=5)
        self.assertAlmostEqual(result["food_demand_kg"], 458.67042, places=5)
        self.assertAlmostEqual(result["gross_water_kg"], 21661.08048, places=5)
        self.assertAlmostEqual(result["water_demand_kg"], 533.2216096, places=5)
        self.assertAlmostEqual(result["raw_ice_demand_kg"], 1250.0, places=6)
        self.assertGreater(result["energy_margin_kwh"], 0.0)
        self.assertAlmostEqual(result["generated_kwh"], 53264.952, places=3)
        self.assertAlmostEqual(result["consumed_kwh"], 41372.267, places=3)
        self.assertAlmostEqual(result["energy_margin_kwh"], 11892.685, places=3)
        self.assertAlmostEqual(result["average_load_kw"], 55.92426374476034, places=6)

    def test_four_period_campaign_has_positive_stocks_and_budget(self):
        result = simulate_campaign(SCENARIO)
        stocks = result["ending_stocks"]
        self.assertTrue(result["all_periods_without_unmet_demand"])
        self.assertAlmostEqual(stocks["oxygen_kg"], 364.30224, places=5)
        self.assertAlmostEqual(stocks["food_solids_kg"], 322.81832, places=5)
        self.assertAlmostEqual(stocks["usable_water_kg"], 2867.1135616, places=5)
        self.assertAlmostEqual(stocks["raw_ice_kg"], 2625.0, places=6)
        self.assertAlmostEqual(result["ending_budget_credits"], 500.0, places=6)
        goals = SCENARIO["victory_conditions"]
        self.assertGreaterEqual(stocks["oxygen_kg"], goals["minimum_oxygen_end_kg"])
        self.assertGreaterEqual(stocks["food_solids_kg"], goals["minimum_food_end_kg"])
        self.assertGreaterEqual(stocks["usable_water_kg"], goals["minimum_water_end_kg"])
        self.assertGreaterEqual(result["ending_budget_credits"], goals["minimum_budget_credits"])

    def test_water_output_is_limited_by_available_raw_ice(self):
        constrained = dict(SCENARIO)
        constrained["stocks"] = dict(SCENARIO["stocks"])
        constrained["stocks"]["raw_ice_kg"] = 100.0
        result = simulate_period(constrained)
        self.assertAlmostEqual(result["water_output_kg"], 80.0, places=6)
        self.assertAlmostEqual(result["raw_ice_shortfall_kg"], 1150.0, places=6)
        self.assertEqual(result["raw_ice_end_kg"], 0.0)

    def test_projects_fit_the_initial_budget_and_available_labor(self):
        for project in SCENARIO["projects"]:
            self.assertLessEqual(project["cost_credits"], SCENARIO["finance"]["starting_credits"])
            self.assertLessEqual(project["labor_fte"], SCENARIO["population"]["available_fte"])

    def test_reactor_service_preserves_critical_load(self):
        result = simulate_reactor_service(SCENARIO, duration_hours=48.0)
        self.assertGreaterEqual(result["steady_margin_kw"], 0.0)
        self.assertLessEqual(result["battery_required_kwh"], result["battery_capacity_kwh"])
        self.assertAlmostEqual(result["battery_required_kwh"], 96.0, places=6)

    def test_one_period_oxygen_plant_outage_uses_reserve_without_stockout(self):
        period_scenario = dict(SCENARIO)
        period_scenario["stocks"] = dict(SCENARIO["stocks"])
        result = simulate_period(period_scenario, oxygen_output_multiplier=0.0)
        self.assertEqual(result["oxygen_unmet_kg"], 0.0)
        self.assertAlmostEqual(result["oxygen_end_kg"], 1178.57556, places=5)

    def test_water_extraction_reduction_can_be_absorbed_by_initial_reserve(self):
        result = simulate_period(SCENARIO, water_output_multiplier=0.5)
        self.assertEqual(result["water_unmet_kg"], 0.0)
        self.assertEqual(result["raw_ice_shortfall_kg"], 0.0)
        self.assertGreater(result["water_end_kg"], 0.0)


if __name__ == "__main__":
    unittest.main()

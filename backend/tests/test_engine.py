import json
import unittest
from pathlib import Path

from backend.app.domain.engine import DomainError, advance_period, initial_state


ROOT = Path(__file__).resolve().parents[2]
with (ROOT / "data" / "scenarios" / "ares-01.json").open(encoding="utf-8") as source:
    SCENARIO = json.load(source)


class PeriodEngineTests(unittest.TestCase):
    def test_initial_state_uses_scenario_stocks(self):
        state = initial_state(SCENARIO)
        self.assertEqual(state["period"], 0)
        self.assertEqual(state["oxygen_kg"], 1800.0)
        self.assertEqual(state["budget_credits"], 600.0)

    def test_first_period_applies_oxygen_event(self):
        state = initial_state(SCENARIO)
        new_state, report = advance_period(SCENARIO, state, 1)
        self.assertAlmostEqual(report["oxygen_output_kg"], 150.0)
        self.assertAlmostEqual(report["oxygen_demand_kg"], 621.42444, places=5)
        self.assertAlmostEqual(new_state["oxygen_kg"], 1328.57556, places=5)
        self.assertEqual(report["event_id"], "EVT-O2-ISRU-DEGRADATION")

    def test_four_periods_match_reference_campaign(self):
        state = initial_state(SCENARIO)
        reports = []
        for period in range(1, 5):
            state, report = advance_period(SCENARIO, state, period)
            reports.append(report)
        self.assertAlmostEqual(state["oxygen_kg"], 364.30224, places=5)
        self.assertAlmostEqual(state["food_solids_kg"], 322.81832, places=5)
        self.assertAlmostEqual(state["usable_water_kg"], 2867.1135616, places=5)
        self.assertAlmostEqual(state["raw_ice_kg"], 2625.0, places=6)
        self.assertAlmostEqual(state["budget_credits"], 500.0, places=6)
        self.assertTrue(all(item["demand_fully_met"] for item in reports))

    def test_periods_must_be_advanced_sequentially(self):
        with self.assertRaises(DomainError):
            advance_period(SCENARIO, initial_state(SCENARIO), 2)

    def test_campaign_cannot_advance_past_scenario(self):
        state = initial_state(SCENARIO)
        for period in range(1, 5):
            state, _ = advance_period(SCENARIO, state, period)
        with self.assertRaises(DomainError):
            advance_period(SCENARIO, state, 5)

    def test_raw_ice_limits_water_production(self):
        state = initial_state(SCENARIO)
        state["raw_ice_kg"] = 100.0
        new_state, report = advance_period(SCENARIO, state, 1)
        self.assertEqual(new_state["raw_ice_kg"], 0.0)
        self.assertAlmostEqual(report["water_output_kg"], 80.0)
        self.assertGreater(report["raw_ice_shortfall_kg"], 0.0)

    def test_scheduled_reactor_service_checks_critical_reserve(self):
        state = initial_state(SCENARIO)
        _, report = advance_period(SCENARIO, state, 2)
        service = report["power_service"]
        self.assertEqual(service["modules_online"], 1)
        self.assertAlmostEqual(service["available_kw"], 36.0)
        self.assertAlmostEqual(service["battery_required_kwh"], 96.0)
        self.assertTrue(service["within_reserve"])


if __name__ == "__main__":
    unittest.main()

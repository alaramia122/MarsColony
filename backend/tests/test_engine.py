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
        self.assertAlmostEqual(
            reports[0]["material_processes"]["oxygen_isru"]["energy_used_kwh"],
            4500.0,
            places=6,
        )
        self.assertAlmostEqual(
            reports[0]["material_processes"]["ice_to_usable_water"]["energy_used_kwh"],
            5000.0,
            places=6,
        )

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

    def test_budget_shortfall_is_reported_without_negative_balance(self):
        state = initial_state(SCENARIO)
        state["budget_credits"] = 10.0
        new_state, report = advance_period(SCENARIO, state, 1)
        self.assertEqual(new_state["budget_credits"], 0.0)
        self.assertEqual(report["budget_unmet_credits"], 15.0)

    def test_scheduled_reactor_service_checks_critical_reserve_and_dispatch(self):
        state = initial_state(SCENARIO)
        state, _ = advance_period(SCENARIO, state, 1)
        _, report = advance_period(SCENARIO, state, 2)
        service = report["power_service"]
        self.assertEqual(service["modules_online"], 1)
        self.assertAlmostEqual(service["available_kw"], 36.0)
        self.assertAlmostEqual(service["battery_required_kwh"], 96.0)
        self.assertTrue(service["within_reserve"])
        dispatch = report["power_dispatch"]
        self.assertAlmostEqual(dispatch["allocated_kw"], 36.0)
        self.assertAlmostEqual(dispatch["loads"][0]["allocated_kw"], 31.0)
        self.assertAlmostEqual(dispatch["loads"][1]["allocated_kw"], 5.0)
        self.assertGreater(dispatch["shed_kwh"], 0.0)

        period_hours = SCENARIO["time"]["period_sols"] * SCENARIO["time"]["hours_per_sol"]
        requested_load_kw = sum(SCENARIO["power"]["loads_kw"].values())
        expected_generation = 72.0 * (period_hours - 48.0) + 36.0 * 48.0
        expected_consumption = (
            requested_load_kw * (period_hours - 48.0)
            + 36.0 * 48.0
            + 96.0
        )
        self.assertAlmostEqual(report["generated_kwh"], expected_generation, places=5)
        self.assertAlmostEqual(report["consumed_kwh"], expected_consumption, places=5)
        self.assertAlmostEqual(
            report["energy_margin_kwh"],
            expected_generation + 96.0 - expected_consumption,
            places=5,
        )
        self.assertAlmostEqual(service["battery_discharge_kwh"], 96.0)
        self.assertEqual(service["battery_shortfall_kwh"], 0.0)

    def test_power_service_cannot_exceed_period_duration(self):
        scenario = json.loads(json.dumps(SCENARIO))
        scenario["scripted_events"][1]["duration_hours"] = 1000.0
        state = initial_state(scenario)
        state, _ = advance_period(scenario, state, 1)
        with self.assertRaises(DomainError):
            advance_period(scenario, state, 2)


if __name__ == "__main__":
    unittest.main()

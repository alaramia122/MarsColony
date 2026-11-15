import json
import unittest
from pathlib import Path

from backend.app.domain.engine import DomainError, advance_period, initial_state


ROOT = Path(__file__).resolve().parents[2]
with (ROOT / "data" / "scenarios" / "ares-01.json").open(encoding="utf-8") as source:
    SCENARIO = json.load(source)


class PeriodEngineTests(unittest.TestCase):
    def test_initial_state_uses_scenario_resources_and_battery(self):
        state = initial_state(SCENARIO)
        self.assertEqual(state["period"], 0)
        self.assertEqual(state["oxygen_kg"], 1800.0)
        self.assertEqual(state["budget_credits"], 600.0)
        self.assertEqual(state["battery_soc_kwh"], 1000.0)

    def test_first_period_applies_scenario_defined_oxygen_event(self):
        state = initial_state(SCENARIO)
        new_state, report = advance_period(SCENARIO, state, 1)
        self.assertAlmostEqual(report["oxygen_output_kg"], 150.0)
        self.assertAlmostEqual(report["oxygen_demand_kg"], 621.42444, places=5)
        self.assertAlmostEqual(new_state["oxygen_kg"], 1328.57556, places=5)
        self.assertEqual(report["event_id"], "EVT-O2-ISRU-DEGRADATION")

    def test_four_periods_match_energy_coupled_campaign_invariants(self):
        state = initial_state(SCENARIO)
        reports = []
        for period in range(1, 5):
            previous = dict(state)
            state, report = advance_period(SCENARIO, state, period)
            reports.append(report)
            self.assertEqual(previous["period"], period - 1)
            self.assertGreaterEqual(state["oxygen_kg"], 0.0)
            self.assertGreaterEqual(state["food_solids_kg"], 0.0)
            self.assertGreaterEqual(state["usable_water_kg"], 0.0)
            self.assertGreaterEqual(state["raw_ice_kg"], 0.0)
            self.assertGreaterEqual(state["budget_credits"], 0.0)
            self.assertGreaterEqual(
                state["battery_soc_kwh"],
                SCENARIO["power"]["battery"]["minimum_charge_kwh"],
            )
            self.assertLessEqual(
                state["battery_soc_kwh"],
                SCENARIO["power"]["battery"]["capacity_kwh"],
            )

        self.assertTrue(all(item["essential_demands_met"] for item in reports))
        self.assertAlmostEqual(state["oxygen_kg"], 356.037283, places=5)
        self.assertAlmostEqual(state["food_solids_kg"], 322.818320, places=5)
        self.assertAlmostEqual(state["usable_water_kg"], 2802.230370, places=5)
        self.assertAlmostEqual(state["raw_ice_kg"], 2706.103987, places=5)
        self.assertAlmostEqual(state["budget_credits"], 500.0, places=6)
        self.assertGreaterEqual(state["oxygen_kg"], SCENARIO["victory_conditions"]["minimum_oxygen_end_kg"])
        self.assertGreaterEqual(state["food_solids_kg"], SCENARIO["victory_conditions"]["minimum_food_end_kg"])
        self.assertGreaterEqual(state["usable_water_kg"], SCENARIO["victory_conditions"]["minimum_water_end_kg"])
        self.assertAlmostEqual(state["battery_soc_kwh"], 1000.0, places=5)

        self.assertAlmostEqual(
            reports[0]["material_processes"]["oxygen_isru"]["energy_used_kwh"],
            4500.0,
            places=5,
        )
        self.assertAlmostEqual(
            reports[0]["material_processes"]["ice_to_usable_water"]["energy_used_kwh"],
            5000.0,
            places=5,
        )
        self.assertLess(reports[1]["oxygen_output_kg"], 300.0)
        self.assertLess(reports[1]["water_output_kg"], 1000.0)
        self.assertAlmostEqual(reports[1]["oxygen_output_kg"], 291.735043, places=5)
        self.assertAlmostEqual(reports[1]["water_output_kg"], 935.116810, places=5)
        self.assertAlmostEqual(reports[1]["power_service"]["battery_discharge_kwh"], 96.0)
        self.assertFalse(reports[1]["all_electrical_loads_served"])

    def test_period_two_dispatches_power_and_discharges_battery(self):
        state = initial_state(SCENARIO)
        state, _ = advance_period(SCENARIO, state, 1)
        new_state, report = advance_period(SCENARIO, state, 2)

        service = report["power_service"]
        self.assertEqual(service["modules_online"], 1)
        self.assertAlmostEqual(service["available_generation_kw"], 36.0)
        self.assertAlmostEqual(service["battery_required_kwh"], 96.0)
        self.assertTrue(service["within_reserve"])
        dispatch = service["dispatch"]
        allocations = {item["load_id"]: item for item in dispatch["loads"]}
        self.assertAlmostEqual(dispatch["allocated_kw"], 38.0)
        self.assertAlmostEqual(allocations["critical_systems"]["allocated_kw"], 31.0)
        self.assertAlmostEqual(allocations["oxygen_isru"]["allocated_kw"], 7.0)
        self.assertAlmostEqual(allocations["water_processing"]["allocated_kw"], 0.0)
        self.assertGreater(dispatch["shed_kwh"], 0.0)
        self.assertFalse(report["all_electrical_loads_served"])

        battery = report["battery"]
        self.assertAlmostEqual(battery["state_of_charge_start_kwh"], 1000.0)
        self.assertAlmostEqual(battery["state_of_charge_end_kwh"], 1000.0 - 96.0 / 0.9)
        self.assertAlmostEqual(service["battery_discharge_kwh"], 96.0)
        self.assertAlmostEqual(
            new_state["battery_soc_kwh"], battery["state_of_charge_end_kwh"], places=5
        )
        self.assertAlmostEqual(
            report["consumed_kwh"],
            sum(
                interval["dispatch"]["allocated_kwh"]
                for interval in report["power_intervals"]
            ),
        )
        self.assertAlmostEqual(
            report["energy_margin_kwh"],
            report["generated_kwh"] + report["battery_discharge_kwh"]
            - report["consumed_kwh"] - report["battery_charge_input_kwh"],
        )

    def test_battery_recharges_from_generation_surplus_after_maintenance(self):
        state = initial_state(SCENARIO)
        for period in (1, 2):
            state, _ = advance_period(SCENARIO, state, period)
        self.assertLess(state["battery_soc_kwh"], 1000.0)
        state, report = advance_period(SCENARIO, state, 3)
        self.assertAlmostEqual(state["battery_soc_kwh"], 1000.0, places=5)
        self.assertGreater(report["battery_charge_input_kwh"], 0.0)
        self.assertGreater(report["battery_loss_kwh"], 0.0)

    def test_battery_reserve_shortfall_is_explicit(self):
        scenario = json.loads(json.dumps(SCENARIO))
        scenario["power"]["battery"]["initial_charge_kwh"] = 100.0
        scenario["power"]["battery"]["maximum_charge_kw"] = 0.0
        state = initial_state(scenario)
        state, _ = advance_period(scenario, state, 1)
        state, report = advance_period(scenario, state, 2)
        self.assertFalse(report["power_service"]["within_reserve"])
        self.assertGreater(report["power_service"]["battery_shortfall_kw"], 0.0)
        self.assertFalse(report["essential_demands_met"])
        self.assertEqual(state["battery_soc_kwh"], 100.0)

    def test_process_recipes_are_read_from_scenario_data(self):
        scenario = json.loads(json.dumps(SCENARIO))
        scenario["processes"]["oxygen_isru"]["requested_cycles_per_period"] = 200.0
        state, report = advance_period(scenario, initial_state(scenario), 1)
        self.assertAlmostEqual(report["oxygen_output_kg"], 100.0)
        self.assertAlmostEqual(report["material_processes"]["oxygen_isru"]["requested_cycles"], 100.0)

    def test_input_state_is_not_mutated(self):
        state = initial_state(SCENARIO)
        before = dict(state)
        advance_period(SCENARIO, state, 1)
        self.assertEqual(state, before)

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

    def test_power_service_cannot_exceed_period_duration(self):
        scenario = json.loads(json.dumps(SCENARIO))
        scenario["scripted_events"][1]["duration_hours"] = 1000.0
        state = initial_state(scenario)
        state, _ = advance_period(scenario, state, 1)
        with self.assertRaises(DomainError):
            advance_period(scenario, state, 2)


if __name__ == "__main__":
    unittest.main()

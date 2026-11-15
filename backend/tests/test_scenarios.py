import json
import unittest
from pathlib import Path

from backend.app.scenarios import ScenarioValidationError, validate_scenario


ROOT = Path(__file__).resolve().parents[2]
with (ROOT / "data" / "scenarios" / "ares-01.json").open(encoding="utf-8") as source:
    SCENARIO = json.load(source)


class ScenarioValidationTests(unittest.TestCase):
    def test_reference_scenario_is_valid(self):
        validate_scenario(SCENARIO)

    def test_initial_stock_cannot_exceed_storage_capacity(self):
        scenario = json.loads(json.dumps(SCENARIO))
        scenario["stocks"]["oxygen_kg"] = 2600
        with self.assertRaises(ScenarioValidationError):
            validate_scenario(scenario)

    def test_duplicate_storage_assignment_is_rejected(self):
        scenario = json.loads(json.dumps(SCENARIO))
        scenario["resource_catalog"]["oxygen"]["storage_id"] = "water_tank"
        with self.assertRaises(ScenarioValidationError):
            validate_scenario(scenario)

    def test_unassigned_power_load_is_rejected(self):
        scenario = json.loads(json.dumps(SCENARIO))
        scenario["power"]["dispatch_groups"][0]["load_sources"].remove(
            "medical_and_communications"
        )
        with self.assertRaises(ScenarioValidationError):
            validate_scenario(scenario)

    def test_energy_process_must_have_a_dispatch_group(self):
        scenario = json.loads(json.dumps(SCENARIO))
        scenario["power"]["dispatch_groups"] = [
            group for group in scenario["power"]["dispatch_groups"]
            if group.get("process_id") != "oxygen_isru"
        ]
        with self.assertRaises(ScenarioValidationError):
            validate_scenario(scenario)

    def test_battery_efficiency_cannot_exceed_one(self):
        scenario = json.loads(json.dumps(SCENARIO))
        scenario["power"]["battery"]["charge_efficiency"] = 1.1
        with self.assertRaises(ScenarioValidationError):
            validate_scenario(scenario)


if __name__ == "__main__":
    unittest.main()

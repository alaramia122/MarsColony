import json
import unittest
from copy import deepcopy
from pathlib import Path

from backend.app.entity_catalog import load_entity_catalog, validate_entity
from backend.app.domain.engine import advance_period, initial_state


ROOT = Path(__file__).resolve().parents[2]
with (ROOT / "data" / "scenarios" / "ares-01.json").open(encoding="utf-8") as source:
    SCENARIO = json.load(source)


class EntityCatalogTests(unittest.TestCase):
    def test_every_builtin_definition_matches_scenario_resources(self):
        catalogue = load_entity_catalog("ares-01")
        for entity in catalogue["entities"]:
            with self.subTest(entity=entity["id"]):
                validate_entity(entity, SCENARIO["resource_catalog"], "ares-01")

    def test_catalogue_contains_buildable_assets_and_research_technologies(self):
        catalogue = load_entity_catalog("ares-01")
        types = {item["entity_type"] for item in catalogue["entities"]}
        self.assertIn("infrastructure", types)
        self.assertIn("vehicle", types)
        self.assertIn("technology", types)

    def test_unknown_top_level_definition_field_is_rejected(self):
        catalogue = load_entity_catalog("ares-01")
        entity = deepcopy(catalogue["entities"][0])
        entity["arbitrary_state_write"] = {"budget_credits": 0}
        with self.assertRaises(ValueError):
            validate_entity(entity, SCENARIO["resource_catalog"], "ares-01")

    def test_unsupported_technology_effect_is_rejected(self):
        catalogue = load_entity_catalog("ares-01")
        technology = deepcopy(next(
            item for item in catalogue["entities"]
            if item["entity_type"] == "technology"
        ))
        technology["effects"]["arbitrary_state_write"] = True
        with self.assertRaises(ValueError):
            validate_entity(technology, SCENARIO["resource_catalog"], "ares-01")


class InfrastructureEconomyTests(unittest.TestCase):
    def test_starter_facility_condition_scales_its_bound_process(self):
        scenario = deepcopy(SCENARIO)
        scenario["initial_infrastructure"] = [
            {
                "id": "ATM-1",
                "name": "Атмосферный модуль",
                "kind": "atmosphere_control",
                "domain_codes": ["life_support"],
                "properties": {"condition": 0.5, "status": "commissioned", "starter_asset": True, "process_bindings": ["oxygen_isru"]},
            },
            {
                "id": "WATER-1",
                "name": "Водный контур",
                "kind": "water_recycling",
                "domain_codes": ["life_support"],
                "properties": {"condition": 1.0, "status": "commissioned", "starter_asset": True, "process_bindings": ["ice_to_usable_water"]},
            },
            {
                "id": "GREEN-1",
                "name": "Теплица",
                "kind": "hydroponic_greenhouse",
                "domain_codes": ["agriculture"],
                "properties": {"condition": 1.0, "status": "commissioned", "starter_asset": True, "process_bindings": ["food_growing"]},
            },
        ]
        state = initial_state(scenario)
        updated, report = advance_period(scenario, state, 1)
        self.assertAlmostEqual(report["oxygen_output_kg"], 75.0, places=5)
        self.assertAlmostEqual(report["infrastructure_process_capacity"]["oxygen_isru"], 0.5)
        self.assertAlmostEqual(updated["infrastructure"][0]["condition"], 0.495)
        self.assertAlmostEqual(state["infrastructure"][0]["properties"]["condition"], 0.5)

    def test_research_technology_increases_matching_asset_process_output(self):
        scenario = deepcopy(SCENARIO)
        scenario["initial_infrastructure"] = [{
            "id": "GREEN-1",
            "name": "Теплица",
            "kind": "hydroponic_greenhouse",
            "domain_codes": ["agriculture", "life_support"],
            "properties": {"condition": 1.0, "status": "commissioned", "process_bindings": ["food_growing"]},
        }]
        state = initial_state(scenario)
        state["technologies"] = [{
            "id": "advanced_bioreactor",
            "name": "Высокоэффективный биореактор",
            "domain_codes": ["agriculture", "science", "life_support"],
            "effects": {"resource_output_multiplier": {"food_solids": 1.15}},
        }]
        _, report = advance_period(scenario, state, 1)
        self.assertAlmostEqual(report["food_output_kg"], 51.75, places=5)

    def test_drilling_rig_produces_ice_and_consumes_power_and_budget(self):
        scenario = deepcopy(SCENARIO)
        scenario["initial_infrastructure"] = [
            {
                "id": "DRILL-1",
                "name": "Буровая установка",
                "kind": "drilling_rig",
                "domain_codes": ["geology"],
                "properties": {
                    "condition": 1.0,
                    "status": "commissioned",
                    "economic_effects": {
                        "power_load_kw": 1.0,
                        "resource_outputs_per_period": {"raw_ice": 120.0},
                        "operating_cost_credits_per_period": 1.0,
                    },
                },
            }
        ]
        state = initial_state(scenario)
        updated, report = advance_period(scenario, state, 1)
        mining = next(item for item in report["infrastructure_operations"] if item["asset_id"] == "DRILL-1")
        self.assertAlmostEqual(mining["outputs_produced"]["raw_ice"], 120.0, places=5)
        self.assertGreater(mining["energy_used_kwh"], 0.0)
        self.assertGreater(mining["operating_cost_paid"], 0.0)
        self.assertAlmostEqual(updated["raw_ice_kg"], state["raw_ice_kg"] + 120.0, places=5)


if __name__ == "__main__":
    unittest.main()

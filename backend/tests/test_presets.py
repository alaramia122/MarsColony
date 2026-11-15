import unittest

from backend.app.domain.engine import initial_state
from backend.app.presets import apply_starter_preset, load_starter_presets
from backend.app.entity_catalog import load_entity_catalog
from backend.app.scenarios import load_scenario


class StarterInfrastructurePresetTests(unittest.TestCase):
    def test_each_preset_provides_a_kit_for_all_responsibility_domains(self):
        catalogue = load_starter_presets("ares-01")
        self.assertEqual(len(catalogue["presets"]), 4)
        expected_domains = set(catalogue["domain_names"])
        for preset in catalogue["presets"]:
            self.assertEqual(set(preset["domain_packs"]), expected_domains)
            self.assertTrue(all(preset["domain_packs"][domain] for domain in expected_domains))

    def test_presets_create_individual_objects_for_all_domains(self):
        expected_domains = set(load_starter_presets("ares-01")["domain_names"])
        for preset_id in ("balanced", "science", "industrial", "autonomous"):
            scenario = apply_starter_preset(load_scenario("ares-01"), preset_id)
            state = initial_state(scenario)
            assets = state["infrastructure"]
            self.assertGreater(len(assets), 0)
            self.assertEqual(
                {domain for asset in assets for domain in asset["domain_codes"]},
                expected_domains,
            )
            self.assertTrue(all(asset["properties"]["starter_asset"] for asset in assets))
            self.assertTrue(all(asset["properties"]["simulation_effects"] == "modeled" for asset in assets))

    def test_every_starter_kind_has_an_economic_or_process_contract(self):
        starter = load_starter_presets("ares-01")
        entity_catalogue = load_entity_catalog("ares-01")
        starter_kinds = {
            asset["kind"]
            for preset in starter["presets"]
            for assets in preset["domain_packs"].values()
            for asset in assets
        }
        self.assertEqual(starter_kinds, set(entity_catalogue["starter_kind_overrides"]))

    def test_catalogue_contracts_are_attached_to_matching_starter_assets(self):
        scenario = apply_starter_preset(load_scenario("ares-01"), "balanced")
        assets = scenario["initial_infrastructure"]
        drilling_rig = next(asset for asset in assets if asset["kind"] == "drilling_rig")
        atmosphere = next(asset for asset in assets if asset["kind"] == "atmosphere_control")
        self.assertEqual(drilling_rig["properties"]["economic_effects"]["resource_outputs_per_period"]["raw_ice"], 120)
        self.assertEqual(atmosphere["properties"]["process_bindings"], ["oxygen_isru"])

    def test_presets_have_distinct_infrastructure_compositions(self):
        catalogue = load_starter_presets("ares-01")
        compositions = {
            preset["id"]: sum(
                int(asset.get("quantity", 1))
                for assets in preset["domain_packs"].values()
                for asset in assets
            )
            for preset in catalogue["presets"]
        }
        self.assertEqual(len(set(compositions.values())), len(compositions))


if __name__ == "__main__":
    unittest.main()

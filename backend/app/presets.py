"""Starter infrastructure presets for MarsColony sessions."""
import json
import re
from pathlib import Path
from typing import Any


MODULE_PATH = Path(__file__).resolve()
DATA_ROOTS = (MODULE_PATH.parents[2], MODULE_PATH.parents[1])

REQUIRED_DOMAINS = {
    "geology", "industry", "energy", "life_support", "construction",
    "agriculture", "medicine", "social", "education", "science",
    "planetary", "transport", "administration", "safety",
    "communications", "external_relations",
}

DOMAIN_POSITIONS = {
    "geology": (14, 18), "industry": (38, 18), "energy": (62, 18), "life_support": (86, 18),
    "construction": (14, 40), "agriculture": (38, 40), "medicine": (62, 40), "social": (86, 40),
    "education": (14, 62), "science": (38, 62), "planetary": (62, 62), "transport": (86, 62),
    "administration": (14, 84), "safety": (38, 84), "communications": (62, 84), "external_relations": (86, 84),
}


def load_starter_presets(scenario_id: str) -> dict[str, Any]:
    """Load and validate the infrastructure preset catalogue for a scenario."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", scenario_id):
        raise FileNotFoundError("Scenario identifier has an invalid format.")
    for root in DATA_ROOTS:
        path = root / "data" / "starter_presets" / f"{scenario_id}.json"
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8") as source:
            catalogue = json.load(source)
        if catalogue.get("scenario_id") != scenario_id:
            raise ValueError("Starter preset catalogue scenario_id does not match its filename.")
        preset_ids = set()
        for preset in catalogue.get("presets", []):
            preset_id = preset.get("id")
            if not preset_id or preset_id in preset_ids:
                raise ValueError("Starter preset identifiers must be unique and non-empty.")
            preset_ids.add(preset_id)
            packs = preset.get("domain_packs", {})
            if set(packs) != REQUIRED_DOMAINS:
                raise ValueError(f"Preset '{preset_id}' must provide a starter kit for all 16 responsibility domains.")
            for domain, assets in packs.items():
                if not isinstance(assets, list) or not assets:
                    raise ValueError(f"Preset '{preset_id}' has an empty kit for '{domain}'.")
                for asset in assets:
                    if not asset.get("kind") or not asset.get("name") or not asset.get("description"):
                        raise ValueError(f"Preset '{preset_id}' contains an incomplete asset in '{domain}'.")
                    quantity = asset.get("quantity", 1)
                    if not isinstance(quantity, int) or isinstance(quantity, bool) or not 1 <= quantity <= 50:
                        raise ValueError(f"Preset '{preset_id}' has an invalid quantity in '{domain}'.")
        if not preset_ids:
            raise ValueError("Starter preset catalogue is empty.")
        return catalogue
    raise FileNotFoundError(f"No starter preset catalogue for scenario '{scenario_id}'.")


def get_starter_preset(catalogue: dict[str, Any], preset_id: str) -> dict[str, Any]:
    for preset in catalogue["presets"]:
        if preset["id"] == preset_id:
            return preset
    raise KeyError(preset_id)


def build_initial_infrastructure(preset: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand domain kits into individual commissioned objects in the session state."""
    result = []
    for domain, assets in preset["domain_packs"].items():
        base_x, base_y = DOMAIN_POSITIONS[domain]
        domain_asset_index = 0
        for asset in assets:
            for index in range(asset.get("quantity", 1)):
                object_index = len(result) + 1
                offset = domain_asset_index * 5
                domain_asset_index += 1
                result.append({
                    "id": f"START-{preset['id'].upper()}-{object_index:03d}",
                    "name": asset["name"] if asset.get("quantity", 1) == 1 else f"{asset['name']} №{index + 1}",
                    "kind": asset["kind"],
                    "domain_codes": [domain],
                    "properties": {
                        "condition": 1.0,
                        "status": "commissioned",
                        "simulation_effects": "not_modeled",
                        "description": asset["description"],
                        "starter_asset": True,
                        "starter_preset_id": preset["id"],
                        "x": min(98, base_x + offset),
                        "y": min(98, base_y + offset),
                    },
                })
    return result


def apply_starter_preset(scenario: dict[str, Any], preset_id: str) -> dict[str, Any]:
    """Attach a selected preset's assets and metadata to a fresh scenario snapshot."""
    catalogue = load_starter_presets(scenario["scenario_id"])
    preset = get_starter_preset(catalogue, preset_id)
    scenario["initial_infrastructure"] = build_initial_infrastructure(preset)
    scenario["starter_preset"] = {
        "id": preset["id"],
        "name": preset["name"],
        "tagline": preset["tagline"],
        "description": preset["description"],
        "strategy": preset["strategy"],
        "tradeoffs": preset["tradeoffs"],
    }
    return scenario


def serialize_preset_catalogue(catalogue: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the selectable presets and their domain kits to the session creator."""
    return catalogue["presets"]

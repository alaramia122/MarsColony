"""Load versioned scenario data from the repository data directory."""
import json
from pathlib import Path
from typing import Any

SCENARIO_DIR = Path(__file__).resolve().parents[3] / "data" / "scenarios"


def load_scenario(scenario_id: str) -> dict[str, Any]:
    if scenario_id != "ares-01":
        raise FileNotFoundError(f"Unknown scenario: {scenario_id}")
    path = SCENARIO_DIR / "ares-01.json"
    with path.open("r", encoding="utf-8") as scenario_file:
        return json.load(scenario_file)

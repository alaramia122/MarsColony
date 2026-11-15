"""Load versioned scenario data from the repository or container image."""
import json
from pathlib import Path
from typing import Any

MODULE_PATH = Path(__file__).resolve()
DATA_ROOTS = (MODULE_PATH.parents[2], MODULE_PATH.parents[1])


def load_scenario(scenario_id: str) -> dict[str, Any]:
    if scenario_id != "ares-01":
        raise FileNotFoundError(f"Unknown scenario: {scenario_id}")
    for root in DATA_ROOTS:
        path = root / "data" / "scenarios" / "ares-01.json"
        if path.is_file():
            with path.open("r", encoding="utf-8") as scenario_file:
                return json.load(scenario_file)
    raise FileNotFoundError("Scenario data directory is missing.")

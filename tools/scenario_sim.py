"""Reference calculator for the Ares-1 Stage 0 scenario.

This is a deterministic design/validation artifact, not the future game backend.
Only the Python standard library is required.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_scenario(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as scenario_file:
        return json.load(scenario_file)


def simulate_period(
    scenario: dict[str, Any],
    *,
    oxygen_output_multiplier: float = 1.0,
    food_output_multiplier: float = 1.0,
    water_output_multiplier: float = 1.0,
) -> dict[str, float]:
    """Calculate one period and return flows and ending stocks."""
    period_sols = scenario["time"]["period_sols"]
    crew = scenario["population"]["total"]
    hours = period_sols * scenario["time"]["hours_per_sol"]
    crew_days = crew * hours / 24.0
    stocks = scenario["stocks"]
    life = scenario["life_support"]
    power = scenario["power"]

    oxygen_demand = life["oxygen_demand_kg_per_person_day"] * crew_days
    oxygen_output = life["oxygen_isru_output_kg_per_period"] * oxygen_output_multiplier
    oxygen_available = stocks["oxygen_kg"] + oxygen_output
    oxygen_unmet = max(0.0, oxygen_demand - oxygen_available)
    oxygen_end = max(0.0, oxygen_available - oxygen_demand)

    food_demand = life["food_ration_demand_kg_per_person_day"] * crew_days
    food_output = life["food_growing_output_kg_per_period"] * food_output_multiplier
    food_available = stocks["food_rations_kg"] + food_output
    food_unmet = max(0.0, food_demand - food_available)
    food_end = max(0.0, food_available - food_demand)

    gross_water = life["gross_water_demand_kg_per_person_day"] * crew_days
    water_loss = gross_water * (1.0 - life["water_recovery_fraction"])
    greenhouse_water = life["greenhouse_net_water_makeup_kg_per_period"]
    water_demand = water_loss + greenhouse_water

    requested_water_output = life["water_product_output_kg_per_period"] * water_output_multiplier
    requested_raw_ice = requested_water_output / life["ice_to_usable_water_yield"]
    raw_ice_available = stocks["raw_ice_kg"]
    raw_ice_shortfall = max(0.0, requested_raw_ice - raw_ice_available)
    raw_ice_demand = min(requested_raw_ice, raw_ice_available)
    water_output = raw_ice_demand * life["ice_to_usable_water_yield"]
    water_available = stocks["usable_water_kg"] + water_output
    water_unmet = max(0.0, water_demand - water_available)
    water_end = max(0.0, water_available - water_demand)
    raw_ice_end = max(0.0, raw_ice_available - raw_ice_demand)

    load_kw = sum(power["loads_kw"].values())
    generation_kw = (
        power["module_count"]
        * power["module_nominal_kw"]
        * power["availability_factor"]
    )
    generated_kwh = generation_kw * hours
    consumed_kwh = load_kw * hours
    energy_margin_kwh = generated_kwh - consumed_kwh
    peak_deficit_kw = max(0.0, power["peak_load_kw"] - generation_kw)
    peak_support_hours = (
        power["battery_capacity_kwh"] / peak_deficit_kw
        if peak_deficit_kw > 0.0
        else float("inf")
    )

    return {
        "period_hours": hours,
        "oxygen_demand_kg": oxygen_demand,
        "oxygen_output_kg": oxygen_output,
        "oxygen_unmet_kg": oxygen_unmet,
        "oxygen_end_kg": oxygen_end,
        "food_demand_kg": food_demand,
        "food_output_kg": food_output,
        "food_unmet_kg": food_unmet,
        "food_end_kg": food_end,
        "gross_water_kg": gross_water,
        "water_loss_kg": water_loss,
        "water_demand_kg": water_demand,
        "water_output_kg": water_output,
        "water_unmet_kg": water_unmet,
        "water_end_kg": water_end,
        "raw_ice_demand_kg": raw_ice_demand,
        "raw_ice_shortfall_kg": raw_ice_shortfall,
        "raw_ice_end_kg": raw_ice_end,
        "average_load_kw": load_kw,
        "available_generation_kw": generation_kw,
        "generated_kwh": generated_kwh,
        "consumed_kwh": consumed_kwh,
        "energy_margin_kwh": energy_margin_kwh,
        "peak_deficit_kw": peak_deficit_kw,
        "peak_support_hours": peak_support_hours,
    }


def simulate_campaign(scenario: dict[str, Any], *, apply_scripted_events: bool = True) -> dict[str, Any]:
    """Run the campaign, optionally applying scripted water, crop and oxygen events."""
    current = dict(scenario["stocks"])
    period_results: list[dict[str, float]] = []
    for period_number in range(1, scenario["time"]["period_count"] + 1):
        period_scenario = dict(scenario)
        period_scenario["stocks"] = current
        event = next(
            (item for item in scenario.get("scripted_events", []) if item.get("period") == period_number),
            {},
        ) if apply_scripted_events else {}
        result = simulate_period(
            period_scenario,
            oxygen_output_multiplier=event.get("oxygen_output_multiplier", 1.0),
            food_output_multiplier=event.get("food_output_multiplier", 1.0),
            water_output_multiplier=event.get("water_output_multiplier", 1.0),
        )
        period_results.append(result)
        current["oxygen_kg"] = result["oxygen_end_kg"]
        current["food_rations_kg"] = result["food_end_kg"]
        current["usable_water_kg"] = result["water_end_kg"]
        current["raw_ice_kg"] = result["raw_ice_end_kg"]

    finance = scenario["finance"]
    ending_budget = (
        finance["starting_credits"]
        - finance["operating_cost_per_period"] * scenario["time"]["period_count"]
    )
    return {
        "periods": period_results,
        "ending_stocks": current,
        "ending_budget_credits": ending_budget,
        "all_periods_without_unmet_demand": all(
            period["oxygen_unmet_kg"] == 0.0
            and period["food_unmet_kg"] == 0.0
            and period["water_unmet_kg"] == 0.0
            and period["raw_ice_shortfall_kg"] == 0.0
            for period in period_results
        ),
    }


def simulate_reactor_service(scenario: dict[str, Any], duration_hours: float = 48.0) -> dict[str, float]:
    """Check a one-module service window against critical loads and battery reserve."""
    power = scenario["power"]
    available_kw = power["module_nominal_kw"] * power["availability_factor"]
    critical_kw = power["critical_load_kw"]
    critical_peak_kw = power["critical_peak_kw"]
    steady_margin_kw = available_kw - critical_kw
    battery_required_kwh = max(0.0, critical_peak_kw - available_kw) * duration_hours
    return {
        "available_kw": available_kw,
        "critical_load_kw": critical_kw,
        "steady_margin_kw": steady_margin_kw,
        "battery_required_kwh": battery_required_kwh,
        "battery_capacity_kwh": power["battery_capacity_kwh"],
        "battery_margin_kwh": power["battery_capacity_kwh"] - battery_required_kwh,
    }


def main() -> None:
    scenario_path = Path(__file__).resolve().parents[1] / "data" / "scenarios" / "ares-01.json"
    scenario = load_scenario(scenario_path)
    result = simulate_campaign(scenario)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

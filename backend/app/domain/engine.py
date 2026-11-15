"""Deterministic Ares-1 period calculation.

The domain engine has no database, HTTP, or LLM dependencies. It receives
scenario data and a plain state snapshot and returns a new state plus report.
"""
from __future__ import annotations

from typing import Any

from .energy import PowerLoad, dispatch_power
from .resources import ResourceStock, execute_process


class DomainError(ValueError):
    """Raised when a requested game transition violates domain rules."""


def initial_state(scenario: dict[str, Any]) -> dict[str, Any]:
    """Create the first mutable game-state snapshot from a scenario."""
    stocks = scenario["stocks"]
    return {
        "period": 0,
        "oxygen_kg": float(stocks["oxygen_kg"]),
        "food_solids_kg": float(stocks["food_solids_kg"]),
        "usable_water_kg": float(stocks["usable_water_kg"]),
        "raw_ice_kg": float(stocks["raw_ice_kg"]),
        "budget_credits": float(scenario["finance"]["starting_credits"]),
    }


def advance_period(
    scenario: dict[str, Any],
    state: dict[str, Any],
    period_number: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Calculate one period and return (new_state, report).

    Material production is constrained by input stocks and storage capacities.
    Demand shortfalls are explicit; no material stock or budget goes negative.
    """
    period_count = int(scenario["time"]["period_count"])
    expected_period = int(state["period"]) + 1
    if period_number != expected_period:
        raise DomainError(
            f"Expected period {expected_period}, received {period_number}."
        )
    if period_number > period_count:
        raise DomainError("The scenario has already reached its final period.")

    period_hours = (
        float(scenario["time"]["period_sols"])
        * float(scenario["time"]["hours_per_sol"])
    )
    crew_days = (
        int(scenario["population"]["total"]) * period_hours / 24.0
    )
    life = scenario["life_support"]
    power = scenario["power"]
    scenario_stocks = scenario["stocks"]
    event = next(
        (
            item
            for item in scenario.get("scripted_events", [])
            if int(item.get("period", -1)) == period_number
        ),
        {},
    )

    oxygen_demand = (
        float(life["oxygen_demand_kg_per_person_day"]) * crew_days
    )
    requested_oxygen = (
        float(life["oxygen_isru_output_kg_per_period"])
        * float(event.get("oxygen_output_multiplier", 1.0))
    )
    oxygen_stocks, oxygen_process = execute_process(
        {"oxygen": ResourceStock(
            float(state["oxygen_kg"]),
            float(scenario_stocks["oxygen_capacity_kg"]),
        )},
        {},
        {"oxygen": 1.0},
        requested_oxygen,
        energy_kwh_per_cycle=float(life["oxygen_isru_specific_energy_kwh_per_kg"]),
    )
    oxygen_output = oxygen_process["outputs_produced"].get("oxygen", 0.0)
    oxygen_available = oxygen_stocks["oxygen"].quantity
    oxygen_unmet = max(0.0, oxygen_demand - oxygen_available)
    oxygen_end = max(0.0, oxygen_available - oxygen_demand)

    food_demand = (
        float(life["food_solids_demand_kg_per_person_day"]) * crew_days
    )
    requested_food = (
        float(life["food_growing_output_kg_per_period"])
        * float(event.get("food_output_multiplier", 1.0))
    )
    food_stocks, food_process = execute_process(
        {"food": ResourceStock(
            float(state["food_solids_kg"]),
            float(scenario_stocks["food_storage_capacity_kg"]),
        )},
        {},
        {"food": 1.0},
        requested_food,
    )
    food_output = food_process["outputs_produced"].get("food", 0.0)
    food_available = food_stocks["food"].quantity
    food_unmet = max(0.0, food_demand - food_available)
    food_end = max(0.0, food_available - food_demand)

    gross_water = (
        float(life["gross_water_demand_kg_per_person_day"]) * crew_days
    )
    water_loss = gross_water * (
        1.0 - float(life["water_recovery_fraction"])
    )
    water_demand = (
        water_loss
        + float(life["greenhouse_net_water_makeup_kg_per_period"])
    )
    requested_water = (
        float(life["water_product_output_kg_per_period"])
        * float(event.get("water_output_multiplier", 1.0))
    )
    ice_yield = float(life["ice_to_usable_water_yield"])
    water_stocks, water_process = execute_process(
        {
            "raw_ice": ResourceStock(float(state["raw_ice_kg"])),
            "usable_water": ResourceStock(
                float(state["usable_water_kg"]),
                float(scenario_stocks["usable_water_capacity_kg"]),
            ),
        },
        {"raw_ice": 1.0 / ice_yield},
        {"usable_water": 1.0},
        requested_water,
        energy_kwh_per_cycle=float(
            life["water_system_specific_energy_kwh_per_kg_product"]
        ),
    )
    water_output = water_process["outputs_produced"].get("usable_water", 0.0)
    ice_used = water_process["inputs_consumed"].get("raw_ice", 0.0)
    water_available = water_stocks["usable_water"].quantity
    water_unmet = max(0.0, water_demand - water_available)
    water_end = max(0.0, water_available - water_demand)
    ice_end = water_stocks["raw_ice"].quantity
    requested_ice = requested_water / ice_yield
    ice_shortfall = (
        max(0.0, requested_ice - ice_used)
        if "input:raw_ice" in water_process["limiting_constraints"]
        else 0.0
    )

    load_kw = sum(float(value) for value in power["loads_kw"].values())
    generation_kw = (
        int(power["module_count"])
        * float(power["module_nominal_kw"])
        * float(power["availability_factor"])
    )
    generated_kwh = generation_kw * period_hours
    consumed_kwh = load_kw * period_hours
    energy_margin_kwh = generated_kwh - consumed_kwh

    service_duration_hours = float(event.get("duration_hours", 0.0))
    modules_online = int(event.get("modules_online", power["module_count"]))
    service_available_kw = (
        modules_online
        * float(power["module_nominal_kw"])
        * float(power["availability_factor"])
    )
    critical_load_kw = float(power["critical_load_kw"])
    critical_peak_kw = float(power["critical_peak_kw"])
    service_battery_required_kwh = (
        max(0.0, critical_peak_kw - service_available_kw)
        * service_duration_hours
    )
    service_power_ok = (
        service_available_kw >= critical_load_kw
        and service_battery_required_kwh <= float(power["battery_capacity_kwh"])
    )
    power_dispatch = None
    if service_duration_hours > 0:
        flexible_load_kw = max(0.0, load_kw - critical_load_kw)
        power_dispatch = dispatch_power(
            service_available_kw,
            [
                PowerLoad("critical_systems", critical_load_kw, priority=0),
                PowerLoad("flexible_systems", flexible_load_kw, priority=1),
            ],
            duration_hours=service_duration_hours,
        )

    finance = scenario["finance"]
    operating_cost = float(finance["operating_cost_per_period"])
    budget_available = float(state["budget_credits"])
    budget_unmet = max(0.0, operating_cost - budget_available)
    budget_end = max(0.0, budget_available - operating_cost)

    new_state = {
        "period": period_number,
        "oxygen_kg": oxygen_end,
        "food_solids_kg": food_end,
        "usable_water_kg": water_end,
        "raw_ice_kg": ice_end,
        "budget_credits": budget_end,
    }
    report = {
        "period": period_number,
        "period_hours": period_hours,
        "crew_days": crew_days,
        "oxygen_demand_kg": oxygen_demand,
        "oxygen_output_kg": oxygen_output,
        "oxygen_unmet_kg": oxygen_unmet,
        "food_demand_kg": food_demand,
        "food_output_kg": food_output,
        "food_unmet_kg": food_unmet,
        "gross_water_kg": gross_water,
        "water_loss_kg": water_loss,
        "water_demand_kg": water_demand,
        "water_output_kg": water_output,
        "water_unmet_kg": water_unmet,
        "raw_ice_used_kg": ice_used,
        "raw_ice_shortfall_kg": ice_shortfall,
        "material_processes": {
            "oxygen_isru": oxygen_process,
            "food_growing": food_process,
            "ice_to_usable_water": water_process,
        },
        "available_generation_kw": generation_kw,
        "average_load_kw": load_kw,
        "generated_kwh": generated_kwh,
        "consumed_kwh": consumed_kwh,
        "energy_margin_kwh": energy_margin_kwh,
        "power_service": {
            "modules_online": modules_online,
            "available_kw": service_available_kw,
            "critical_load_kw": critical_load_kw,
            "duration_hours": service_duration_hours,
            "battery_required_kwh": service_battery_required_kwh,
            "battery_capacity_kwh": float(power["battery_capacity_kwh"]),
            "within_reserve": service_power_ok,
        },
        "power_dispatch": power_dispatch,
        "budget_cost_credits": operating_cost,
        "budget_unmet_credits": budget_unmet,
        "event_id": event.get("id"),
        "demand_fully_met": (
            oxygen_unmet == 0.0
            and food_unmet == 0.0
            and water_unmet == 0.0
            and ice_shortfall == 0.0
        ),
    }
    return new_state, report

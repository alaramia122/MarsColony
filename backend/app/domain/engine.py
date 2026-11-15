"""Deterministic scenario-driven period calculation for the colony economy."""
from __future__ import annotations

from typing import Any

from .energy import BatterySpec, PowerLoad, dispatch_power, operate_battery
from .resources import ResourceStock, execute_process


class DomainError(ValueError):
    """Raised when a requested game transition violates domain rules."""


def _round_resource(value: float, scenario: dict[str, Any], resource_id: str) -> float:
    digits = int(scenario["resource_catalog"][resource_id]["precision_digits"])
    return round(value, digits)


def _storage_capacity(scenario: dict[str, Any], resource_id: str) -> float | None:
    storage_id = scenario["resource_catalog"][resource_id]["storage_id"]
    storage = next(
        item for item in scenario["storage_locations"] if item["id"] == storage_id
    )
    capacity = storage["capacity"]
    return None if capacity is None else float(capacity)


def _battery_spec(power: dict[str, Any]) -> BatterySpec:
    battery = power["battery"]
    return BatterySpec(
        capacity_kwh=float(battery["capacity_kwh"]),
        minimum_charge_kwh=float(battery["minimum_charge_kwh"]),
        maximum_charge_kw=float(battery["maximum_charge_kw"]),
        maximum_discharge_kw=float(battery["maximum_discharge_kw"]),
        charge_efficiency=float(battery["charge_efficiency"]),
        discharge_efficiency=float(battery["discharge_efficiency"]),
    )


def initial_state(scenario: dict[str, Any]) -> dict[str, Any]:
    """Create the first game-state snapshot from a validated scenario."""
    stocks = scenario["stocks"]
    state = {
        "period": 0,
        "infrastructure": [],
        "production_bonuses_per_period": {},
        "power_generation_kw_delta": 0.0,
    }
    for resource in scenario["resource_catalog"].values():
        state[resource["state_key"]] = float(stocks[resource["state_key"]])
    state["budget_credits"] = float(scenario["finance"]["starting_credits"])
    state["battery_soc_kwh"] = float(scenario["power"]["battery"]["initial_charge_kwh"])
    return state


def _execute_scenario_process(
    scenario: dict[str, Any],
    state: dict[str, Any],
    process_id: str,
    requested_cycles: float,
    energy_available_kwh: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    process = scenario["processes"][process_id]
    stocks: dict[str, ResourceStock] = {}
    for resource_id in set(process["inputs_per_cycle"]) | set(process["outputs_per_cycle"]):
        resource = scenario["resource_catalog"][resource_id]
        stocks[resource_id] = ResourceStock(
            quantity=float(state[resource["state_key"]]),
            capacity=_storage_capacity(scenario, resource_id),
        )
    updated_stocks, report = execute_process(
        stocks,
        process["inputs_per_cycle"],
        process["outputs_per_cycle"],
        requested_cycles,
        energy_kwh_per_cycle=float(process["energy_kwh_per_cycle"]),
        energy_available_kwh=energy_available_kwh,
    )
    for resource_id, stock in updated_stocks.items():
        state_key = scenario["resource_catalog"][resource_id]["state_key"]
        state[state_key] = _round_resource(stock.quantity, scenario, resource_id)
    return state, report


def _power_loads(
    scenario: dict[str, Any],
    average_load_kw: float,
    process_multipliers: dict[str, float],
    additional_loads: list[dict[str, Any]] | None = None,
) -> list[PowerLoad]:
    power = scenario["power"]
    loads = []
    for group in power["dispatch_groups"]:
        requested_kw = 0.0
        for source in group["load_sources"]:
            if source == "critical_load_kw":
                requested_kw += float(power["critical_load_kw"])
            elif source == "remaining_average_load":
                requested_kw += max(
                    0.0, average_load_kw - float(power["critical_load_kw"])
                )
            else:
                requested_kw += float(power["loads_kw"][source])
        process_id = group.get("process_id")
        if process_id:
            requested_kw *= process_multipliers[process_id]
        loads.append(PowerLoad(
            load_id=group["id"],
            requested_kw=requested_kw,
            priority=int(group["priority"]),
            minimum_kw=float(group["minimum_kw"]),
        ))
    for item in additional_loads or []:
        loads.append(PowerLoad(
            load_id=str(item["load_id"]),
            requested_kw=float(item["requested_kw"]),
            priority=int(item.get("priority", 4)),
            minimum_kw=float(item.get("minimum_kw", item["requested_kw"])),
        ))
    return loads


def advance_period(
    scenario: dict[str, Any],
    state: dict[str, Any],
    period_number: int,
    additional_loads: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Calculate one period and return a new state and a detailed report."""
    state = dict(state)
    battery_soc_start = float(
        state.get("battery_soc_kwh", scenario["power"]["battery"]["initial_charge_kwh"])
    )
    state["battery_soc_kwh"] = battery_soc_start
    period_count = int(scenario["time"]["period_count"])
    expected_period = int(state["period"]) + 1
    if period_number != expected_period:
        raise DomainError(f"Expected period {expected_period}, received {period_number}.")
    if period_number > period_count:
        raise DomainError("The scenario has already reached its final period.")

    period_hours = (
        float(scenario["time"]["period_sols"])
        * float(scenario["time"]["hours_per_sol"])
    )
    crew_days = int(scenario["population"]["total"]) * period_hours / 24.0
    life = scenario["life_support"]
    power = scenario["power"]
    event = next(
        (item for item in scenario.get("scripted_events", [])
         if int(item.get("period", -1)) == period_number),
        {},
    )

    event_multipliers = {}
    process_reports = {}
    for process_id, process in scenario["processes"].items():
        multiplier_key = process.get("event_multiplier_key")
        multiplier = float(event.get(multiplier_key, 1.0)) if multiplier_key else 1.0
        if multiplier < 0:
            raise DomainError(f"Event multiplier for '{process_id}' cannot be negative.")
        event_multipliers[process_id] = (
            float(process["requested_cycles_per_period"]) * multiplier
        )

    average_load_kw = sum(float(value) for value in power["loads_kw"].values())
    generation_kw = (
        int(power["module_count"])
        * float(power["module_nominal_kw"])
        * float(power["availability_factor"])
        + float(state.get("power_generation_kw_delta", 0.0))
    )
    service_duration_hours = float(event.get("duration_hours", 0.0))
    if service_duration_hours < 0 or service_duration_hours > period_hours:
        raise DomainError("Power-service duration must fit inside the current period.")
    modules_online = int(event.get("modules_online", power["module_count"]))
    if modules_online < 0 or modules_online > int(power["module_count"]):
        raise DomainError("Online power-module count is outside the installed fleet.")
    service_generation_kw = (
        modules_online
        * float(power["module_nominal_kw"])
        * float(power["availability_factor"])
        + float(state.get("power_generation_kw_delta", 0.0))
    )
    normal_duration_hours = period_hours - service_duration_hours
    battery_spec = _battery_spec(power)
    battery_soc = float(state["battery_soc_kwh"])
    interval_reports = []
    process_energy_budgets = {
        process_id: 0.0 for process_id in scenario["processes"]
    }
    total_generated_kwh = 0.0
    total_allocated_kwh = 0.0
    total_battery_discharge_kwh = 0.0
    total_battery_charge_input_kwh = 0.0
    total_battery_loss_kwh = 0.0
    total_shed_kwh = 0.0

    intervals = [
        ("normal", normal_duration_hours, generation_kw, float(power["critical_load_kw"])),
        ("maintenance", service_duration_hours, service_generation_kw,
         float(power["critical_peak_kw"]) if service_duration_hours > 0
         else float(power["critical_load_kw"])),
    ]
    for interval_id, duration, available_generation, critical_target in intervals:
        if duration <= 0:
            continue
        required_battery_kw = max(0.0, critical_target - available_generation)
        battery_report = operate_battery(
            battery_soc,
            battery_spec,
            duration,
            requested_discharge_kw=required_battery_kw,
        )
        battery_soc = battery_report["state_of_charge_end_kwh"]
        supplied_kw = available_generation + battery_report["discharge_kw"]
        dispatch = dispatch_power(
            supplied_kw,
            _power_loads(scenario, average_load_kw, {
                process_id: (
                    event_multipliers[process_id]
                    / float(scenario["processes"][process_id]["requested_cycles_per_period"])
                    if float(scenario["processes"][process_id]["requested_cycles_per_period"]) > 0
                    else 0.0
                )
                for process_id in scenario["processes"]
            }, additional_loads),
            duration_hours=duration,
        )
        battery_charge_report = None
        if battery_report["discharge_kw"] == 0.0 and dispatch["unallocated_kw"] > 0:
            battery_charge_report = operate_battery(
                battery_soc,
                battery_spec,
                duration,
                available_charge_kw=dispatch["unallocated_kw"],
            )
            battery_soc = battery_charge_report["state_of_charge_end_kwh"]
        for allocation in dispatch["loads"]:
            group = next(
                item for item in power["dispatch_groups"]
                if item["id"] == allocation["load_id"]
            )
            process_id = group.get("process_id")
            if process_id:
                process_energy_budgets[process_id] += (
                    allocation["allocated_kw"] * duration
                )
        generated = available_generation * duration
        consumed = dispatch["allocated_kwh"]
        discharge = battery_report["discharge_output_kwh"]
        charge_input = (
            battery_charge_report["charge_input_kwh"]
            if battery_charge_report else 0.0
        )
        battery_loss = battery_report["energy_loss_kwh"] + (
            battery_charge_report["energy_loss_kwh"]
            if battery_charge_report else 0.0
        )
        total_generated_kwh += generated
        total_allocated_kwh += consumed
        total_battery_discharge_kwh += discharge
        total_battery_charge_input_kwh += charge_input
        total_battery_loss_kwh += battery_loss
        total_shed_kwh += dispatch["shed_kwh"]
        interval_reports.append({
            "interval": interval_id,
            "duration_hours": duration,
            "generation_kw": available_generation,
            "battery_discharge": battery_report,
            "battery_charge": battery_charge_report,
            "dispatch": dispatch,
        })

    for process_id, requested_cycles in event_multipliers.items():
        process = scenario["processes"][process_id]
        energy_per_cycle = float(process["energy_kwh_per_cycle"])
        energy_budget = process_energy_budgets[process_id]
        if energy_per_cycle == 0:
            energy_budget = 0.0
        state, process_report = _execute_scenario_process(
            scenario, state, process_id, requested_cycles, energy_budget
        )
        process_reports[process_id] = process_report

    production_bonus_report = {}
    for resource_id, quantity in state.get("production_bonuses_per_period", {}).items():
        if resource_id not in scenario["resource_catalog"]:
            raise DomainError(f"Unknown production bonus resource '{resource_id}'.")
        resource = scenario["resource_catalog"][resource_id]
        state_key = resource["state_key"]
        current = float(state[state_key])
        capacity = _storage_capacity(scenario, resource_id)
        requested_quantity = float(quantity)
        produced_quantity = requested_quantity
        if capacity is not None:
            produced_quantity = min(requested_quantity, max(0.0, capacity - current))
        state[state_key] = _round_resource(
            current + produced_quantity, scenario, resource_id
        )
        production_bonus_report[resource_id] = {
            "requested": requested_quantity,
            "produced": produced_quantity,
            "curtailed": requested_quantity - produced_quantity,
        }

    allocated_process_energy_kwh = sum(process_energy_budgets.values())
    actual_process_energy_kwh = sum(
        item["energy_used_kwh"] for item in process_reports.values()
    )
    non_process_consumption_kwh = total_allocated_kwh - allocated_process_energy_kwh
    total_consumed_kwh = non_process_consumption_kwh + actual_process_energy_kwh
    unused_allocated_process_energy_kwh = max(
        0.0, allocated_process_energy_kwh - actual_process_energy_kwh
    )

    oxygen_demand = float(life["oxygen_demand_kg_per_person_day"]) * crew_days
    food_demand = float(life["food_solids_demand_kg_per_person_day"]) * crew_days
    gross_water = float(life["gross_water_demand_kg_per_person_day"]) * crew_days
    water_loss = gross_water * (1.0 - float(life["water_recovery_fraction"]))
    water_demand = water_loss + float(life["greenhouse_net_water_makeup_kg_per_period"])

    oxygen_key = scenario["resource_catalog"]["oxygen"]["state_key"]
    food_key = scenario["resource_catalog"]["food_solids"]["state_key"]
    water_key = scenario["resource_catalog"]["usable_water"]["state_key"]
    ice_key = scenario["resource_catalog"]["raw_ice"]["state_key"]
    oxygen_output = process_reports["oxygen_isru"]["outputs_produced"]["oxygen"]
    food_output = process_reports["food_growing"]["outputs_produced"]["food_solids"]
    water_output = process_reports["ice_to_usable_water"]["outputs_produced"]["usable_water"]
    ice_used = process_reports["ice_to_usable_water"]["inputs_consumed"].get("raw_ice", 0.0)

    oxygen_available = float(state[oxygen_key])
    food_available = float(state[food_key])
    water_available = float(state[water_key])
    oxygen_unmet = max(0.0, oxygen_demand - oxygen_available)
    food_unmet = max(0.0, food_demand - food_available)
    water_unmet = max(0.0, water_demand - water_available)
    state[oxygen_key] = _round_resource(
        max(0.0, oxygen_available - oxygen_demand), scenario, "oxygen"
    )
    state[food_key] = _round_resource(
        max(0.0, food_available - food_demand), scenario, "food_solids"
    )
    state[water_key] = _round_resource(
        max(0.0, water_available - water_demand), scenario, "usable_water"
    )

    requested_ice = event_multipliers["ice_to_usable_water"] / float(
        life["ice_to_usable_water_yield"]
    )
    ice_shortfall = max(
        0.0, requested_ice - ice_used
    ) if "input:raw_ice" in process_reports["ice_to_usable_water"]["limiting_constraints"] else 0.0

    finance = scenario["finance"]
    operating_cost = float(finance["operating_cost_per_period"])
    budget_available = float(state["budget_credits"])
    budget_unmet = max(0.0, operating_cost - budget_available)
    state["budget_credits"] = round(max(0.0, budget_available - operating_cost), 6)
    state["period"] = period_number
    state["battery_soc_kwh"] = round(battery_soc, 6)

    service_dispatch = next(
        (item["dispatch"] for item in interval_reports if item["interval"] == "maintenance"),
        None,
    )
    service_battery = next(
        (item["battery_discharge"] for item in interval_reports
         if item["interval"] == "maintenance"),
        None,
    )
    critical_allocation = None
    if service_dispatch:
        critical_allocation = next(
            (item for item in service_dispatch["loads"] if item["load_id"] == power["critical_dispatch_group_id"]),
            None,
        )
    service_power_ok = (
        service_duration_hours == 0
        or (
            service_battery is not None
            and service_battery["discharge_shortfall_kw"] <= 1e-9
            and critical_allocation is not None
            and critical_allocation["allocated_kw"] >= float(power["critical_load_kw"]) - 1e-9
        )
    )
    critical_services_met = all(
        any(
            allocation["load_id"] == power["critical_dispatch_group_id"]
            and allocation["allocated_kw"] >= float(power["critical_load_kw"]) - 1e-9
            for allocation in interval["dispatch"]["loads"]
        )
        for interval in interval_reports
    )
    all_electrical_loads_served = all(
        interval["dispatch"]["all_loads_served"] for interval in interval_reports
    )
    battery_config = power["battery"]
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
        "material_processes": process_reports,
        "project_production_bonuses": production_bonus_report,
        "power_intervals": interval_reports,
        "available_generation_kw": generation_kw,
        "average_load_kw": average_load_kw,
        "generated_kwh": total_generated_kwh,
        "consumed_kwh": total_consumed_kwh,
        "allocated_kwh": total_allocated_kwh,
        "unused_allocated_process_energy_kwh": unused_allocated_process_energy_kwh,
        "battery_discharge_kwh": total_battery_discharge_kwh,
        "battery_charge_input_kwh": total_battery_charge_input_kwh,
        "battery_loss_kwh": total_battery_loss_kwh,
        "energy_margin_kwh": (
            total_generated_kwh + total_battery_discharge_kwh
            - total_consumed_kwh - total_battery_charge_input_kwh
        ),
        "battery": {
            "capacity_kwh": float(battery_config["capacity_kwh"]),
            "state_of_charge_start_kwh": battery_soc_start,
            "state_of_charge_end_kwh": battery_soc,
            "minimum_charge_kwh": float(battery_config["minimum_charge_kwh"]),
            "within_operating_limits": (
                battery_soc >= float(battery_config["minimum_charge_kwh"]) - 1e-9
                and battery_soc <= float(battery_config["capacity_kwh"]) + 1e-9
            ),
        },
        "power_service": {
            "modules_online": modules_online,
            "available_generation_kw": service_generation_kw,
            "critical_load_kw": float(power["critical_load_kw"]),
            "critical_peak_kw": float(power["critical_peak_kw"]),
            "duration_hours": service_duration_hours,
            "battery_required_kwh": max(
                0.0, float(power["critical_peak_kw"]) - service_generation_kw
            ) * service_duration_hours,
            "battery_discharge_kwh": (
                service_battery["discharge_output_kwh"] if service_battery else 0.0
            ),
            "battery_shortfall_kw": (
                service_battery["discharge_shortfall_kw"] if service_battery else 0.0
            ),
            "within_reserve": service_power_ok,
            "dispatch": service_dispatch,
        },
        "power_dispatch": service_dispatch,
        "budget_cost_credits": operating_cost,
        "budget_unmet_credits": budget_unmet,
        "event_id": event.get("id"),
        "shed_kwh": total_shed_kwh,
        "all_electrical_loads_served": all_electrical_loads_served,
        "essential_demands_met": (
            oxygen_unmet <= 1e-9
            and food_unmet <= 1e-9
            and water_unmet <= 1e-9
            and ice_shortfall <= 1e-9
            and critical_services_met
            and service_power_ok
        ),
    }
    return state, report

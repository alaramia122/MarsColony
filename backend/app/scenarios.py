"""Load and validate versioned scenario definitions."""
import json
import re
from math import isfinite
from pathlib import Path
from typing import Any


MODULE_PATH = Path(__file__).resolve()
DATA_ROOTS = (MODULE_PATH.parents[2], MODULE_PATH.parents[1])


class ScenarioValidationError(ValueError):
    """Raised when a scenario contains inconsistent or unsafe model data."""


def _positive_number(value: Any, label: str, allow_zero: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ScenarioValidationError(f"{label} must be numeric.") from error
    if not isfinite(number) or (number < 0 if allow_zero else number <= 0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ScenarioValidationError(f"{label} must be finite and {qualifier}.")
    return number


def validate_scenario(scenario: dict[str, Any]) -> None:
    """Validate resource, storage, process, battery and dispatch references."""
    for key in ("scenario_id", "version", "time", "population", "stocks",
                "resource_catalog", "storage_locations", "processes", "power", "finance"):
        if key not in scenario:
            raise ScenarioValidationError(f"Scenario is missing '{key}'.")

    period_count_value = scenario["time"]["period_count"]
    if not isinstance(period_count_value, int) or isinstance(period_count_value, bool):
        raise ScenarioValidationError("Scenario period_count must be an integer.")
    period_count = period_count_value
    if period_count < 1:
        raise ScenarioValidationError("Scenario period_count must be positive.")
    period_sols = _positive_number(scenario["time"]["period_sols"], "period_sols")
    _positive_number(scenario["time"]["hours_per_sol"], "hours_per_sol")
    if not isinstance(scenario["population"]["total"], int) or scenario["population"]["total"] <= 0:
        raise ScenarioValidationError("Population total must be a positive integer.")
    if "total_sols" in scenario["time"]:
        total_sols = _positive_number(scenario["time"]["total_sols"], "total_sols")
        if abs(total_sols - period_sols * period_count) > 1e-9:
            raise ScenarioValidationError("total_sols must equal period_sols multiplied by period_count.")

    storage_by_id = {}
    for storage in scenario["storage_locations"]:
        storage_id = storage.get("id")
        if not storage_id or storage_id in storage_by_id:
            raise ScenarioValidationError("Storage identifiers must be unique and non-empty.")
        if not storage.get("name") or not storage.get("location"):
            raise ScenarioValidationError(
                f"Storage '{storage_id}' needs a name and physical location."
            )
        capacity = storage.get("capacity")
        if capacity is not None:
            _positive_number(capacity, f"Storage capacity '{storage_id}'", allow_zero=True)
        storage_by_id[storage_id] = storage

    state_keys = set()
    for resource_id, resource in scenario["resource_catalog"].items():
        state_key = resource.get("state_key")
        if state_key in state_keys:
            raise ScenarioValidationError("Resource state keys must be unique.")
        state_keys.add(state_key)
        if not state_key or state_key not in scenario["stocks"]:
            raise ScenarioValidationError(
                f"Resource '{resource_id}' references a missing initial stock."
            )
        _positive_number(
            scenario["stocks"][state_key],
            f"Initial stock '{state_key}'",
            allow_zero=True,
        )
        if not resource.get("unit"):
            raise ScenarioValidationError(f"Resource '{resource_id}' has no unit.")
        precision = resource.get("precision_digits")
        if not isinstance(precision, int) or not 0 <= precision <= 9:
            raise ScenarioValidationError(
                f"Resource '{resource_id}' precision_digits must be between 0 and 9."
            )
        storage_id = resource.get("storage_id")
        storage = storage_by_id.get(storage_id)
        if storage is None or storage.get("resource_id") != resource_id:
            raise ScenarioValidationError(
                f"Resource '{resource_id}' has an invalid storage location."
            )
        capacity = storage.get("capacity")
        if capacity is not None and float(scenario["stocks"][state_key]) > float(capacity):
            raise ScenarioValidationError(
                f"Initial stock '{state_key}' exceeds storage capacity."
            )

    for process_id, process in scenario["processes"].items():
        _positive_number(
            process["requested_cycles_per_period"],
            f"Process '{process_id}' requested_cycles_per_period",
            allow_zero=True,
        )
        _positive_number(
            process["energy_kwh_per_cycle"],
            f"Process '{process_id}' energy_kwh_per_cycle",
            allow_zero=True,
        )
        inputs = process.get("inputs_per_cycle", {})
        outputs = process.get("outputs_per_cycle", {})
        if not outputs:
            raise ScenarioValidationError(f"Process '{process_id}' must produce a resource.")
        if set(inputs).intersection(outputs):
            raise ScenarioValidationError(
                f"Process '{process_id}' cannot consume and produce the same resource in one recipe."
            )
        for resource_id, quantity in {**inputs, **outputs}.items():
            if resource_id not in scenario["resource_catalog"]:
                raise ScenarioValidationError(
                    f"Process '{process_id}' references unknown resource '{resource_id}'."
                )
            _positive_number(quantity, f"Process '{process_id}' quantity", allow_zero=False)

    power = scenario["power"]
    if not isinstance(power["module_count"], int) or power["module_count"] <= 0:
        raise ScenarioValidationError("Installed power module count must be a positive integer.")
    _positive_number(power["module_nominal_kw"], "Power module nominal rating")
    availability = _positive_number(power["availability_factor"], "Power availability factor")
    if availability > 1:
        raise ScenarioValidationError("Power availability factor cannot exceed 1.")
    for load_id, load_kw in power["loads_kw"].items():
        _positive_number(load_kw, f"Electrical load '{load_id}'", allow_zero=True)
    _positive_number(power["critical_load_kw"], "Critical base load")
    _positive_number(power["critical_peak_kw"], "Critical peak load")
    battery = power["battery"]
    capacity = _positive_number(battery["capacity_kwh"], "Battery capacity")
    initial = _positive_number(
        battery["initial_charge_kwh"], "Battery initial charge", allow_zero=True
    )
    minimum = _positive_number(
        battery["minimum_charge_kwh"], "Battery minimum charge", allow_zero=True
    )
    if not 0 <= minimum <= initial <= capacity:
        raise ScenarioValidationError("Battery initial and minimum charge must fit capacity.")
    for key in ("maximum_charge_kw", "maximum_discharge_kw"):
        _positive_number(battery[key], f"Battery {key}", allow_zero=True)
    for key in ("charge_efficiency", "discharge_efficiency"):
        efficiency = _positive_number(battery[key], f"Battery {key}")
        if efficiency > 1:
            raise ScenarioValidationError(f"Battery {key} cannot exceed 1.")

    load_ids = set(power["loads_kw"])
    group_ids = set()
    covered_loads = []
    process_groups = set()
    critical_group_id = power.get("critical_dispatch_group_id")
    critical_group_load_kw = 0.0
    for group in power["dispatch_groups"]:
        group_id = group.get("id")
        if not group_id or group_id in group_ids:
            raise ScenarioValidationError("Dispatch group identifiers must be unique.")
        group_ids.add(group_id)
        if not isinstance(group.get("priority"), int):
            raise ScenarioValidationError(f"Dispatch group '{group_id}' needs an integer priority.")
        if not group.get("load_sources"):
            raise ScenarioValidationError(f"Dispatch group '{group_id}' has no load sources.")
        for source in group["load_sources"]:
            if source not in load_ids:
                raise ScenarioValidationError(
                    f"Dispatch group '{group_id}' references unknown load '{source}'."
                )
            covered_loads.append(source)
            if group_id == critical_group_id:
                critical_group_load_kw += float(power["loads_kw"][source])
        _positive_number(group.get("minimum_kw", 0), f"Dispatch group '{group_id}' minimum", True)
        process_id = group.get("process_id")
        if process_id is not None:
            if process_id not in scenario["processes"]:
                raise ScenarioValidationError(
                    f"Dispatch group '{group_id}' references unknown process '{process_id}'."
                )
            if process_id in process_groups:
                raise ScenarioValidationError(
                    f"Process '{process_id}' is assigned to multiple dispatch groups."
                )
            process_groups.add(process_id)

    if sorted(covered_loads) != sorted(load_ids):
        raise ScenarioValidationError(
            "Every electrical load must belong to exactly one dispatch group."
        )
    if critical_group_id not in group_ids:
        raise ScenarioValidationError("The critical dispatch group is missing.")
    if abs(critical_group_load_kw - float(power["critical_load_kw"])) > 1e-6:
        raise ScenarioValidationError(
            "Critical dispatch group load must match critical_load_kw."
        )
    if float(power["critical_peak_kw"]) < float(power["critical_load_kw"]):
        raise ScenarioValidationError("Critical peak load cannot be below critical base load.")
    for process_id, process in scenario["processes"].items():
        if float(process["energy_kwh_per_cycle"]) > 0 and process_id not in process_groups:
            raise ScenarioValidationError(
                f"Energy-consuming process '{process_id}' has no power dispatch group."
            )

    for event in scenario.get("scripted_events", []):
        period = int(event.get("period", 0))
        if not 1 <= period <= period_count:
            raise ScenarioValidationError("Scripted event period is outside the scenario.")
        known_multiplier_keys = {
            process.get("event_multiplier_key")
            for process in scenario["processes"].values()
            if process.get("event_multiplier_key")
        }
        for key in known_multiplier_keys:
            if key in event:
                multiplier = _positive_number(event[key], f"Event {key}", allow_zero=True)
                if multiplier > 10:
                    raise ScenarioValidationError(f"Event {key} is outside the supported range.")
        if "duration_hours" in event:
            duration = _positive_number(event["duration_hours"], "Event duration", allow_zero=True)
            period_hours = period_sols * float(scenario["time"]["hours_per_sol"])
            if duration > period_hours:
                raise ScenarioValidationError("Event duration exceeds one scenario period.")
        if "modules_online" in event:
            modules_online = event["modules_online"]
            if not isinstance(modules_online, int) or not 0 <= modules_online <= power["module_count"]:
                raise ScenarioValidationError("Event modules_online is outside the installed fleet.")


def load_scenario(scenario_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", scenario_id):
        raise FileNotFoundError("Scenario identifier has an invalid format.")
    for root in DATA_ROOTS:
        path = root / "data" / "scenarios" / f"{scenario_id}.json"
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8") as scenario_file:
                    scenario = json.load(scenario_file)
                if scenario.get("scenario_id") != scenario_id:
                    raise ScenarioValidationError(
                        "Scenario filename and scenario_id differ."
                    )
                validate_scenario(scenario)
                return scenario
            except ScenarioValidationError:
                raise
            except (KeyError, TypeError, ValueError) as error:
                raise ScenarioValidationError(
                    f"Scenario '{scenario_id}' is malformed: {error}"
                ) from error
    raise FileNotFoundError(f"Unknown scenario: {scenario_id}")

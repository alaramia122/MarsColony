"""Deterministic power dispatch and battery operation primitives."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


class EnergyModelError(ValueError):
    """Raised when an electrical dispatch or battery input is invalid."""


@dataclass(frozen=True)
class PowerLoad:
    """A consumer with a dispatch priority and optional minimum stable load."""

    load_id: str
    requested_kw: float
    priority: int
    minimum_kw: float = 0.0


@dataclass(frozen=True)
class BatterySpec:
    """Battery limits; efficiencies are fractions between zero and one."""

    capacity_kwh: float
    minimum_charge_kwh: float
    maximum_charge_kw: float
    maximum_discharge_kw: float
    charge_efficiency: float
    discharge_efficiency: float


def _validate_battery(spec: BatterySpec, state_of_charge_kwh: float) -> None:
    values = (
        spec.capacity_kwh,
        spec.minimum_charge_kwh,
        spec.maximum_charge_kw,
        spec.maximum_discharge_kw,
        spec.charge_efficiency,
        spec.discharge_efficiency,
        state_of_charge_kwh,
    )
    if not all(isfinite(value) for value in values):
        raise EnergyModelError("Battery parameters must be finite.")
    if spec.capacity_kwh <= 0:
        raise EnergyModelError("Battery capacity must be positive.")
    if not 0 <= spec.minimum_charge_kwh <= spec.capacity_kwh:
        raise EnergyModelError("Battery minimum charge must fit its capacity.")
    if not 0 <= state_of_charge_kwh <= spec.capacity_kwh:
        raise EnergyModelError("Battery state of charge must fit its capacity.")
    if spec.maximum_charge_kw < 0 or spec.maximum_discharge_kw < 0:
        raise EnergyModelError("Battery power limits must be non-negative.")
    if not 0 < spec.charge_efficiency <= 1:
        raise EnergyModelError("Battery charge efficiency must be in (0, 1].")
    if not 0 < spec.discharge_efficiency <= 1:
        raise EnergyModelError("Battery discharge efficiency must be in (0, 1].")


def operate_battery(
    state_of_charge_kwh: float,
    spec: BatterySpec,
    duration_hours: float,
    requested_discharge_kw: float = 0.0,
    available_charge_kw: float = 0.0,
) -> dict:
    """Apply one interval of charging or discharging, never both at once."""
    _validate_battery(spec, state_of_charge_kwh)
    if not isfinite(duration_hours) or duration_hours <= 0:
        raise EnergyModelError("Battery interval must be finite and positive.")
    if not isfinite(requested_discharge_kw) or requested_discharge_kw < 0:
        raise EnergyModelError("Requested battery discharge must be non-negative.")
    if not isfinite(available_charge_kw) or available_charge_kw < 0:
        raise EnergyModelError("Available charging power must be non-negative.")
    if requested_discharge_kw > 0 and available_charge_kw > 0:
        raise EnergyModelError("Battery cannot charge and discharge in one interval.")

    discharge_limit_kw = (
        max(0.0, state_of_charge_kwh - spec.minimum_charge_kwh)
        * spec.discharge_efficiency
        / duration_hours
    )
    discharge_kw = min(
        requested_discharge_kw,
        spec.maximum_discharge_kw,
        discharge_limit_kw,
    )
    discharge_output_kwh = discharge_kw * duration_hours
    discharge_stored_kwh = discharge_output_kwh / spec.discharge_efficiency

    charge_room_kwh = spec.capacity_kwh - state_of_charge_kwh
    charge_input_limit_kw = (
        charge_room_kwh / spec.charge_efficiency / duration_hours
    )
    charge_kw = min(
        available_charge_kw,
        spec.maximum_charge_kw,
        max(0.0, charge_input_limit_kw),
    )
    charge_input_kwh = charge_kw * duration_hours
    charge_stored_kwh = charge_input_kwh * spec.charge_efficiency

    new_state_of_charge = (
        state_of_charge_kwh - discharge_stored_kwh + charge_stored_kwh
    )
    new_state_of_charge = min(spec.capacity_kwh, max(0.0, new_state_of_charge))
    return {
        "state_of_charge_start_kwh": state_of_charge_kwh,
        "state_of_charge_end_kwh": new_state_of_charge,
        "discharge_kw": discharge_kw,
        "discharge_output_kwh": discharge_output_kwh,
        "discharge_stored_kwh": discharge_stored_kwh,
        "charge_kw": charge_kw,
        "charge_input_kwh": charge_input_kwh,
        "charge_stored_kwh": charge_stored_kwh,
        "energy_loss_kwh": (
            discharge_stored_kwh - discharge_output_kwh
            + charge_input_kwh - charge_stored_kwh
        ),
        "discharge_shortfall_kw": max(0.0, requested_discharge_kw - discharge_kw),
    }


def dispatch_power(
    available_kw: float,
    loads: list[PowerLoad],
    duration_hours: float = 1.0,
) -> dict:
    """Allocate available power in ascending priority order, then by load ID.

    A consumer receives its requested load when possible. A partial allocation
    is made only when it meets that consumer's minimum stable load; otherwise
    the consumer is shed for this dispatch interval.
    """
    if not isfinite(available_kw) or available_kw < 0:
        raise EnergyModelError("Available power must be finite and non-negative.")
    if not isfinite(duration_hours) or duration_hours <= 0:
        raise EnergyModelError("Duration must be finite and positive.")

    identifiers: set[str] = set()
    for load in loads:
        if not load.load_id or load.load_id in identifiers:
            raise EnergyModelError("Power load identifiers must be unique and non-empty.")
        identifiers.add(load.load_id)
        if not isfinite(load.requested_kw) or load.requested_kw < 0:
            raise EnergyModelError(
                f"Requested power for '{load.load_id}' must be non-negative."
            )
        if not isfinite(load.minimum_kw) or not 0 <= load.minimum_kw <= load.requested_kw:
            raise EnergyModelError(
                f"Minimum power for '{load.load_id}' must be between zero and demand."
            )

    remaining_kw = available_kw
    allocations = []
    for load in sorted(loads, key=lambda item: (item.priority, item.load_id)):
        allocated_kw = min(load.requested_kw, remaining_kw)
        if allocated_kw < load.minimum_kw:
            allocated_kw = 0.0
        remaining_kw -= allocated_kw
        allocations.append({
            "load_id": load.load_id,
            "priority": load.priority,
            "requested_kw": load.requested_kw,
            "allocated_kw": allocated_kw,
            "shed_kw": load.requested_kw - allocated_kw,
            "served": allocated_kw >= load.requested_kw,
        })

    requested_kw = sum(load.requested_kw for load in loads)
    allocated_kw = sum(item["allocated_kw"] for item in allocations)
    return {
        "available_kw": available_kw,
        "requested_kw": requested_kw,
        "allocated_kw": allocated_kw,
        "unallocated_kw": max(0.0, available_kw - allocated_kw),
        "duration_hours": duration_hours,
        "requested_kwh": requested_kw * duration_hours,
        "allocated_kwh": allocated_kw * duration_hours,
        "shed_kwh": (requested_kw - allocated_kw) * duration_hours,
        "loads": allocations,
        "all_loads_served": allocated_kw >= requested_kw - 1e-9,
    }

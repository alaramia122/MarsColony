"""Deterministic material-flow calculations for the colony economy."""
from __future__ import annotations

from dataclasses import dataclass
from math import isclose, isfinite
from typing import Mapping


class ResourceModelError(ValueError):
    """Raised when a resource stock or production recipe is invalid."""


@dataclass(frozen=True)
class ResourceStock:
    """Available quantity and optional storage capacity for one resource."""

    quantity: float
    capacity: float | None = None

    def validate(self, resource_id: str) -> None:
        if not isfinite(self.quantity) or self.quantity < 0:
            raise ResourceModelError(
                f"Stock for '{resource_id}' must be a finite non-negative number."
            )
        if self.capacity is not None:
            if not isfinite(self.capacity) or self.capacity < 0:
                raise ResourceModelError(
                    f"Capacity for '{resource_id}' must be finite and non-negative."
                )
            if self.quantity > self.capacity:
                raise ResourceModelError(
                    f"Stock for '{resource_id}' exceeds its storage capacity."
                )


def _validate_recipe(recipe: Mapping[str, float], label: str) -> None:
    for resource_id, quantity in recipe.items():
        if not resource_id:
            raise ResourceModelError(f"{label} contains an empty resource identifier.")
        if not isfinite(quantity) or quantity <= 0:
            raise ResourceModelError(
                f"{label} quantity for '{resource_id}' must be finite and positive."
            )


def execute_process(
    stocks: Mapping[str, ResourceStock],
    inputs_per_cycle: Mapping[str, float],
    outputs_per_cycle: Mapping[str, float],
    requested_cycles: float,
    energy_kwh_per_cycle: float = 0.0,
    energy_available_kwh: float | None = None,
) -> tuple[dict[str, ResourceStock], dict]:
    """Execute as many complete-equivalent process cycles as resources allow.

    Fractional cycles are supported for aggregated industrial processes. Inputs,
    energy and output storage capacity constrain the same deterministic cycle
    count. The input mapping is never mutated.
    """
    if not inputs_per_cycle and not outputs_per_cycle:
        raise ResourceModelError("A process must consume or produce a resource.")
    _validate_recipe(inputs_per_cycle, "Inputs")
    _validate_recipe(outputs_per_cycle, "Outputs")
    if set(inputs_per_cycle).intersection(outputs_per_cycle):
        raise ResourceModelError(
            "A resource cannot be both an input and an output in one process recipe."
        )
    if not isfinite(requested_cycles) or requested_cycles <= 0:
        raise ResourceModelError("Requested cycles must be finite and positive.")
    if not isfinite(energy_kwh_per_cycle) or energy_kwh_per_cycle < 0:
        raise ResourceModelError("Energy per cycle must be finite and non-negative.")
    if energy_kwh_per_cycle > 0:
        if energy_available_kwh is None:
            raise ResourceModelError("Available energy is required for this process.")
        if not isfinite(energy_available_kwh) or energy_available_kwh < 0:
            raise ResourceModelError("Available energy must be finite and non-negative.")

    result = dict(stocks)
    for resource_id, stock in result.items():
        stock.validate(resource_id)

    required_ids = set(inputs_per_cycle) | set(outputs_per_cycle)
    missing_ids = required_ids.difference(result)
    if missing_ids:
        raise ResourceModelError(
            "Missing stock definitions: " + ", ".join(sorted(missing_ids))
        )

    limits: list[tuple[str, float]] = [("requested_cycles", requested_cycles)]
    for resource_id, amount in inputs_per_cycle.items():
        limits.append((f"input:{resource_id}", result[resource_id].quantity / amount))
    if energy_kwh_per_cycle > 0:
        limits.append(("energy", float(energy_available_kwh) / energy_kwh_per_cycle))
    for resource_id, amount in outputs_per_cycle.items():
        stock = result[resource_id]
        if stock.capacity is not None:
            remaining_capacity = stock.capacity - stock.quantity
            limits.append((f"capacity:{resource_id}", remaining_capacity / amount))

    completed_cycles = max(0.0, min(value for _, value in limits))
    limiting_constraints = [
        name for name, value in limits
        if isclose(value, completed_cycles, rel_tol=1e-9, abs_tol=1e-9)
    ]

    consumed = {
        resource_id: amount * completed_cycles
        for resource_id, amount in inputs_per_cycle.items()
    }
    produced = {
        resource_id: amount * completed_cycles
        for resource_id, amount in outputs_per_cycle.items()
    }
    for resource_id, amount in consumed.items():
        stock = result[resource_id]
        result[resource_id] = ResourceStock(
            quantity=max(0.0, stock.quantity - amount),
            capacity=stock.capacity,
        )
    for resource_id, amount in produced.items():
        stock = result[resource_id]
        result[resource_id] = ResourceStock(
            quantity=stock.quantity + amount,
            capacity=stock.capacity,
        )

    energy_used = completed_cycles * energy_kwh_per_cycle
    return result, {
        "requested_cycles": requested_cycles,
        "completed_cycles": completed_cycles,
        "uncompleted_cycles": max(0.0, requested_cycles - completed_cycles),
        "inputs_consumed": consumed,
        "outputs_produced": produced,
        "energy_used_kwh": energy_used,
        "limiting_constraints": limiting_constraints,
        "completed": isclose(
            completed_cycles, requested_cycles, rel_tol=1e-9, abs_tol=1e-9
        ),
    }

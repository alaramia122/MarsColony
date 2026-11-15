"""Deterministic priority-based electrical load dispatch."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


class EnergyModelError(ValueError):
    """Raised when an electrical dispatch input is invalid."""


@dataclass(frozen=True)
class PowerLoad:
    """A consumer with a dispatch priority and optional minimum stable load."""

    load_id: str
    requested_kw: float
    priority: int
    minimum_kw: float = 0.0


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

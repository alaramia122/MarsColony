"""Deterministic axial-coordinate helpers for the colony's pointy-top hex map.

World coordinates are independent of viewport size and zoom. Legacy percentage
coordinates are converted against a fixed 100x100 logical map extent.
"""
from __future__ import annotations

from math import sqrt
from typing import Iterable

SQRT3 = sqrt(3.0)
LEGACY_MAP_EXTENT = 100.0
LEGACY_HEX_SIZE = 2.0


def axial_to_pixel(q: int, r: int, size: float = 1.0) -> tuple[float, float]:
    """Convert axial coordinates to pointy-top Cartesian coordinates."""
    if size <= 0:
        raise ValueError("Размер гекса должен быть положительным.")
    return (
        size * SQRT3 * (q + r / 2.0),
        size * 1.5 * r,
    )


def pixel_to_axial(x: float, y: float, size: float = 1.0) -> tuple[int, int]:
    """Convert Cartesian coordinates to the nearest pointy-top hex."""
    if size <= 0:
        raise ValueError("Размер гекса должен быть положительным.")
    fractional_q = (SQRT3 / 3.0 * x - y / 3.0) / size
    fractional_r = (2.0 / 3.0 * y) / size
    cube_x = fractional_q
    cube_z = fractional_r
    cube_y = -cube_x - cube_z
    rounded_x = round(cube_x)
    rounded_y = round(cube_y)
    rounded_z = round(cube_z)
    delta_x = abs(rounded_x - cube_x)
    delta_y = abs(rounded_y - cube_y)
    delta_z = abs(rounded_z - cube_z)
    if delta_x > delta_y and delta_x > delta_z:
        rounded_x = -rounded_y - rounded_z
    elif delta_y > delta_z:
        rounded_y = -rounded_x - rounded_z
    else:
        rounded_z = -rounded_x - rounded_y
    return int(rounded_x), int(rounded_z)


def hex_distance(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Return shortest distance between two axial cells."""
    aq, ar = a
    bq, br = b
    dq = aq - bq
    dr = ar - br
    return (abs(dq) + abs(dr) + abs(dq + dr)) // 2


def hex_neighbors(q: int, r: int) -> list[tuple[int, int]]:
    """Return the six neighboring cells in stable clockwise order."""
    return [
        (q + 1, r), (q + 1, r - 1), (q, r - 1),
        (q - 1, r), (q - 1, r + 1), (q, r + 1),
    ]


def normalize_footprint(cells: Iterable[tuple[int, int]]) -> list[list[int]]:
    """Validate and normalize a footprint; the anchor cell must be included."""
    normalized = sorted({(int(q), int(r)) for q, r in cells})
    if not normalized:
        raise ValueError("Размер занимаемой площадки не может быть нулевым.")
    if len(normalized) > 256:
        raise ValueError("Площадка не может занимать более 256 гексов.")
    return [[q, r] for q, r in normalized]


def legacy_percent_to_axial(x_percent: float, y_percent: float) -> tuple[int, int]:
    """Map old 0..100 percentage positions onto a fixed logical hex grid."""
    if not (0.0 <= x_percent <= 100.0 and 0.0 <= y_percent <= 100.0):
        raise ValueError("Старые координаты карты должны находиться в диапазоне от 0 до 100.")
    x = x_percent / 100.0 * LEGACY_MAP_EXTENT
    y = y_percent / 100.0 * LEGACY_MAP_EXTENT
    return pixel_to_axial(x, y, LEGACY_HEX_SIZE)

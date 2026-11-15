"""Shared catalogue for reusable colony entities and their economic contracts."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .domain.projects import DOMAIN_CODES

ROOTS = (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[1])
ENTITY_TYPES = {"infrastructure", "equipment", "vehicle", "technology", "process", "procedure"}
EFFECT_KEYS = {
    "power_generation_kw", "power_load_kw",
    "resource_inputs_per_period", "resource_outputs_per_period",
    "operating_cost_credits_per_period", "battery_capacity_kwh",
}
BUILD_KEYS = {"budget_credits", "duration_periods", "labor_fte", "energy_kwh_per_period", "resource_requirements"}


class EntityCatalogError(ValueError):
    """A catalogue definition is malformed or unsafe for simulation."""


def load_entity_catalog(scenario_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", scenario_id):
        raise FileNotFoundError("Scenario identifier has an invalid format.")
    for root in ROOTS:
        path = root / "data" / "entity_catalogs" / f"{scenario_id}.json"
        if path.is_file():
            with path.open("r", encoding="utf-8") as source:
                catalogue = json.load(source)
            validate_catalog(catalogue, scenario_id)
            return catalogue
    raise FileNotFoundError(f"No entity catalogue for scenario '{scenario_id}'.")


def _finite_nonnegative(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
        raise EntityCatalogError(f"{field} must be a finite non-negative number.")
    return float(value)


def validate_entity(entity: dict[str, Any], resource_catalog: dict[str, Any], scenario_id: str) -> None:
    if not isinstance(entity, dict):
        raise EntityCatalogError("Entity definition must be an object.")
    allowed_entity_keys = {
        "id", "entity_type", "name", "description", "domain_codes", "kind",
        "build", "research", "operation", "effects", "tags", "prerequisites",
        "capabilities",
    }
    if not set(entity).issubset(allowed_entity_keys):
        raise EntityCatalogError("Entity definition contains unsupported top-level fields.")
    if not isinstance(entity.get("id"), str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", entity["id"]):
        raise EntityCatalogError("Entity ID must be a stable lowercase identifier.")
    if entity.get("entity_type") not in ENTITY_TYPES:
        raise EntityCatalogError("Unsupported entity type.")
    if not isinstance(entity.get("name"), str) or not entity["name"].strip() or len(entity["name"]) > 160:
        raise EntityCatalogError("Entity name is required and must not exceed 160 characters.")
    if not isinstance(entity.get("description"), str) or not entity["description"].strip():
        raise EntityCatalogError("Entity description is required.")
    domains = entity.get("domain_codes")
    if (
        not isinstance(domains, list) or not domains
        or len(domains) != len(set(domains))
        or not set(domains).issubset(DOMAIN_CODES)
    ):
        raise EntityCatalogError("Entity must belong to one or more unique valid responsibility domains.")
    if entity["entity_type"] in {"infrastructure", "equipment", "vehicle", "process"}:
        if not isinstance(entity.get("kind"), str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", entity["kind"]):
            raise EntityCatalogError("Buildable entities require a stable lowercase kind identifier.")
    if "tags" in entity and (
        not isinstance(entity["tags"], list)
        or any(not isinstance(tag, str) or not tag.strip() for tag in entity["tags"])
    ):
        raise EntityCatalogError("Entity tags must be a list of non-empty strings.")
    build = entity.get("build", entity.get("research", {}))
    if not isinstance(build, dict) or not set(build).issubset(BUILD_KEYS):
        raise EntityCatalogError("Build/research contract contains unsupported fields.")
    for key in ("budget_credits", "duration_periods", "labor_fte", "energy_kwh_per_period"):
        if key in build:
            _finite_nonnegative(build[key], f"{key}")
    if "duration_periods" in build and (int(build["duration_periods"]) != build["duration_periods"] or build["duration_periods"] < 1):
        raise EntityCatalogError("duration_periods must be a positive integer.")
    requirements = build.get("resource_requirements", {})
    if not isinstance(requirements, dict):
        raise EntityCatalogError("resource_requirements must be an object.")
    for resource_id, quantity in requirements.items():
        if resource_id not in resource_catalog:
            raise EntityCatalogError(f"Unknown resource '{resource_id}' in entity requirements.")
        _finite_nonnegative(quantity, f"resource_requirements.{resource_id}")
    if "build" in entity and "research" in entity:
        raise EntityCatalogError("An entity must define either build requirements or research requirements, not both.")
    if entity["entity_type"] in {"infrastructure", "equipment", "vehicle", "process"}:
        if "effects" in entity:
            raise EntityCatalogError("Only technology entities may define technology effects.")
        operation = entity.get("operation", {})
        if not isinstance(operation, dict) or not set(operation).issubset(EFFECT_KEYS):
            raise EntityCatalogError("Operation contract contains unsupported effects.")
        for key in ("power_generation_kw", "power_load_kw", "operating_cost_credits_per_period", "battery_capacity_kwh"):
            if key in operation:
                _finite_nonnegative(operation[key], key)
        for key in ("resource_inputs_per_period", "resource_outputs_per_period"):
            values = operation.get(key, {})
            if not isinstance(values, dict):
                raise EntityCatalogError(f"{key} must be an object.")
            for resource_id, quantity in values.items():
                if resource_id not in resource_catalog:
                    raise EntityCatalogError(f"Unknown resource '{resource_id}' in {key}.")
                if _finite_nonnegative(quantity, f"{key}.{resource_id}") <= 0:
                    raise EntityCatalogError(f"{key}.{resource_id} must be positive.")
    if entity["entity_type"] == "technology":
        if "operation" in entity:
            raise EntityCatalogError("Technology entities use research and effects, not infrastructure operation contracts.")
        effects = entity.get("effects", {})
        if not isinstance(effects, dict):
            raise EntityCatalogError("Technology effects must be an object.")
        supported_effects = {
            "resource_output_multiplier",
            "labor_demand_multiplier",
            "asset_condition_loss_multiplier",
        }
        if not set(effects).issubset(supported_effects):
            raise EntityCatalogError("Technology contains an unsupported economic effect.")
        multipliers = effects.get("resource_output_multiplier", {})
        if not isinstance(multipliers, dict):
            raise EntityCatalogError("resource_output_multiplier must be an object.")
        for resource_id, multiplier in multipliers.items():
            if resource_id not in resource_catalog:
                raise EntityCatalogError(f"Unknown technology output resource '{resource_id}'.")
            if not 0 < _finite_nonnegative(multiplier, f"resource_output_multiplier.{resource_id}") <= 3:
                raise EntityCatalogError("Technology resource multipliers must be greater than zero and no more than 3.")
        for key in ("labor_demand_multiplier", "asset_condition_loss_multiplier"):
            if key in effects and not 0 < _finite_nonnegative(effects[key], key) <= 1:
                raise EntityCatalogError(f"{key} must be greater than zero and no more than one.")
    if entity["entity_type"] == "procedure" and ("operation" in entity or "effects" in entity):
        raise EntityCatalogError("Procedures cannot define infrastructure or technology economic effects.")


def validate_catalog(catalogue: dict[str, Any], scenario_id: str) -> None:
    allowed_catalogue_keys = {"scenario_id", "version", "entities", "starter_kind_overrides"}
    if (
        not isinstance(catalogue, dict)
        or not set(catalogue).issubset(allowed_catalogue_keys)
        or catalogue.get("scenario_id") != scenario_id
        or not isinstance(catalogue.get("entities"), list)
    ):
        raise EntityCatalogError("Catalogue scenario_id, fields or entities list are invalid.")
    ids = set()
    # Resource validation is completed by the caller using the scenario resource catalogue.
    for entity in catalogue["entities"]:
        if entity.get("id") in ids:
            raise EntityCatalogError("Entity catalogue IDs must be unique.")
        ids.add(entity.get("id"))


def validate_entity_catalogue_resources(
    catalogue: dict[str, Any],
    resource_catalog: dict[str, Any],
) -> None:
    allowed_processes = {"oxygen_isru", "food_growing", "ice_to_usable_water"}
    overrides = catalogue.get("starter_kind_overrides", {})
    if not isinstance(overrides, dict):
        raise EntityCatalogError("starter_kind_overrides must be an object.")
    for kind, definition in overrides.items():
        if not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", kind):
            raise EntityCatalogError("Starter override kind must be a stable lowercase identifier.")
        if not isinstance(definition, dict) or not set(definition).issubset({"economic_effects", "process_bindings"}):
            raise EntityCatalogError(f"Starter override '{kind}' contains unsupported fields.")
        effects = definition.get("economic_effects", {})
        if not isinstance(effects, dict) or not set(effects).issubset(EFFECT_KEYS):
            raise EntityCatalogError(f"Starter override '{kind}' has an invalid economic contract.")
        for key in ("power_generation_kw", "power_load_kw", "operating_cost_credits_per_period", "battery_capacity_kwh"):
            if key in effects:
                _finite_nonnegative(effects[key], f"{kind}.{key}")
        for key in ("resource_inputs_per_period", "resource_outputs_per_period"):
            values = effects.get(key, {})
            if not isinstance(values, dict):
                raise EntityCatalogError(f"{kind}.{key} must be an object.")
            for resource_id, quantity in values.items():
                if resource_id not in resource_catalog:
                    raise EntityCatalogError(f"Starter override '{kind}' references unknown resource '{resource_id}'.")
                if _finite_nonnegative(quantity, f"{kind}.{key}.{resource_id}") <= 0:
                    raise EntityCatalogError(f"{kind}.{key}.{resource_id} must be positive.")
        bindings = definition.get("process_bindings", [])
        if (
            not isinstance(bindings, list)
            or len(bindings) != len(set(bindings))
            or not set(bindings).issubset(allowed_processes)
        ):
            raise EntityCatalogError(f"Starter override '{kind}' has invalid process bindings.")


def resolve_catalogue_entity(catalogue: dict[str, Any], entity_id: str) -> dict[str, Any]:
    for entity in catalogue["entities"]:
        if entity["id"] == entity_id:
            return entity
    raise KeyError(entity_id)

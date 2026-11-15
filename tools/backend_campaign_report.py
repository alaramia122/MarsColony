"""Print the deterministic backend campaign result for a scenario."""
import json

from backend.app.domain.engine import advance_period, initial_state
from backend.app.scenarios import load_scenario


def main() -> None:
    scenario = load_scenario("ares-01")
    state = initial_state(scenario)
    periods = []
    for period_number in range(1, int(scenario["time"]["period_count"]) + 1):
        state, report = advance_period(scenario, state, period_number)
        periods.append({
            "period": period_number,
            "event_id": report["event_id"],
            "oxygen_output_kg": report["oxygen_output_kg"],
            "food_output_kg": report["food_output_kg"],
            "water_output_kg": report["water_output_kg"],
            "energy_margin_kwh": report["energy_margin_kwh"],
            "battery_soc_kwh": state["battery_soc_kwh"],
            "essential_demands_met": report["essential_demands_met"],
            "all_electrical_loads_served": report["all_electrical_loads_served"],
        })
    print(json.dumps({
        "scenario_id": scenario["scenario_id"],
        "scenario_version": scenario["version"],
        "final_state": state,
        "periods": periods,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

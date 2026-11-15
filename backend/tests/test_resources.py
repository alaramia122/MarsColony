import unittest

from backend.app.domain.resources import (
    ResourceModelError,
    ResourceStock,
    execute_process,
)


class ResourceProcessTests(unittest.TestCase):
    def setUp(self):
        self.stocks = {
            "ice": ResourceStock(1000.0, 1200.0),
            "water": ResourceStock(100.0, 500.0),
            "filter": ResourceStock(8.0, 10.0),
        }

    def test_process_balances_inputs_outputs_and_energy(self):
        result, report = execute_process(
            self.stocks,
            {"ice": 2.0, "filter": 0.01},
            {"water": 1.5},
            requested_cycles=100.0,
            energy_kwh_per_cycle=3.0,
            energy_available_kwh=240.0,
        )
        self.assertAlmostEqual(report["completed_cycles"], 80.0)
        self.assertAlmostEqual(result["ice"].quantity, 840.0)
        self.assertAlmostEqual(result["filter"].quantity, 7.2)
        self.assertAlmostEqual(result["water"].quantity, 220.0)
        self.assertAlmostEqual(report["energy_used_kwh"], 240.0)
        self.assertEqual(report["limiting_constraints"], ["energy"])

    def test_storage_capacity_limits_production(self):
        result, report = execute_process(
            self.stocks, {"ice": 1.0}, {"water": 2.0}, 300.0
        )
        self.assertAlmostEqual(report["completed_cycles"], 200.0)
        self.assertEqual(report["limiting_constraints"], ["capacity:water"])
        self.assertAlmostEqual(result["water"].quantity, 500.0)

    def test_input_shortage_is_reported_without_negative_stock(self):
        result, report = execute_process(
            self.stocks, {"filter": 2.0}, {"water": 1.0}, 20.0
        )
        self.assertEqual(report["completed_cycles"], 4.0)
        self.assertEqual(report["limiting_constraints"], ["input:filter"])
        self.assertEqual(result["filter"].quantity, 0.0)

    def test_process_reports_energy_without_explicit_dispatch_limit(self):
        _, report = execute_process(
            self.stocks, {"ice": 1.0}, {"water": 1.0}, 10.0,
            energy_kwh_per_cycle=2.5,
        )
        self.assertEqual(report["energy_used_kwh"], 25.0)

    def test_input_stock_mapping_is_not_mutated(self):
        before = dict(self.stocks)
        execute_process(self.stocks, {"ice": 1.0}, {"water": 1.0}, 10.0)
        self.assertEqual(self.stocks, before)

    def test_invalid_or_missing_resource_data_is_rejected(self):
        with self.assertRaises(ResourceModelError):
            execute_process(self.stocks, {"unknown": 1.0}, {"water": 1.0}, 1.0)
        with self.assertRaises(ResourceModelError):
            execute_process(self.stocks, {"ice": -1.0}, {"water": 1.0}, 1.0)
        with self.assertRaises(ResourceModelError):
            execute_process(
                {"water": ResourceStock(10.0, 5.0)},
                {"ice": 1.0},
                {"water": 1.0},
                1.0,
            )


if __name__ == "__main__":
    unittest.main()

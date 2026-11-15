import unittest

from backend.app.domain.energy import EnergyModelError, PowerLoad, dispatch_power


class PowerDispatchTests(unittest.TestCase):
    def test_critical_loads_are_served_before_flexible_loads(self):
        result = dispatch_power(
            36.0,
            [
                PowerLoad("research", 8.0, priority=3),
                PowerLoad("life-support", 24.0, priority=0, minimum_kw=20.0),
                PowerLoad("habitat", 12.0, priority=1, minimum_kw=10.0),
            ],
            duration_hours=48.0,
        )
        allocations = {item["load_id"]: item for item in result["loads"]}
        self.assertEqual(allocations["life-support"]["allocated_kw"], 24.0)
        self.assertEqual(allocations["habitat"]["allocated_kw"], 12.0)
        self.assertEqual(allocations["research"]["allocated_kw"], 0.0)
        self.assertEqual(result["allocated_kwh"], 1728.0)
        self.assertFalse(result["all_loads_served"])

    def test_partial_load_below_minimum_is_shed(self):
        result = dispatch_power(
            5.0, [PowerLoad("pump", 8.0, priority=0, minimum_kw=6.0)]
        )
        self.assertEqual(result["loads"][0]["allocated_kw"], 0.0)
        self.assertEqual(result["loads"][0]["shed_kw"], 8.0)

    def test_ties_are_resolved_by_identifier(self):
        result = dispatch_power(
            5.0,
            [PowerLoad("zeta", 5.0, 1), PowerLoad("alpha", 5.0, 1)],
        )
        self.assertEqual(result["loads"][0]["load_id"], "alpha")
        self.assertEqual(result["loads"][0]["allocated_kw"], 5.0)

    def test_invalid_loads_are_rejected(self):
        with self.assertRaises(EnergyModelError):
            dispatch_power(-1.0, [])
        with self.assertRaises(EnergyModelError):
            dispatch_power(10.0, [PowerLoad("bad", 5.0, 0, 6.0)])


if __name__ == "__main__":
    unittest.main()

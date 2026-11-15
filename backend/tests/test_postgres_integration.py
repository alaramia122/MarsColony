"""Integration checks against the PostgreSQL service configured by CI."""
import os
import unittest

from sqlalchemy import delete

from backend.app import api
from backend.app.api import AdvancePeriodRequest, CreateSessionRequest
from backend.app.models import GameEventRecord, GameSessionRecord


RUN_POSTGRES_TESTS = os.getenv("MARS_COLONY_POSTGRES_TESTS") == "1"


@unittest.skipUnless(RUN_POSTGRES_TESTS, "PostgreSQL integration tests run in CI.")
class PostgreSQLSessionTests(unittest.TestCase):
    def test_session_survives_database_round_trip_and_full_campaign(self):
        created = api.create_session(CreateSessionRequest(scenario_id="ares-01"))
        session_id = created["id"]
        try:
            self.assertEqual(created["current_period"], 0)
            for expected_period in range(1, 5):
                advanced = api.advance_game_session(
                    session_id,
                    AdvancePeriodRequest(expected_period=expected_period - 1),
                )
                self.assertEqual(advanced["current_period"], expected_period)

            restored = api.get_session(session_id)
            self.assertEqual(restored["current_period"], 4)
            self.assertEqual(len(restored["events"]), 5)
            self.assertEqual(restored["events"][0]["type"], "session.created")
            self.assertTrue(
                all(event["type"] == "period.completed" for event in restored["events"][1:])
            )
            self.assertAlmostEqual(restored["state"]["oxygen_kg"], 364.30224, places=5)
            self.assertAlmostEqual(restored["state"]["food_solids_kg"], 322.81832, places=5)
            self.assertAlmostEqual(restored["state"]["usable_water_kg"], 2867.11356, places=4)
        finally:
            with api.SessionLocal.begin() as db:
                db.execute(
                    delete(GameEventRecord).where(GameEventRecord.session_id == session_id)
                )
                db.execute(
                    delete(GameSessionRecord).where(GameSessionRecord.id == session_id)
                )


if __name__ == "__main__":
    unittest.main()

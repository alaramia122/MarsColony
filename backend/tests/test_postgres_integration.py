"""Integration checks against the PostgreSQL service configured by CI."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException
from sqlalchemy import delete

from backend.app import api
from backend.app.api import AdvancePeriodRequest, CreateSessionRequest
from backend.app.models import GameEventRecord, GameSessionRecord


RUN_POSTGRES_TESTS = os.getenv("MARS_COLONY_POSTGRES_TESTS") == "1"


@unittest.skipUnless(RUN_POSTGRES_TESTS, "PostgreSQL integration tests run in CI.")
class PostgreSQLSessionTests(unittest.TestCase):
    def test_concurrent_advance_commands_cannot_skip_a_period(self):
        created = api.create_session(CreateSessionRequest(scenario_id="ares-01"))
        session_id = created["id"]
        owner_token = created["owner_token"]
        try:
            def advance_once(_index):
                try:
                    api.advance_game_session(
                        session_id,
                        AdvancePeriodRequest(expected_period=0),
                        player_token=owner_token,
                    )
                    return "advanced"
                except HTTPException as error:
                    return error.status_code

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(advance_once, range(2)))

            self.assertEqual(outcomes.count("advanced"), 1)
            self.assertEqual(outcomes.count(409), 1)
            restored = api.get_session(session_id, owner_token)
            self.assertEqual(restored["current_period"], 1)
            self.assertEqual(len(restored["events"]), 2)
        finally:
            with api.SessionLocal.begin() as db:
                db.execute(
                    delete(GameEventRecord).where(GameEventRecord.session_id == session_id)
                )
                db.execute(
                    delete(GameSessionRecord).where(GameSessionRecord.id == session_id)
                )

    def test_session_survives_database_round_trip_and_full_campaign(self):
        created = api.create_session(CreateSessionRequest(scenario_id="ares-01"))
        session_id = created["id"]
        owner_token = created["owner_token"]
        try:
            self.assertEqual(created["current_period"], 0)
            for expected_period in range(1, 5):
                advanced = api.advance_game_session(
                    session_id,
                    AdvancePeriodRequest(expected_period=expected_period - 1),
                    player_token=owner_token,
                )
                self.assertEqual(advanced["current_period"], expected_period)

            restored = api.get_session(session_id, owner_token)
            self.assertEqual(restored["current_period"], 4)
            self.assertEqual(len(restored["events"]), 5)
            self.assertEqual(restored["events"][0]["type"], "session.created")
            self.assertTrue(
                all(event["type"] == "period.completed" for event in restored["events"][1:])
            )
            state = restored["state"]
            self.assertGreaterEqual(state["oxygen_kg"], 200.0)
            self.assertGreaterEqual(state["food_solids_kg"], 250.0)
            self.assertGreaterEqual(state["usable_water_kg"], 1000.0)
            self.assertGreaterEqual(state["budget_credits"], 100.0)
            self.assertGreaterEqual(state["raw_ice_kg"], 0.0)
            self.assertGreaterEqual(state["battery_soc_kwh"], 0.0)
            self.assertLessEqual(state["battery_soc_kwh"], 1000.0)
            self.assertTrue(
                all(event["payload"]["essential_demands_met"] for event in restored["events"][1:])
            )
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

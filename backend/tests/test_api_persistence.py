import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import api
from backend.app.db import Base
from backend.app.api import AdvancePeriodRequest, CreateSessionRequest
from backend.app.models import GameEventRecord


class SessionPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False
        )
        self.original_session_factory = api.SessionLocal
        self.original_scenario_loader = api.load_scenario
        api.SessionLocal = self.session_factory

    def tearDown(self):
        api.SessionLocal = self.original_session_factory
        api.load_scenario = self.original_scenario_loader
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_invalid_scenario_is_rejected_before_persistence(self):
        from backend.app.scenarios import ScenarioValidationError

        def invalid_scenario(_scenario_id):
            raise ScenarioValidationError("Invalid test scenario.")

        api.load_scenario = invalid_scenario
        with self.assertRaises(HTTPException) as error:
            api.create_session(CreateSessionRequest(scenario_id="ares-01"))
        self.assertEqual(error.exception.status_code, 422)

    def test_session_state_and_period_history_are_persisted(self):
        created = api.create_session(CreateSessionRequest(scenario_id="ares-01"))
        self.assertEqual(created["current_period"], 0)
        session_id = created["id"]

        first_period = api.advance_game_session(
            session_id, AdvancePeriodRequest(expected_period=0)
        )
        self.assertEqual(first_period["current_period"], 1)
        self.assertEqual(first_period["report"]["event_id"], "EVT-O2-ISRU-DEGRADATION")

        restored = api.get_session(session_id)
        self.assertEqual(restored["current_period"], 1)
        self.assertEqual(len(restored["events"]), 2)
        self.assertEqual(restored["events"][0]["type"], "session.created")
        self.assertEqual(restored["events"][1]["type"], "period.completed")

    def test_session_snapshot_is_used_after_scenario_file_changes(self):
        created = api.create_session(CreateSessionRequest(scenario_id="ares-01"))
        session_id = created["id"]

        def scenario_is_unavailable(_scenario_id):
            raise FileNotFoundError("Scenario file has changed or moved.")

        api.load_scenario = scenario_is_unavailable
        advanced = api.advance_game_session(session_id)
        self.assertEqual(advanced["current_period"], 1)

    def test_stale_expected_period_is_rejected_without_mutating_session(self):
        created = api.create_session(CreateSessionRequest(scenario_id="ares-01"))
        session_id = created["id"]
        api.advance_game_session(session_id, AdvancePeriodRequest(expected_period=0))

        with self.assertRaises(HTTPException) as error:
            api.advance_game_session(session_id, AdvancePeriodRequest(expected_period=0))
        self.assertEqual(error.exception.status_code, 409)

        restored = api.get_session(session_id)
        self.assertEqual(restored["current_period"], 1)
        self.assertEqual(len(restored["events"]), 2)

    def test_failed_event_insert_rolls_back_period_state(self):
        created = api.create_session(CreateSessionRequest(scenario_id="ares-01"))
        session_id = created["id"]
        original_state = dict(created["state"])
        with self.session_factory.begin() as db:
            db.add(GameEventRecord(
                session_id=session_id,
                sequence=1,
                event_type="test.conflict",
                payload={"purpose": "force unique sequence conflict"},
            ))

        with self.assertRaises(HTTPException) as error:
            api.advance_game_session(
                session_id,
                AdvancePeriodRequest(expected_period=0),
            )
        self.assertEqual(error.exception.status_code, 503)

        restored = api.get_session(session_id)
        self.assertEqual(restored["current_period"], 0)
        self.assertEqual(restored["state"], original_state)
        self.assertEqual(
            [event["sequence"] for event in restored["events"]],
            [0, 1],
        )

    def test_session_reaches_scenario_end_and_rejects_extra_period(self):
        created = api.create_session(CreateSessionRequest(scenario_id="ares-01"))
        session_id = created["id"]
        for expected_period in range(1, 5):
            result = api.advance_game_session(
                session_id, AdvancePeriodRequest(expected_period=expected_period - 1)
            )
            self.assertEqual(result["current_period"], expected_period)

        with self.assertRaises(HTTPException) as error:
            api.advance_game_session(session_id)
        self.assertEqual(error.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()

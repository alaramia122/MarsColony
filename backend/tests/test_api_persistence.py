import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import api
from backend.app.db import Base
from backend.app.api import CreateSessionRequest


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
        api.SessionLocal = self.session_factory

    def tearDown(self):
        api.SessionLocal = self.original_session_factory
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_session_state_and_period_history_are_persisted(self):
        created = api.create_session(CreateSessionRequest(scenario_id="ares-01"))
        self.assertEqual(created["current_period"], 0)
        session_id = created["id"]

        first_period = api.advance_game_session(session_id)
        self.assertEqual(first_period["current_period"], 1)
        self.assertEqual(first_period["report"]["event_id"], "EVT-O2-ISRU-DEGRADATION")

        restored = api.get_session(session_id)
        self.assertEqual(restored["current_period"], 1)
        self.assertEqual(len(restored["events"]), 2)
        self.assertEqual(restored["events"][0]["type"], "session.created")
        self.assertEqual(restored["events"][1]["type"], "period.completed")

    def test_session_reaches_scenario_end_and_rejects_extra_period(self):
        created = api.create_session(CreateSessionRequest(scenario_id="ares-01"))
        session_id = created["id"]
        for expected_period in range(1, 5):
            result = api.advance_game_session(session_id)
            self.assertEqual(result["current_period"], expected_period)

        with self.assertRaises(HTTPException) as error:
            api.advance_game_session(session_id)
        self.assertEqual(error.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()

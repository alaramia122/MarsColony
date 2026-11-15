import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import api, realtime
from backend.app.db import Base
from backend.app.main import app
from backend.app.api import CreateSessionRequest


class WebSocketStreamTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False
        )
        self.old_api_factory = api.SessionLocal
        self.old_stream_factory = realtime.SessionLocal
        api.SessionLocal = self.factory
        realtime.SessionLocal = self.factory
        self.client = TestClient(app)
        self.session = api.create_session(CreateSessionRequest(scenario_id="ares-01"))

    def tearDown(self):
        api.SessionLocal = self.old_api_factory
        realtime.SessionLocal = self.old_stream_factory
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_stream_authenticates_and_sends_authoritative_snapshot(self):
        with self.client.websocket_connect(
            f"/api/v1/sessions/{self.session['id']}/stream"
        ) as websocket:
            websocket.send_json({
                "type": "authenticate",
                "player_token": self.session["owner_token"],
            })
            message = websocket.receive_json()
            self.assertEqual(message["type"], "snapshot")
            self.assertEqual(message["session"]["id"], self.session["id"])
            self.assertEqual(message["session"]["current_period"], 0)
            self.assertEqual(message["player"]["id"], self.session["owner_player_id"])
            self.assertEqual(message["last_sequence"], 0)

    def test_stream_delivers_event_after_database_commit(self):
        with self.client.websocket_connect(
            f"/api/v1/sessions/{self.session['id']}/stream"
        ) as websocket:
            websocket.send_json({
                "type": "authenticate",
                "player_token": self.session["owner_token"],
            })
            snapshot = websocket.receive_json()
            self.assertEqual(snapshot["type"], "snapshot")
            api.advance_game_session(
                self.session["id"],
                api.AdvancePeriodRequest(expected_period=0),
                player_token=self.session["owner_token"],
            )
            event = websocket.receive_json()
            self.assertEqual(event["type"], "event")
            self.assertEqual(event["sequence"], 1)
            self.assertEqual(event["event_type"], "period.completed")

    def test_stream_rejects_invalid_session_token(self):
        with self.client.websocket_connect(
            f"/api/v1/sessions/{self.session['id']}/stream"
        ) as websocket:
            websocket.send_json({
                "type": "authenticate",
                "player_token": "not-a-valid-token",
            })
            with self.assertRaises(Exception):
                websocket.receive_json()


if __name__ == "__main__":
    unittest.main()

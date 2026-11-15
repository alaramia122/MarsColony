import asyncio
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.websockets import WebSocketDisconnect

from backend.app import api, realtime
from backend.app.db import Base
from backend.app.api import CreateSessionRequest


class FakeWebSocket:
    def __init__(self, token, after_snapshot=None):
        self.message = {"type": "authenticate", "player_token": token}
        self.after_snapshot = after_snapshot
        self.sent = []
        self.close_code = None

    async def accept(self):
        return None

    async def receive_json(self):
        return self.message

    async def send_json(self, payload):
        self.sent.append(payload)
        if payload["type"] == "snapshot":
            if self.after_snapshot:
                self.after_snapshot()
            else:
                raise WebSocketDisconnect(code=1000)
        elif payload["type"] == "event":
            raise WebSocketDisconnect(code=1000)

    async def close(self, code, reason):
        self.close_code = code


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
        self.session = api.create_session(CreateSessionRequest(scenario_id="ares-01"))

    def tearDown(self):
        api.SessionLocal = self.old_api_factory
        realtime.SessionLocal = self.old_stream_factory
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_stream_authenticates_and_sends_authoritative_snapshot(self):
        websocket = FakeWebSocket(self.session["owner_token"])
        asyncio.run(realtime.session_stream(websocket, self.session["id"]))
        self.assertEqual(websocket.sent[0]["type"], "snapshot")
        self.assertEqual(websocket.sent[0]["session"]["id"], self.session["id"])
        self.assertEqual(websocket.sent[0]["session"]["current_period"], 0)
        self.assertEqual(websocket.sent[0]["player"]["id"], self.session["owner_player_id"])
        self.assertEqual(websocket.sent[0]["last_sequence"], 0)

    def test_stream_delivers_event_after_database_commit(self):
        def commit_period():
            api.advance_game_session(
                self.session["id"],
                api.AdvancePeriodRequest(expected_period=0),
                player_token=self.session["owner_token"],
            )

        websocket = FakeWebSocket(self.session["owner_token"], after_snapshot=commit_period)
        asyncio.run(realtime.session_stream(websocket, self.session["id"]))
        self.assertEqual(websocket.sent[1]["type"], "event")
        self.assertEqual(websocket.sent[1]["sequence"], 1)
        self.assertEqual(websocket.sent[1]["event_type"], "period.completed")

    def test_stream_rejects_invalid_session_token(self):
        websocket = FakeWebSocket("not-a-valid-token")
        asyncio.run(realtime.session_stream(websocket, self.session["id"]))
        self.assertEqual(websocket.close_code, 4401)
        self.assertEqual(websocket.sent, [])


if __name__ == "__main__":
    unittest.main()

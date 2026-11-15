"""Authenticated WebSocket stream for committed session events."""
import asyncio
import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from .api import serialize_session
from .db import SessionLocal
from .models import GameEventRecord, GameSessionRecord, PlayerRecord

router = APIRouter()


@router.websocket("/api/v1/sessions/{session_id}/stream")
async def session_stream(websocket: WebSocket, session_id: str) -> None:
    """Authenticate in-band, send a full snapshot, then stream committed events.

    Authentication is the first WebSocket message so browser clients do not need
    to put bearer credentials in URLs, where proxies commonly log them.
    """
    await websocket.accept()
    try:
        try:
            message = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        except (asyncio.TimeoutError, ValueError):
            await websocket.close(code=4401, reason="Authentication required")
            return

        if not isinstance(message, dict) or message.get("type") != "authenticate":
            await websocket.close(code=4401, reason="Authentication required")
            return
        token = message.get("player_token")
        if not isinstance(token, str) or not token:
            await websocket.close(code=4401, reason="Invalid credentials")
            return

        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with SessionLocal() as db:
            session = db.get(GameSessionRecord, session_id)
            if session is None:
                await websocket.close(code=4404, reason="Session not found")
                return
            player = db.scalar(select(PlayerRecord).where(
                PlayerRecord.session_id == session_id,
                PlayerRecord.token_hash == digest,
            ))
            if player is None:
                await websocket.close(code=4401, reason="Invalid credentials")
                return

            # Read the event watermark before the snapshot. A transaction committed
            # after this query is delivered as an event, even if its state is already
            # visible in the snapshot; clients treat events as refresh notifications.
            last_sequence = db.scalar(
                select(func.max(GameEventRecord.sequence)).where(
                    GameEventRecord.session_id == session_id
                )
            )
            last_sequence = -1 if last_sequence is None else int(last_sequence)
            snapshot = serialize_session(session)
            player_payload = {
                "id": player.id,
                "display_name": player.display_name,
                "roles": player.roles or [],
                "responsibility_domains": player.responsibility_domains or [],
            }

        await websocket.send_json({
            "type": "snapshot",
            "session": snapshot,
            "player": player_payload,
            "last_sequence": last_sequence,
        })

        last_heartbeat = asyncio.get_running_loop().time()
        while True:
            await asyncio.sleep(0.75)
            with SessionLocal() as db:
                events = db.scalars(select(GameEventRecord).where(
                    GameEventRecord.session_id == session_id,
                    GameEventRecord.sequence > last_sequence,
                ).order_by(GameEventRecord.sequence).limit(250)).all()
                payloads = [{
                    "type": "event",
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "payload": event.payload,
                    "created_at": event.created_at.isoformat()
                    if event.created_at else None,
                } for event in events]

            for event in payloads:
                await websocket.send_json(event)
                last_sequence = event["sequence"]

            now = asyncio.get_running_loop().time()
            if now - last_heartbeat >= 15:
                await websocket.send_json({
                    "type": "heartbeat",
                    "last_sequence": last_sequence,
                    "server_time": datetime.now(timezone.utc).isoformat(),
                })
                last_heartbeat = now
    except WebSocketDisconnect:
        return
    except SQLAlchemyError:
        try:
            await websocket.close(code=1011, reason="Session storage unavailable")
        except RuntimeError:
            return

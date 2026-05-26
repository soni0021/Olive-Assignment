"""Read + write endpoints for the chat UI's persistence needs.

Distinct from `analytics.py` (which only reads aggregations over inference_logs).
This router owns:
- `POST /v1/users`               — upsert by username (claim-any-name model)
- `GET  /v1/users/{username}`    — lookup
- `POST /v1/sessions`            — create session for a user
- `GET  /v1/users/{uid}/sessions` — list a user's sessions ordered by recency
- `GET  /v1/sessions/{sid}`      — fetch one session + its messages
- `POST /v1/sessions/{sid}/messages` — append a message

All writes happen directly against Postgres (no Redis indirection) — message
volume is 1–2 per chat turn, not per token, so the broker hop would add
latency without absorbing real load.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from api.db import engine

logger = logging.getLogger("chat_store")

router = APIRouter()


# ---------- request/response models ----------


class UserIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)


class User(BaseModel):
    id: UUID
    username: str
    created_at: str
    last_seen_at: str


class SessionIn(BaseModel):
    user_id: UUID
    title: str | None = Field(default=None, max_length=255)


class Session(BaseModel):
    id: UUID
    user_id: UUID | None
    title: str | None
    created_at: str
    updated_at: str
    message_count: int = 0
    preview: str | None = None


class MessageIn(BaseModel):
    role: str = Field(pattern=r"^(user|assistant|system|tool)$")
    content: str = Field(max_length=200_000)


class Message(BaseModel):
    id: UUID
    session_id: UUID
    role: str
    content: str
    created_at: str


class SessionWithMessages(Session):
    messages: list[Message] = []


# ---------- users ----------


@router.post("/v1/users", response_model=User)
async def upsert_user(body: UserIn) -> dict[str, Any]:
    """Claim-any-name semantics: insert on first sight, return existing on conflict."""
    sql = text(
        """
        INSERT INTO users (username) VALUES (:u)
        ON CONFLICT (username) DO UPDATE SET last_seen_at = now()
        RETURNING id, username, created_at, last_seen_at
        """
    )
    async with engine().begin() as conn:
        row = (await conn.execute(sql, {"u": body.username.strip()})).mappings().one()
    return {
        "id": row["id"],
        "username": row["username"],
        "created_at": row["created_at"].isoformat(),
        "last_seen_at": row["last_seen_at"].isoformat(),
    }


@router.get("/v1/users/{username}", response_model=User)
async def get_user(username: str) -> dict[str, Any]:
    sql = text(
        "SELECT id, username, created_at, last_seen_at FROM users "
        "WHERE LOWER(username) = LOWER(:u)"
    )
    async with engine().connect() as conn:
        row = (await conn.execute(sql, {"u": username})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")
    return {
        "id": row["id"],
        "username": row["username"],
        "created_at": row["created_at"].isoformat(),
        "last_seen_at": row["last_seen_at"].isoformat(),
    }


# ---------- sessions ----------


@router.post("/v1/sessions", response_model=Session)
async def create_session(body: SessionIn) -> dict[str, Any]:
    sql = text(
        """
        INSERT INTO sessions (user_id, title)
        VALUES (:uid, :title)
        RETURNING id, user_id, title, created_at, updated_at
        """
    )
    async with engine().begin() as conn:
        row = (await conn.execute(sql, {"uid": body.user_id, "title": body.title})).mappings().one()
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "title": row["title"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "message_count": 0,
        "preview": None,
    }


@router.get("/v1/users/{user_id}/sessions", response_model=list[Session])
async def list_sessions(user_id: UUID, limit: int = 50) -> list[dict[str, Any]]:
    """Return user's sessions ordered by recency, with last-message preview.

    The query joins messages with a LATERAL subselect to grab only the most
    recent row per session — avoids loading the full transcript. Index on
    messages.created_at keeps this <50ms for normal usage.
    """
    sql = text(
        """
        SELECT
            s.id,
            s.user_id,
            s.title,
            s.created_at,
            s.updated_at,
            COALESCE(mc.n, 0) AS message_count,
            mp.content AS preview
        FROM sessions s
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS n FROM messages WHERE session_id = s.id
        ) mc ON true
        LEFT JOIN LATERAL (
            SELECT content FROM messages
            WHERE session_id = s.id
            ORDER BY created_at DESC
            LIMIT 1
        ) mp ON true
        WHERE s.user_id = :uid
        ORDER BY s.updated_at DESC
        LIMIT :lim
        """
    )
    async with engine().connect() as conn:
        rows = (await conn.execute(sql, {"uid": user_id, "lim": limit})).mappings().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        preview = r["preview"] or ""
        out.append(
            {
                "id": r["id"],
                "user_id": r["user_id"],
                "title": r["title"],
                "created_at": r["created_at"].isoformat(),
                "updated_at": r["updated_at"].isoformat(),
                "message_count": r["message_count"],
                "preview": preview[:160] if preview else None,
            }
        )
    return out


@router.get("/v1/sessions/{session_id}", response_model=SessionWithMessages)
async def get_session(session_id: UUID) -> dict[str, Any]:
    session_sql = text(
        "SELECT id, user_id, title, created_at, updated_at FROM sessions WHERE id = :sid"
    )
    messages_sql = text(
        "SELECT id, session_id, role, content, created_at FROM messages "
        "WHERE session_id = :sid ORDER BY created_at ASC"
    )
    async with engine().connect() as conn:
        srow = (await conn.execute(session_sql, {"sid": session_id})).mappings().first()
        if srow is None:
            raise HTTPException(status_code=404, detail="session not found")
        mrows = (await conn.execute(messages_sql, {"sid": session_id})).mappings().all()

    messages = [
        {
            "id": m["id"],
            "session_id": m["session_id"],
            "role": m["role"],
            "content": m["content"],
            "created_at": m["created_at"].isoformat(),
        }
        for m in mrows
    ]
    return {
        "id": srow["id"],
        "user_id": srow["user_id"],
        "title": srow["title"],
        "created_at": srow["created_at"].isoformat(),
        "updated_at": srow["updated_at"].isoformat(),
        "message_count": len(messages),
        "preview": messages[-1]["content"][:160] if messages else None,
        "messages": messages,
    }


@router.post("/v1/sessions/{session_id}/messages", response_model=Message)
async def append_message(session_id: UUID, body: MessageIn) -> dict[str, Any]:
    insert_msg = text(
        """
        INSERT INTO messages (session_id, role, content)
        VALUES (:sid, :role, :content)
        RETURNING id, session_id, role, content, created_at
        """
    )
    bump_session = text("UPDATE sessions SET updated_at = now() WHERE id = :sid")
    # Auto-title from the first user message so the sidebar shows something useful.
    autotitle = text(
        """
        UPDATE sessions
        SET title = LEFT(:content, 60)
        WHERE id = :sid AND title IS NULL AND :role = 'user'
        """
    )
    async with engine().begin() as conn:
        row = (
            await conn.execute(
                insert_msg,
                {"sid": session_id, "role": body.role, "content": body.content},
            )
        ).mappings().one()
        await conn.execute(bump_session, {"sid": session_id})
        await conn.execute(autotitle, {"sid": session_id, "role": body.role, "content": body.content})
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "content": row["content"],
        "created_at": row["created_at"].isoformat(),
    }

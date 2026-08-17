# app/chat_ws.py
from __future__ import annotations

import json
from typing import Dict, Set, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import jwt, JWTError
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, func

from app.db import engine
from app.auth import JWT_SECRET, JWT_ALG, CurrentUser
from app.models import User, RolePermission, Chat, ChatMember

ws_router = APIRouter()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def load_permissions(db, role_key: str) -> set[str]:
    rows = db.scalars(select(RolePermission).where(RolePermission.role_key == role_key)).all()
    return {r.perm_key for r in rows}


def user_from_token(db, token: str) -> Optional[CurrentUser]:
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        uid = payload.get("sub")
        if not uid:
            return None
        u = db.scalar(select(User).where(User.id == uid))
        if not u:
            return None
        perms = load_permissions(db, u.role_key)
        return CurrentUser(id=u.id, role_key=u.role_key, department_id=u.department_id, section_id=u.section_id, perms=perms)
    except JWTError:
        return None


def can_access_chat(db, user: CurrentUser, c: Chat) -> bool:
    if getattr(c, "is_super_admin", False):
        return "admin.manage" in (user.perms or set())
    member_count = int(db.scalar(select(func.count()).select_from(ChatMember).where(ChatMember.chat_id == c.id)) or 0)
    if member_count:
        return db.scalar(select(ChatMember).where(ChatMember.chat_id == c.id, ChatMember.user_id == user.id)) is not None
    if "ticket.view_all" in (user.perms or set()):
        return True
    return (c.department_id == user.department_id) and (c.section_id == user.section_id)


class WSManager:
    def __init__(self):
        self.rooms: Dict[int, Set[WebSocket]] = {}

    async def connect(self, chat_id: int, ws: WebSocket):
        await ws.accept()
        self.rooms.setdefault(chat_id, set()).add(ws)

    def disconnect(self, chat_id: int, ws: WebSocket):
        if chat_id in self.rooms:
            self.rooms[chat_id].discard(ws)
            if not self.rooms[chat_id]:
                del self.rooms[chat_id]

    async def broadcast(self, chat_id: int, data: dict):
        conns = list(self.rooms.get(chat_id, set()))
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(chat_id, ws)


ws_manager = WSManager()


@ws_router.websocket("/api/v1/ws/chat/{chat_id}")
async def ws_chat(ws: WebSocket, chat_id: int):
    token = ws.query_params.get("token", "")
    db = SessionLocal()
    try:
        user = user_from_token(db, token)
        if not user:
            await ws.close(code=4401)
            return

        c = db.scalar(select(Chat).where(Chat.id == chat_id))
        if not c:
            await ws.close(code=4404)
            return

        if not can_access_chat(db, user, c):
            await ws.close(code=4403)
            return

        await ws_manager.connect(chat_id, ws)

        while True:
            raw = await ws.receive_text()
            if raw == "ping":
                continue

            try:
                data = json.loads(raw)
            except Exception:
                continue

            if data.get("type") == "typing":
                await ws_manager.broadcast(chat_id, {
                    "type": "typing",
                    "chat_id": chat_id,
                    "user_id": user.id,
                })

    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(chat_id, ws)
        db.close()

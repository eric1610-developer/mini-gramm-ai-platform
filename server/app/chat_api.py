# app/chat_api.py
from __future__ import annotations

import os
import anyio
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import db_session
from app.auth import get_current_user, CurrentUser, require_perm
from app.models import Chat, Message, ChatReadState, ChatMember, MessageAttachment, User
from app.chat_ws import ws_manager
from app.chat_models import (
    ChatCreateIn, ChatOut,
    MessageCreateIn, MessageOut,
    ChatWithMessagesOut, AttachmentOut,
)

from pydantic import BaseModel
from typing import Dict

router = APIRouter(prefix="/api/v1", tags=["chat"])
# а сами endpoints начинай с /chats...

SUPER_ADMIN_USER_ID = os.getenv("SUPER_ADMIN_USER_ID", "DEMO-ADMIN")
SUPER_ADMIN_CHAT_TITLE = os.getenv("SUPER_ADMIN_CHAT_TITLE", "Super Admin Chat")


def is_admin(user: CurrentUser) -> bool:
    return (user.role_key == "ADMIN") or ("admin.manage" in user.perms)


def is_super_admin_user(user: CurrentUser) -> bool:
    return user.id == SUPER_ADMIN_USER_ID


def _can_access_chat(c: Chat, user: CurrentUser, db: Session | None = None) -> bool:
    if getattr(c, "is_super_admin", False):
        return "admin.manage" in user.perms
    if db is not None:
        member_count = int(db.scalar(select(func.count()).select_from(ChatMember).where(ChatMember.chat_id == c.id)) or 0)
        if member_count:
            return db.scalar(select(ChatMember).where(ChatMember.chat_id == c.id, ChatMember.user_id == user.id)) is not None
    if is_admin(user):
        return True
    return (c.department_id == user.department_id) and (c.section_id == user.section_id)


def _can_write_chat(c: Chat, user: CurrentUser) -> bool:
    # Read-only chats: only the configured demo/system administrator can write.
    if getattr(c, "is_read_only", 0):
        return user.id == SUPER_ADMIN_USER_ID

    # 🔐 Super Admin чат: только admin.manage
    if getattr(c, "is_super_admin", False) and "admin.manage" not in user.perms:
        return False

    # 📢 Announcement чат: admin.manage или HEAD
    if getattr(c, "is_announcement", False):
        if "admin.manage" not in user.perms and user.role_key != "HEAD":
            return False

    return True

def _chat_out(c: Chat, user: CurrentUser, db: Session | None = None) -> ChatOut:
    unread = 0
    if db is not None:
        state = db.scalar(select(ChatReadState).where(ChatReadState.chat_id == c.id, ChatReadState.user_id == user.id))
        last_read = int(state.last_read_message_id or 0) if state else 0
        unread = int(db.scalar(select(func.count()).select_from(Message).where(Message.chat_id == c.id, Message.id > last_read, Message.user_id != user.id)) or 0)
    display_title = c.title
    if db is not None and c.department_id == "PRIVATE" and c.section_id == "DIRECT":
        other_id = db.scalar(select(ChatMember.user_id).where(ChatMember.chat_id == c.id, ChatMember.user_id != user.id).limit(1))
        other = db.get(User, other_id) if other_id else None
        if other:
            display_title = other.full_name or other.id
    return ChatOut(id=c.id, title=display_title, department_id=c.department_id, section_id=c.section_id,
                   created_by=c.created_by, created_at=c.created_at, accessible=_can_access_chat(c, user, db), unread_count=unread)


def _msg_out(m: Message, db: Session | None = None) -> MessageOut:
    attachments = []
    if db is not None:
        rows = db.scalars(select(MessageAttachment).where(MessageAttachment.message_id == m.id)).all()
        attachments = [AttachmentOut(id=a.id, name=a.original_name, mime_type=a.mime_type, size_bytes=a.size_bytes, url=f"/api/v1/files/{a.id}") for a in rows]
    return MessageOut(id=m.id, chat_id=m.chat_id, user_id=m.user_id, text=m.text, created_at=m.created_at, attachments=attachments)


@router.post("/chats", response_model=ChatOut)
def create_chat(
    payload: ChatCreateIn,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(get_current_user),
):
    if user.perms:
        require_perm(user, "ticket.create")

    dep = payload.department_id
    sec = payload.section_id
    if not is_admin(user):
        dep = user.department_id
        sec = user.section_id

    c = Chat(
        title=payload.title,
        department_id=dep,
        section_id=sec,
        created_by=user.id,
        is_super_admin=False,
        is_announcement=False,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _chat_out(c, user, db)


@router.get("/chats", response_model=list[ChatOut])
def list_chats(
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(get_current_user),
    limit: int = 50,
):
    q = select(Chat).order_by(Chat.created_at.desc()).limit(limit)
    rows = db.scalars(q).all()
    return [_chat_out(x, user, db) for x in rows if _can_access_chat(x, user, db)]


@router.get("/chats/{chat_id}", response_model=ChatWithMessagesOut)
def get_chat(
    chat_id: int,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(get_current_user),
):
    c = db.scalar(select(Chat).where(Chat.id == chat_id))
    if not c:
        raise HTTPException(status_code=404, detail="Chat not found")

    if not _can_access_chat(c, user, db):
        raise HTTPException(status_code=403, detail="No access to this chat")

    msgs = db.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at.asc())
    ).all()

    states = db.scalars(select(ChatReadState).where(ChatReadState.chat_id == chat_id)).all()
    read_states: Dict[str, int] = {s.user_id: int(s.last_read_message_id or 0) for s in states}

    return ChatWithMessagesOut(
        chat=_chat_out(c, user, db),
        messages=[_msg_out(m, db) for m in msgs],
        read_states=read_states,
    )


@router.post("/chats/{chat_id}/messages", response_model=MessageOut)
def send_message(
    chat_id: int,
    payload: MessageCreateIn,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(get_current_user),
):
    c = db.scalar(select(Chat).where(Chat.id == chat_id))
    if not c:
        raise HTTPException(status_code=404, detail="Chat not found")

    if not _can_access_chat(c, user, db):
        raise HTTPException(status_code=403, detail="No access to this chat")

    if not _can_write_chat(c, user):
        raise HTTPException(status_code=403, detail="This chat is read-only")

    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty message")

    m = Message(chat_id=chat_id, user_id=user.id, text=text)
    db.add(m)
    db.commit()
    db.refresh(m)

    anyio.from_thread.run(ws_manager.broadcast, chat_id, {
        "type": "message",
        "chat_id": chat_id,
        "message": _msg_out(m, db).model_dump() if hasattr(_msg_out(m, db), "model_dump") else _msg_out(m, db).dict(),
    })

    return _msg_out(m, db)


class ReadIn(BaseModel):
    last_read_id: int


@router.post("/chats/{chat_id}/read")
def mark_read(
    chat_id: int,
    payload: ReadIn,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(get_current_user),
):
    c = db.scalar(select(Chat).where(Chat.id == chat_id))
    if not c:
        raise HTTPException(status_code=404, detail="Chat not found")

    if not _can_access_chat(c, user, db):
        raise HTTPException(status_code=403, detail="No access to this chat")

    last_id = int(payload.last_read_id or 0)
    if last_id <= 0:
        return {"ok": True, "last_read_id": 0}

    st = db.scalar(
        select(ChatReadState).where(
            ChatReadState.chat_id == chat_id,
            ChatReadState.user_id == user.id,
        )
    )

    if not st:
        st = ChatReadState(chat_id=chat_id, user_id=user.id, last_read_message_id=last_id, read_at=datetime.utcnow())
        db.add(st)
    else:
        if last_id > int(st.last_read_message_id or 0):
            st.last_read_message_id = last_id
            st.read_at = datetime.utcnow()

    db.commit()

    anyio.from_thread.run(ws_manager.broadcast, chat_id, {
        "type": "read",
        "chat_id": chat_id,
        "user_id": user.id,
        "last_read_id": last_id,
    })

    return {"ok": True, "last_read_id": last_id}

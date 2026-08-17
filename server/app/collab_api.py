from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Optional

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user, hash_password
from app.chat_ws import ws_manager
from app.db import db_session
from app.models import Chat, ChatMember, Message, MessageAttachment, User, EmployeeProfile

router = APIRouter(prefix="/api/v1", tags=["collaboration"])
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/app/uploads"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "50")) * 1024 * 1024
ALLOWED_ROLES = {"WORKER", "MASTER", "ENGINEER", "HEAD", "ADMIN"}


def _is_admin(user: CurrentUser) -> bool:
    return user.role_key == "ADMIN" or "admin.manage" in user.perms


def _member_count(db: Session, chat_id: int) -> int:
    return int(db.scalar(select(func.count()).select_from(ChatMember).where(ChatMember.chat_id == chat_id)) or 0)


def _can_access(db: Session, chat: Chat, user: CurrentUser) -> bool:
    if getattr(chat, "is_super_admin", False):
        return "admin.manage" in user.perms
    if _member_count(db, chat.id) > 0:
        return db.scalar(select(ChatMember).where(ChatMember.chat_id == chat.id, ChatMember.user_id == user.id)) is not None
    if _is_admin(user):
        return True
    return chat.department_id == user.department_id and chat.section_id == user.section_id


class UserOut(BaseModel):
    id: str
    full_name: str
    role_key: str
    department_id: str
    section_id: str


class AdminUserCreate(BaseModel):
    id: str = Field(min_length=2, max_length=64)
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=4, max_length=128)
    role_key: str = "WORKER"
    department_id: str = "FI"
    section_id: str = "FI_MILLING"


class ResetPasswordIn(BaseModel):
    password: str = Field(min_length=4, max_length=128)


class DirectChatIn(BaseModel):
    user_id: str


class GroupChatIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    member_ids: list[str] = Field(default_factory=list)


class SearchHit(BaseModel):
    message_id: int
    chat_id: int
    chat_title: str
    user_id: str
    text: str
    created_at: str


@router.get("/directory/users", response_model=list[UserOut])
def directory_users(q: str = "", limit: int = 100, db: Session = Depends(db_session), user: CurrentUser = Depends(get_current_user)):
    # Corporate isolation: workers/masters/engineers see only their workshop; HEAD sees own enterprise; ADMIN sees all.
    query = select(User)
    if not _is_admin(user):
        if user.role_key == "HEAD":
            query = query.where(User.department_id == user.department_id)
        else:
            query = query.where(User.department_id == user.department_id, User.section_id == user.section_id)
    term = q.strip()
    if term:
        like = f"%{term}%"
        query = query.where((User.id.ilike(like)) | (User.full_name.ilike(like)))
    query = query.order_by(User.full_name.asc()).limit(min(max(limit, 1), 500))
    rows = db.scalars(query).all()
    return [UserOut(id=x.id, full_name=x.full_name, role_key=x.role_key, department_id=x.department_id, section_id=x.section_id) for x in rows]


@router.post("/admin/users", response_model=UserOut)
def admin_create_user(payload: AdminUserCreate, db: Session = Depends(db_session), user: CurrentUser = Depends(get_current_user)):
    if not _is_admin(user):
        raise HTTPException(403, "Admin permission required")
    uid = payload.id.strip()
    if db.get(User, uid):
        raise HTTPException(409, "User already exists")
    role = payload.role_key.upper().strip()
    if role not in ALLOWED_ROLES:
        raise HTTPException(400, f"Unsupported role: {role}")
    row = User(id=uid, full_name=payload.full_name.strip(), role_key=role,
               department_id=payload.department_id.strip() or "FI",
               section_id=payload.section_id.strip() or "FI_MILLING",
               password_hash=hash_password(payload.password))
    db.add(row); db.commit(); db.refresh(row)
    return UserOut(id=row.id, full_name=row.full_name, role_key=row.role_key, department_id=row.department_id, section_id=row.section_id)


@router.post("/admin/users/{user_id}/reset-password")
def admin_reset_password(user_id: str, payload: ResetPasswordIn, db: Session = Depends(db_session), user: CurrentUser = Depends(get_current_user)):
    if not _is_admin(user):
        raise HTTPException(403, "Admin permission required")
    row = db.get(User, user_id)
    if not row:
        raise HTTPException(404, "User not found")
    row.password_hash = hash_password(payload.password)
    db.commit()
    return {"ok": True}


def _validate_members(db: Session, ids: list[str]) -> list[str]:
    clean = list(dict.fromkeys([x.strip() for x in ids if x and x.strip()]))
    if not clean:
        raise HTTPException(400, "No members")
    existing = set(db.scalars(select(User.id).where(User.id.in_(clean))).all())
    missing = [x for x in clean if x not in existing]
    if missing:
        raise HTTPException(400, f"Unknown users: {', '.join(missing)}")
    return clean


def _can_contact_user(db: Session, actor: CurrentUser, target: User) -> bool:
    if _is_admin(actor):
        return True
    if actor.role_key == "HEAD":
        return target.department_id == actor.department_id
    return target.department_id == actor.department_id and target.section_id == actor.section_id


@router.post("/chats/direct")
def create_direct_chat(payload: DirectChatIn, db: Session = Depends(db_session), user: CurrentUser = Depends(get_current_user)):
    target = payload.user_id.strip()
    if target == user.id:
        raise HTTPException(400, "Cannot create direct chat with yourself")
    _validate_members(db, [user.id, target])
    target_user = db.get(User, target)
    if not target_user or not _can_contact_user(db, user, target_user):
        raise HTTPException(403, "Cannot contact employee outside your allowed organization scope")
    # Reuse existing two-member private chat.
    candidates = db.scalars(select(ChatMember.chat_id).where(ChatMember.user_id == user.id)).all()
    for cid in candidates:
        members = db.scalars(select(ChatMember.user_id).where(ChatMember.chat_id == cid)).all()
        if set(members) == {user.id, target} and len(members) == 2:
            c = db.get(Chat, cid)
            if c:
                return {"id": c.id, "title": c.title}
    other = db.get(User, target)
    me = db.get(User, user.id)
    c = Chat(title=other.full_name if other else target, department_id="PRIVATE", section_id="DIRECT", created_by=user.id)
    db.add(c); db.flush()
    db.add_all([ChatMember(chat_id=c.id, user_id=user.id, is_admin=True), ChatMember(chat_id=c.id, user_id=target, is_admin=False)])
    db.commit(); db.refresh(c)
    return {"id": c.id, "title": c.title}


@router.post("/chats/group")
def create_group_chat(payload: GroupChatIn, db: Session = Depends(db_session), user: CurrentUser = Depends(get_current_user)):
    members = _validate_members(db, [user.id] + payload.member_ids)
    for uid in members:
        target = db.get(User, uid)
        if target and not _can_contact_user(db, user, target):
            raise HTTPException(403, f"User {uid} is outside your allowed organization scope")
    c = Chat(title=payload.title.strip(), department_id="PRIVATE", section_id="GROUP", created_by=user.id)
    db.add(c); db.flush()
    db.add_all([ChatMember(chat_id=c.id, user_id=uid, is_admin=(uid == user.id)) for uid in members])
    db.commit(); db.refresh(c)
    return {"id": c.id, "title": c.title, "members": members}


@router.get("/chats/{chat_id}/members", response_model=list[UserOut])
def chat_members(chat_id: int, db: Session = Depends(db_session), user: CurrentUser = Depends(get_current_user)):
    c = db.get(Chat, chat_id)
    if not c or not _can_access(db, c, user):
        raise HTTPException(404, "Chat not found")
    ids = db.scalars(select(ChatMember.user_id).where(ChatMember.chat_id == chat_id)).all()
    if not ids:
        return []
    rows = db.scalars(select(User).where(User.id.in_(ids))).all()
    return [UserOut(id=x.id, full_name=x.full_name, role_key=x.role_key, department_id=x.department_id, section_id=x.section_id) for x in rows]


@router.post("/chats/{chat_id}/upload")
async def upload_file(chat_id: int, file: UploadFile = File(...), caption: str = Form(default=""), db: Session = Depends(db_session), user: CurrentUser = Depends(get_current_user)):
    c = db.get(Chat, chat_id)
    if not c or not _can_access(db, c, user):
        raise HTTPException(404, "Chat not found")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(file.filename or "file").name)[:180] or "file"
    stored = f"{uuid.uuid4().hex}_{safe}"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / stored).write_bytes(data)
    m = Message(chat_id=chat_id, user_id=user.id, text=(caption.strip() or f"📎 {file.filename or 'file'}"))
    db.add(m); db.flush()
    att = MessageAttachment(message_id=m.id, original_name=file.filename or "file", stored_name=stored,
                            mime_type=file.content_type or "application/octet-stream", size_bytes=len(data))
    db.add(att); db.commit(); db.refresh(m); db.refresh(att)
    payload = {"type":"message","chat_id":chat_id,"message":{
        "id":m.id,"chat_id":m.chat_id,"user_id":m.user_id,"text":m.text,"created_at":m.created_at.isoformat(),
        "attachments":[{"id":att.id,"name":att.original_name,"mime_type":att.mime_type,"size_bytes":att.size_bytes,"url":f"/api/v1/files/{att.id}"}]
    }}
    await ws_manager.broadcast(chat_id, payload)
    return payload["message"]


@router.get("/files/{attachment_id}")
def download_file(attachment_id: int, db: Session = Depends(db_session), user: CurrentUser = Depends(get_current_user)):
    att = db.get(MessageAttachment, attachment_id)
    if not att:
        raise HTTPException(404, "File not found")
    m = db.get(Message, att.message_id); c = db.get(Chat, m.chat_id) if m else None
    if not c or not _can_access(db, c, user):
        raise HTTPException(403, "No access")
    path = UPLOAD_DIR / att.stored_name
    if not path.exists():
        raise HTTPException(404, "Stored file missing")
    return FileResponse(path, media_type=att.mime_type, filename=att.original_name)


@router.get("/search/messages", response_model=list[SearchHit])
def search_messages(q: str, chat_id: Optional[int] = None, limit: int = 50, db: Session = Depends(db_session), user: CurrentUser = Depends(get_current_user)):
    term = q.strip()
    if len(term) < 2:
        return []
    query = select(Message, Chat).join(Chat, Chat.id == Message.chat_id).where(Message.text.ilike(f"%{term}%")).order_by(Message.created_at.desc()).limit(min(max(limit,1),100))
    if chat_id:
        query = query.where(Message.chat_id == chat_id)
    out=[]
    for m,c in db.execute(query).all():
        if _can_access(db,c,user):
            out.append(SearchHit(message_id=m.id, chat_id=c.id, chat_title=c.title, user_id=m.user_id, text=m.text, created_at=m.created_at.isoformat()))
    return out

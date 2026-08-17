from __future__ import annotations
from datetime import datetime
from typing import Dict, List
from pydantic import BaseModel, Field

class AttachmentOut(BaseModel):
    id: int
    name: str
    mime_type: str
    size_bytes: int
    url: str

class ChatCreateIn(BaseModel):
    title: str
    department_id: str
    section_id: str

class ChatOut(BaseModel):
    id: int
    title: str
    department_id: str
    section_id: str
    created_by: str
    created_at: datetime
    accessible: bool
    unread_count: int = 0

class MessageCreateIn(BaseModel):
    text: str = Field(min_length=1, max_length=10000)

class MessageOut(BaseModel):
    id: int
    chat_id: int
    user_id: str
    text: str
    created_at: datetime
    attachments: List[AttachmentOut] = []

class ChatWithMessagesOut(BaseModel):
    chat: ChatOut
    messages: List[MessageOut]
    read_states: Dict[str, int] = {}

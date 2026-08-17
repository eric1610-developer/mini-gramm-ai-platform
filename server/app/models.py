# app/models.py
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    String,
    Column,
    Integer,
    Boolean,
    DateTime,
    Text,
    ForeignKey,
    UniqueConstraint,
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.enums import TicketType, Priority, Status


# =========================
# Users + RBAC
# =========================
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    role_key: Mapped[str] = mapped_column(String(32), default="WORKER")
    department_id: Mapped[str] = mapped_column(String(32), default="FI")
    section_id: Mapped[str] = mapped_column(String(64), default="FI_MILLING")
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_key: Mapped[str] = mapped_column(String(32), index=True)
    perm_key: Mapped[str] = mapped_column(String(64), index=True)

    __table_args__ = (UniqueConstraint("role_key", "perm_key", name="uq_role_perm"),)


# =========================
# Tickets
# =========================
class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    type: Mapped[TicketType] = mapped_column(SAEnum(TicketType), index=True)
    category: Mapped[str] = mapped_column(String(64), default="GENERAL")
    title: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")

    priority: Mapped[Priority] = mapped_column(SAEnum(Priority), index=True)
    status: Mapped[Status] = mapped_column(SAEnum(Status), index=True)

    department_id: Mapped[str] = mapped_column(String(32), index=True)
    section_id: Mapped[str] = mapped_column(String(64), index=True)

    created_by: Mapped[str] = mapped_column(String(64), index=True)
    assignee_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TicketHistory(Base):
    __tablename__ = "ticket_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_display_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(255), default="")
    actor_user_id: Mapped[str] = mapped_column(String(64), default="")
    from_status: Mapped[str] = mapped_column(String(64), default="")
    to_status: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class IncidentDetails(Base):
    __tablename__ = "incident_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_display_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    kind: Mapped[str] = mapped_column(String(64), default="GENERAL")
    severity: Mapped[str] = mapped_column(String(8), default="S2")
    location: Mapped[str] = mapped_column(String(255), default="")
    equipment: Mapped[str] = mapped_column(String(255), default="")
    impact: Mapped[str] = mapped_column(String(255), default="")

    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    escalated: Mapped[bool] = mapped_column(Boolean, default=False)


# =========================
# Chat
# =========================
class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), default="")

    department_id: Mapped[str] = mapped_column(String(32), index=True)
    section_id: Mapped[str] = mapped_column(String(64), index=True)

    created_by: Mapped[str] = mapped_column(String(64), default="SYSTEM")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_announcement: Mapped[bool] = mapped_column(Boolean, default=False)
    is_read_only = Column(Integer, nullable=False, default=0)

    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="chat",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)  # оставляем строкой, без FK (не ломаем)
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chat: Mapped["Chat"] = relationship("Chat", back_populates="messages")
class ExamQuestion(Base):
    __tablename__ = "exam_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exam_key: Mapped[str] = mapped_column(String(20), nullable=False)
    qcode: Mapped[str] = mapped_column(String(50), nullable=False)
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    qtype: Mapped[str] = mapped_column(String(20), nullable=False)  # single/multiple
    options: Mapped[str] = mapped_column(Text, nullable=False)
    correct: Mapped[str] = mapped_column(String(50), nullable=False)  # A or A,C
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    media_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default="")

class ExamAttempt(Base):
    __tablename__ = "exam_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exam_key: Mapped[str] = mapped_column(String(20), nullable=False)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False)
    started_at: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)

    answers: Mapped[list["ExamAnswer"]] = relationship("ExamAnswer", back_populates="attempt", cascade="all, delete-orphan")

class ExamAnswer(Base):
    __tablename__ = "exam_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attempt_id: Mapped[int] = mapped_column(Integer, ForeignKey("exam_attempts.id"), nullable=False)
    question_id: Mapped[int] = mapped_column(Integer, ForeignKey("exam_questions.id"), nullable=False)
    selected: Mapped[str] = mapped_column(String(50), nullable=False)
    is_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    answered_at: Mapped[str] = mapped_column(String(40), nullable=False, default="")

    attempt: Mapped["ExamAttempt"] = relationship("ExamAttempt", back_populates="answers")


# ✅ Реальное “прочитано до message_id”
class ChatReadState(Base):
    __tablename__ = "chat_read_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)

    last_read_message_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    read_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("chat_id", "user_id", name="uq_chat_user_read_state"),)


# =========================
# Private/group chat membership + attachments
# =========================
class ChatMember(Base):
    __tablename__ = "chat_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("chat_id", "user_id", name="uq_chat_member"),)


class MessageAttachment(Base):
    __tablename__ = "message_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    original_name: Mapped[str] = mapped_column(String(255), default="file")
    stored_name: Mapped[str] = mapped_column(String(255), unique=True)
    mime_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

# =========================
# Corporate v2 modules
# =========================
class EmployeeProfile(Base):
    __tablename__ = "employee_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    tab_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    enterprise_id: Mapped[str] = mapped_column(String(64), index=True, default="AGMK")
    workshop_id: Mapped[str] = mapped_column(String(64), index=True, default="GENERAL")
    position: Mapped[str] = mapped_column(String(200), default="")
    manager_tab_no: Mapped[str] = mapped_column(String(64), default="")
    employment_status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    domain_login: Mapped[str] = mapped_column(String(128), default="")
    domain_apps_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_director: Mapped[bool] = mapped_column(Boolean, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PayrollSlip(Base):
    __tablename__ = "payroll_slips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    tab_no: Mapped[str] = mapped_column(String(64), index=True)
    period: Mapped[str] = mapped_column(String(20), index=True)
    gross_amount: Mapped[str] = mapped_column(String(64), default="")
    deductions: Mapped[str] = mapped_column(String(64), default="")
    net_amount: Mapped[str] = mapped_column(String(64), default="")
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    source_file: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("tab_no", "period", name="uq_payroll_tab_period"),)


class TechnicalDocument(Base):
    __tablename__ = "technical_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    enterprise_id: Mapped[str] = mapped_column(String(64), index=True)
    workshop_id: Mapped[str] = mapped_column(String(64), index=True, default="ALL")
    equipment_model: Mapped[str] = mapped_column(String(255), index=True, default="")
    equipment_type: Mapped[str] = mapped_column(String(255), default="")
    title: Mapped[str] = mapped_column(String(255))
    doc_type: Mapped[str] = mapped_column(String(64), default="MANUAL")
    file_path: Mapped[str] = mapped_column(String(500), default="")
    text_content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OfflineExamSync(Base):
    __tablename__ = "offline_exam_sync"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_attempt_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    exam_key: Mapped[str] = mapped_column(String(32), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

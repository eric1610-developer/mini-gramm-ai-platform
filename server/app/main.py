# app/main.py
import os
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import FastAPI, Depends, Query, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sqlalchemy import select, func
from sqlalchemy.orm import Session, sessionmaker

from app.db import engine, db_session, Base
from app.notifications.telegram import send_telegram
from app.models import EmployeeProfile
from app.exam_api import router as exam_router

from app.auth import (
    get_current_user,
    CurrentUser,
    require_perm,
    create_access_token,
    verify_password,
    hash_password,
)

from app.models import (
    User, RolePermission,
    Ticket, TicketHistory, IncidentDetails,
    Chat,
)
from app.enums import TicketType, Priority, Status


# =========================================================
# FastAPI app
# =========================================================
app = FastAPI(title="Mini Gramm API", version="2.1")


# =========================================================
# CORS
# =========================================================
CORS_ORIGINS = [x.strip() for x in os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost,http://127.0.0.1"
).split(",") if x.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================================================
# DB session for background tasks
# =========================================================
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# =========================================================
# RBAC seed
# =========================================================
ROLE_PERMS = {
    "WORKER": {"ticket.create", "ticket.view", "ticket.comment"},
    "MASTER": {"ticket.create", "ticket.view", "ticket.view_all", "ticket.comment", "ticket.take", "ticket.assign", "ticket.change_status"},
    "ENGINEER": {"ticket.create", "ticket.view", "ticket.view_all", "ticket.comment", "ticket.take", "ticket.assign", "ticket.change_status", "ticket.close", "ticket.plan"},
    "HEAD": {"ticket.create", "ticket.view", "ticket.view_all", "ticket.comment", "ticket.take", "ticket.assign", "ticket.change_status", "ticket.close", "ticket.escalate", "ticket.plan"},
    "ADMIN": {"ticket.create", "ticket.view", "ticket.view_all", "ticket.comment", "ticket.take", "ticket.assign", "ticket.change_status", "ticket.close", "ticket.escalate", "ticket.plan", "admin.manage"},
}

def seed_rbac(db: Session):
    # 1) RolePermission
    for role_key, perms in ROLE_PERMS.items():
        for perm_key in perms:
            exists = db.scalar(
                select(RolePermission).where(
                    RolePermission.role_key == role_key,
                    RolePermission.perm_key == perm_key,
                )
            )
            if not exists:
                db.add(RolePermission(role_key=role_key, perm_key=perm_key))
    db.commit()

    # 2) demo users (пароли задаём один раз, если password_hash пустой)
    DEMO_PASS_WORKER = os.getenv("DEMO_PASS_WORKER", "demo-worker")
    DEMO_PASS_MASTER = os.getenv("DEMO_PASS_MASTER", "demo-master")
    DEMO_PASS_ENGINEER = os.getenv("DEMO_PASS_ENGINEER", "demo-engineer")
    DEMO_PASS_HEAD = os.getenv("DEMO_PASS_HEAD", "demo-head")
    DEMO_PASS_ADMIN = os.getenv("DEMO_PASS_ADMIN", "demo-admin")

    demo = [
        ("DEMO-WORKER", "Demo Worker",     "WORKER",   DEMO_PASS_WORKER),
        ("DEMO-MASTER", "Demo Master",     "MASTER",   DEMO_PASS_MASTER),
        ("DEMO-ENGINEER", "Demo Engineer", "ENGINEER", DEMO_PASS_ENGINEER),
        ("DEMO-HEAD", "Demo Head",         "HEAD",     DEMO_PASS_HEAD),
        ("DEMO-ADMIN", "Demo Admin",       "ADMIN",    DEMO_PASS_ADMIN),
    ]

    for uid, name, role_key, pwd in demo:
        u = db.scalar(select(User).where(User.id == uid))
        if not u:
            u = User(
                id=uid,
                full_name=name,
                role_key=role_key,
                department_id="FI",
                section_id="FI_MILLING",
            )
            db.add(u)
            db.flush()

        # ✅ пароль ставим только если его ещё нет
        if not getattr(u, "password_hash", None):
            u.password_hash = hash_password(pwd)

    db.commit()


# =========================================================
# System chats seed
# =========================================================
AUTO_CHATS = [
    ("Общий чат АГМК", "AGMK", "ALL"),
    ("ДЦ-1", "FI", "FI_DC1"),
    ("ДЦ-2", "FI", "FI_DC2"),
    ("Энергоцех", "EN", "EN_MAIN"),
    ("КИПиА", "KIPA", "KIPA_MAIN"),
    ("ОТ и ТБ", "TB", "TB_MAIN"),
]

def ensure_super_admin_chat(db: Session) -> int:
    existing = db.scalar(select(Chat).where(Chat.is_super_admin == True))
    if existing:
        return 0
    db.add(Chat(
        title="Super Admin",
        department_id="AGMK",
        section_id="SUPER_ADMIN",
        created_by="SYSTEM",
        is_super_admin=True,
        is_announcement=False,
    ))
    db.commit()
    return 1

def ensure_announcement_chat(db: Session) -> int:
    existing = db.scalar(select(Chat).where(Chat.is_announcement == True))
    if existing:
        return 0
    db.add(Chat(
        title="📢 Объявления АГМК",
        department_id="AGMK",
        section_id="ALL",
        created_by="SYSTEM",
        is_super_admin=False,
        is_announcement=True,
    ))
    db.commit()
    return 1

def ensure_system_chats(db: Session) -> int:
    created = 0
    for title, dept, section in AUTO_CHATS:
        exists = db.scalar(
            select(Chat).where(
                Chat.title == title,
                Chat.department_id == dept,
                Chat.section_id == section,
            )
        )
        if not exists:
            db.add(Chat(
                title=title,
                department_id=dept,
                section_id=section,
                created_by="SYSTEM",
                is_super_admin=False,
                is_announcement=False,
            ))
            created += 1
    if created:
        db.commit()
    return created


# =========================================================
# Helpers
# =========================================================
def write_history(
    db: Session,
    *,
    ticket: Ticket,
    action: str,
    actor: str,
    from_status: Status,
    to_status: Status,
):
    db.add(TicketHistory(
        ticket_display_id=ticket.display_id,
        action=action,
        actor_user_id=actor,
        from_status=from_status.value if hasattr(from_status, "value") else str(from_status),
        to_status=to_status.value if hasattr(to_status, "value") else str(to_status),
    ))

def default_status(t: TicketType) -> Status:
    return {
        TicketType.REQUEST: Status.CREATED,
        TicketType.ALERT: Status.DETECTED,
        TicketType.INCIDENT: Status.REGISTERED,
        TicketType.PLAN_ITEM: Status.ACTIVE,
    }[t]

def make_display_id(n: int) -> str:
    return f"MG-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{n:06d}"

def get_ticket_or_404(db: Session, ticket_id: str) -> Ticket:
    t = db.scalar(select(Ticket).where(Ticket.display_id == ticket_id))
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return t


# =========================================================
# Escalation logic
# =========================================================
def check_and_escalate_incident(db: Session, t: Ticket, d: IncidentDetails):
    if d.escalated:
        return
    if t.status != Status.REGISTERED:
        return

    now = datetime.now(timezone.utc)
    detected_at = d.detected_at
    if detected_at and detected_at.tzinfo is None:
        detected_at = detected_at.replace(tzinfo=timezone.utc)

    if d.severity == "S1" and detected_at and (now - detected_at > timedelta(minutes=1)):
        d.escalated = True
        t.assignee_user_id = "DEMO-HEAD"  # demo HEAD

        write_history(
            db,
            ticket=t,
            action="Эскалация: нет ACK > 1 минут (S1) → назначен HEAD",
            actor="SYSTEM",
            from_status=t.status,
            to_status=t.status,
        )

        msg = (
            f"🚨 <b>ESCALATION</b>\n"
            f"<b>ID:</b> {t.display_id}\n"
            f"<b>Severity:</b> {d.severity}\n"
            f"<b>Location:</b> {d.location}\n"
            f"<b>Equipment:</b> {d.equipment}\n"
            f"<b>Impact:</b> {d.impact}\n"
            f"<b>Assigned:</b> {t.assignee_user_id or '-'}"
        )
        send_telegram(msg)

def run_auto_escalations(db: Session) -> int:
    escalated_count = 0

    tickets = db.scalars(
        select(Ticket).where(
            Ticket.type == TicketType.INCIDENT,
            Ticket.status == Status.REGISTERED,
        )
    ).all()

    for t in tickets:
        d = db.scalar(select(IncidentDetails).where(IncidentDetails.ticket_display_id == t.display_id))
        if not d:
            continue

        was = bool(d.escalated)
        check_and_escalate_incident(db, t, d)

        if (not was) and bool(d.escalated):
            escalated_count += 1

    db.commit()
    return escalated_count


# =========================================================
# Schemas
# =========================================================
class TicketCreateIn(BaseModel):
    type: TicketType
    category: str = "GENERAL"
    department_id: str
    section_id: str
    title: str
    description: str
    priority: Priority = Priority.MEDIUM

class TicketOut(BaseModel):
    id: str
    type: TicketType
    category: str
    title: str
    description: str
    priority: Priority
    status: Status
    department_id: str
    section_id: str
    created_by: str
    assignee_user_id: Optional[str]
    created_at: datetime
    updated_at: datetime

class TicketListOut(BaseModel):
    items: List[TicketOut]
    total: int

class ActionsOut(BaseModel):
    actions: List[str]

class IncidentCreateIn(BaseModel):
    kind: str = "GENERAL"
    severity: str = "S2"
    location: str = ""
    equipment: str = ""
    impact: str = ""
    department_id: str
    section_id: str
    title: str
    description: str
    priority: Priority = Priority.HIGH

class IncidentOut(BaseModel):
    id: str
    status: Status
    priority: Priority
    department_id: str
    section_id: str
    created_by: str
    assignee_user_id: Optional[str]
    created_at: datetime
    updated_at: datetime
    kind: str
    severity: str
    location: str
    equipment: str
    impact: str
    detected_at: datetime
    acknowledged_at: Optional[datetime]
    resolved_at: Optional[datetime]
    escalated: bool

def to_ticket_out(t: Ticket) -> TicketOut:
    return TicketOut(
        id=t.display_id,
        type=t.type,
        category=t.category,
        title=t.title,
        description=t.description,
        priority=t.priority,
        status=t.status,
        department_id=t.department_id,
        section_id=t.section_id,
        created_by=t.created_by,
        assignee_user_id=t.assignee_user_id,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )

def to_incident_out(t: Ticket, d: IncidentDetails) -> IncidentOut:
    return IncidentOut(
        id=t.display_id,
        status=t.status,
        priority=t.priority,
        department_id=t.department_id,
        section_id=t.section_id,
        created_by=t.created_by,
        assignee_user_id=t.assignee_user_id,
        created_at=t.created_at,
        updated_at=t.updated_at,
        kind=d.kind,
        severity=d.severity,
        location=d.location,
        equipment=d.equipment,
        impact=d.impact,
        detected_at=d.detected_at,
        acknowledged_at=d.acknowledged_at,
        resolved_at=d.resolved_at,
        escalated=bool(d.escalated),
    )


# =========================================================
# Auth endpoints
# =========================================================
class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"

@app.post("/api/v1/auth/login", response_model=TokenOut)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(db_session),
):
    u = db.scalar(select(User).where(User.id == form_data.username))
    if not u or not u.password_hash:
        raise HTTPException(status_code=401, detail="Invalid user or password not set")
    profile = db.scalar(select(EmployeeProfile).where(EmployeeProfile.user_id == u.id))
    if profile and profile.employment_status != "ACTIVE":
        raise HTTPException(status_code=403, detail="Employee account is inactive")

    if not verify_password(form_data.password, u.password_hash):
        raise HTTPException(status_code=401, detail="Invalid user or password")

    token = create_access_token(sub=u.id, role_key=u.role_key)
    return {"access_token": token, "token_type": "bearer"}

MAX_DEMO_USERS = int(os.getenv("MAX_DEMO_USERS", "5000"))

class RegisterIn(BaseModel):
    tab_no: str = Field(..., min_length=3, max_length=64)
    full_name: str = Field(..., min_length=2, max_length=200)
    password: str = Field(..., min_length=4, max_length=64)

@app.post("/api/v1/auth/register", response_model=TokenOut)
def register(payload: RegisterIn, db: Session = Depends(db_session)):
    if os.getenv("OPEN_REGISTRATION", "false").lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=403, detail="Open registration is disabled; contact administrator")
    uid = payload.tab_no.strip()
    if uid.isdigit():
        uid = f"U-{uid}"

    exists = db.scalar(select(User).where(User.id == uid))
    if exists:
        raise HTTPException(status_code=409, detail="User already exists")

    total = db.scalar(select(func.count()).select_from(User).where(User.id != "SYSTEM"))
    if total >= MAX_DEMO_USERS:
        raise HTTPException(status_code=403, detail=f"Demo user limit reached ({MAX_DEMO_USERS})")

    u = User(
        id=uid,
        full_name=payload.full_name.strip(),
        role_key="WORKER",
        department_id="FI",
        section_id="FI_MILLING",
        password_hash=hash_password(payload.password),
    )
    db.add(u)
    db.commit()

    token = create_access_token(sub=u.id, role_key=u.role_key)
    return {"access_token": token, "token_type": "bearer"}

@app.post("/api/v1/telegram/test")
def telegram_test(user: CurrentUser = Depends(get_current_user)):
    ok = send_telegram(f"✅ Telegram test from API. User={user.id}")
    return {"ok": ok}


# =========================================================
# Background auto escalation
# =========================================================
AUTO_ESCALATION_SECONDS = int(os.getenv("AUTO_ESCALATION_SECONDS", "30"))

async def _auto_escalation_loop(stop_event: asyncio.Event):
    try:
        while not stop_event.is_set():
            db = SessionLocal()
            try:
                run_auto_escalations(db)
            except Exception as e:
                print("❌ auto escalation error:", e)
            finally:
                db.close()
            await asyncio.sleep(AUTO_ESCALATION_SECONDS)
    except asyncio.CancelledError:
        pass

@app.on_event("startup")
async def on_startup():
    # Clean local deployment: create missing tables before seeding roles/chats.
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_rbac(db)
        ensure_announcement_chat(db)
        ensure_super_admin_chat(db)
        ensure_system_chats(db)
    finally:
        db.close()

    app.state.stop_event = asyncio.Event()
    app.state.auto_task = asyncio.create_task(_auto_escalation_loop(app.state.stop_event))

@app.on_event("shutdown")
async def on_shutdown():
    stop_event = getattr(app.state, "stop_event", None)
    task = getattr(app.state, "auto_task", None)

    if stop_event:
        stop_event.set()

    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass


# =========================================================
# Basic endpoints
# =========================================================
@app.get("/")
def root():
    return {"status": "Mini Gramm API running", "version": "2.0-corporate"}

@app.get("/api/v1/health")
def health():
    return {"ok": True, "service": "mini-gramm-api", "version": "2.0-corporate"}

@app.get("/api/v1/me")
def me(user: CurrentUser = Depends(get_current_user)):
    return {
        "id": user.id,
        "role_key": user.role_key,
        "department_id": user.department_id,
        "section_id": user.section_id,
        "perms": sorted(list(user.perms)),
    }


# =========================================================
# Tickets
# =========================================================
def allowed_actions(ticket: Ticket, user: CurrentUser) -> List[str]:
    actions: List[str] = []
    if "ticket.comment" in user.perms:
        actions.append("comment")

    if ticket.type == TicketType.REQUEST:
        if ticket.status == Status.CREATED:
            if "ticket.take" in user.perms:
                actions.append("take_in_work")
            if "ticket.assign" in user.perms:
                actions.append("assign")
            if "ticket.escalate" in user.perms:
                actions.append("escalate")
        if ticket.status in (Status.IN_PROGRESS, Status.WAITING):
            if "ticket.change_status" in user.perms:
                actions.append("set_waiting")
                actions.append("set_in_progress")
            if "ticket.close" in user.perms:
                actions.append("close")

    if ticket.type == TicketType.INCIDENT:
        if ticket.status == Status.REGISTERED:
            if "ticket.change_status" in user.perms:
                actions.append("acknowledge")
            if "ticket.take" in user.perms:
                actions.append("take_in_work")
            if "ticket.assign" in user.perms:
                actions.append("assign")
        if ticket.status in (Status.ACTIVE, Status.IN_PROGRESS):
            if "ticket.change_status" in user.perms:
                actions.append("resolve")
            if "ticket.close" in user.perms:
                actions.append("close")
        if ticket.status == Status.RESOLVED:
            if "ticket.close" in user.perms:
                actions.append("close")
    return actions

@app.post("/api/v1/tickets", response_model=TicketOut)
def create_ticket(payload: TicketCreateIn, db: Session = Depends(db_session), user: CurrentUser = Depends(get_current_user)):
    require_perm(user, "ticket.create")

    t = Ticket(
        display_id="TEMP",
        type=payload.type,
        category=payload.category,
        title=payload.title,
        description=payload.description,
        priority=payload.priority,
        status=default_status(payload.type),
        department_id=payload.department_id,
        section_id=payload.section_id,
        created_by=user.id,
        assignee_user_id=None,
    )
    db.add(t)
    db.flush()
    t.display_id = make_display_id(t.id)

    write_history(db, ticket=t, action="Создать ticket", actor=user.id, from_status=t.status, to_status=t.status)
    db.commit()
    db.refresh(t)
    return to_ticket_out(t)

@app.get("/api/v1/tickets", response_model=TicketListOut)
def list_tickets(
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(get_current_user),
    department_id: Optional[str] = Query(default=None),
    section_id: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    require_perm(user, "ticket.view")

    q = select(Ticket)
    if "ticket.view_all" not in user.perms:
        q = q.where(Ticket.created_by == user.id)

    if department_id:
        q = q.where(Ticket.department_id == department_id)
    if section_id:
        q = q.where(Ticket.section_id == section_id)

    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(Ticket.updated_at.desc()).offset(offset).limit(limit)).all()
    return TicketListOut(items=[to_ticket_out(r) for r in rows], total=total)

@app.get("/api/v1/tickets/{ticket_id}/actions", response_model=ActionsOut)
def get_ticket_actions(ticket_id: str, db: Session = Depends(db_session), user: CurrentUser = Depends(get_current_user)):
    t = get_ticket_or_404(db, ticket_id)
    if "ticket.view_all" not in user.perms and t.created_by != user.id:
        raise HTTPException(status_code=403, detail="Not allowed")
    return ActionsOut(actions=allowed_actions(t, user))

@app.get("/api/v1/tickets/{ticket_id}/history")
def get_ticket_history(ticket_id: str, db: Session = Depends(db_session), user: CurrentUser = Depends(get_current_user)):
    require_perm(user, "ticket.view")
    t = get_ticket_or_404(db, ticket_id)

    if "ticket.view_all" not in user.perms and t.created_by != user.id:
        raise HTTPException(status_code=403, detail="Not allowed")

    rows = db.scalars(
        select(TicketHistory)
        .where(TicketHistory.ticket_display_id == t.display_id)
        .order_by(TicketHistory.created_at)
    ).all()

    return [
        {"when": r.created_at, "who": r.actor_user_id, "action": r.action, "from": r.from_status, "to": r.to_status}
        for r in rows
    ]


# =========================================================
# Incidents
# =========================================================
@app.post("/api/v1/incidents", response_model=IncidentOut)
def create_incident(payload: IncidentCreateIn, db: Session = Depends(db_session), user: CurrentUser = Depends(get_current_user)):
    require_perm(user, "ticket.create")

    t = Ticket(
        display_id="TEMP",
        type=TicketType.INCIDENT,
        category=payload.kind,
        title=payload.title,
        description=payload.description,
        priority=payload.priority,
        status=Status.REGISTERED,
        department_id=payload.department_id,
        section_id=payload.section_id,
        created_by=user.id,
        assignee_user_id=None,
    )
    db.add(t)
    db.flush()
    t.display_id = make_display_id(t.id)

    d = IncidentDetails(
        ticket_display_id=t.display_id,
        kind=payload.kind,
        severity=payload.severity,
        location=payload.location,
        equipment=payload.equipment,
        impact=payload.impact,
        escalated=False,
    )
    db.add(d)

    write_history(db, ticket=t, action="Создать инцидент", actor=user.id, from_status=t.status, to_status=t.status)
    db.commit()

    d2 = db.scalar(select(IncidentDetails).where(IncidentDetails.ticket_display_id == t.display_id))
    return to_incident_out(t, d2)

@app.get("/api/v1/incidents", response_model=List[IncidentOut])
def list_incidents(db: Session = Depends(db_session), user: CurrentUser = Depends(get_current_user), limit: int = Query(50, ge=1, le=200)):
    require_perm(user, "ticket.view")

    q = select(Ticket).where(Ticket.type == TicketType.INCIDENT)
    if "ticket.view_all" not in user.perms:
        q = q.where(Ticket.created_by == user.id)

    rows = db.scalars(q.order_by(Ticket.updated_at.desc()).limit(limit)).all()

    out: List[IncidentOut] = []
    for t in rows:
        d = db.scalar(select(IncidentDetails).where(IncidentDetails.ticket_display_id == t.display_id))
        if d:
            out.append(to_incident_out(t, d))
    return out

@app.get("/api/v1/incidents/{ticket_id}", response_model=IncidentOut)
def get_incident(ticket_id: str, db: Session = Depends(db_session), user: CurrentUser = Depends(get_current_user)):
    require_perm(user, "ticket.view")

    t = db.scalar(select(Ticket).where(Ticket.display_id == ticket_id, Ticket.type == TicketType.INCIDENT))
    if not t:
        raise HTTPException(status_code=404, detail="Incident not found")

    if "ticket.view_all" not in user.perms and t.created_by != user.id:
        raise HTTPException(status_code=403, detail="Not allowed")

    d = db.scalar(select(IncidentDetails).where(IncidentDetails.ticket_display_id == t.display_id))
    if not d:
        raise HTTPException(status_code=500, detail="Incident details missing")

    return to_incident_out(t, d)

@app.post("/api/v1/incidents/{ticket_id}/ack")
def acknowledge_incident(ticket_id: str, db: Session = Depends(db_session), user: CurrentUser = Depends(get_current_user)):
    require_perm(user, "ticket.change_status")

    t = db.scalar(select(Ticket).where(Ticket.display_id == ticket_id, Ticket.type == TicketType.INCIDENT))
    if not t:
        raise HTTPException(status_code=404, detail="Incident not found")
    if t.status != Status.REGISTERED:
        raise HTTPException(status_code=400, detail="Only REGISTERED incident can be acknowledged")

    d = db.scalar(select(IncidentDetails).where(IncidentDetails.ticket_display_id == t.display_id))
    if not d:
        raise HTTPException(status_code=500, detail="Incident details missing")

    old = t.status
    t.status = Status.ACTIVE
    d.acknowledged_at = func.now()

    write_history(db, ticket=t, action="Подтвердить инцидент", actor=user.id, from_status=old, to_status=t.status)
    db.commit()
    return {"message": "Incident acknowledged", "status": t.status}

@app.post("/api/v1/incidents/{ticket_id}/resolve")
def resolve_incident(ticket_id: str, db: Session = Depends(db_session), user: CurrentUser = Depends(get_current_user)):
    require_perm(user, "ticket.change_status")

    t = db.scalar(select(Ticket).where(Ticket.display_id == ticket_id, Ticket.type == TicketType.INCIDENT))
    if not t:
        raise HTTPException(status_code=404, detail="Incident not found")
    if t.status not in (Status.ACTIVE, Status.IN_PROGRESS):
        raise HTTPException(status_code=400, detail="Incident must be ACTIVE/IN_PROGRESS to resolve")

    d = db.scalar(select(IncidentDetails).where(IncidentDetails.ticket_display_id == t.display_id))
    if not d:
        raise HTTPException(status_code=500, detail="Incident details missing")

    old = t.status
    t.status = Status.RESOLVED
    d.resolved_at = func.now()

    write_history(db, ticket=t, action="Устранено", actor=user.id, from_status=old, to_status=t.status)
    db.commit()
    return {"message": "Incident resolved", "status": t.status}

@app.post("/api/v1/incidents/{ticket_id}/close")
def close_incident(ticket_id: str, db: Session = Depends(db_session), user: CurrentUser = Depends(get_current_user)):
    require_perm(user, "ticket.close")

    t = db.scalar(select(Ticket).where(Ticket.display_id == ticket_id, Ticket.type == TicketType.INCIDENT))
    if not t:
        raise HTTPException(status_code=404, detail="Incident not found")
    if t.status not in (Status.RESOLVED, Status.ACTIVE, Status.IN_PROGRESS):
        raise HTTPException(status_code=400, detail="Incident cannot be closed from current status")

    old = t.status
    t.status = Status.CLOSED

    write_history(db, ticket=t, action="Закрыть инцидент", actor=user.id, from_status=old, to_status=t.status)
    db.commit()
    return {"message": "Incident closed", "status": t.status}


# =========================================================
# Escalations manual run
# =========================================================
@app.post("/api/v1/escalations/run")
def run_escalations_now(db: Session = Depends(db_session), user: CurrentUser = Depends(get_current_user)):
    require_perm(user, "ticket.view_all")
    n = run_auto_escalations(db)
    return {"ok": True, "escalated_count": n}


# =========================================================
# Routers (Chat + WS + Exam)
# =========================================================
from app.chat_api import router as chat_router  # noqa
app.include_router(chat_router)

# exam_router уже имеет prefix="/exam" внутри exam_api.py
# поэтому тут только /api/v1
app.include_router(exam_router, prefix="/api/v1", tags=["exam"])

from app.chat_ws import ws_router  # noqa
app.include_router(ws_router)

from app.collab_api import router as collab_router  # noqa
app.include_router(collab_router)

from app.corporate_api import router as corporate_router  # noqa
app.include_router(corporate_router)

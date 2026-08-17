# app/auth.py
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Set

from fastapi import Depends, Header, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from passlib.context import CryptContext
from passlib.exc import UnknownHashError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import db_session
from app.models import User, RolePermission, EmployeeProfile

JWT_SECRET = os.getenv("JWT_SECRET", "development-only-change-before-deployment")
JWT_ALG = "HS256"
JWT_EXPIRE_MIN = int(os.getenv("JWT_EXPIRE_MIN", "720"))  # 12 часов

# ✅ СТАБИЛЬНО на Windows: только pbkdf2 (без bcrypt)
pwd_context = CryptContext(
    schemes=["pbkdf2_sha256"],
    deprecated="auto",
)

# auto_error=False => если нет Bearer, даём пройти по X-User-Id
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def hash_password(password: str) -> str:
    password = password or ""
    # (оставим защиту на всякий случай)
    b = password.encode("utf-8")
    if len(b) > 72:
        password = b[:72].decode("utf-8", errors="ignore")
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(password or "", password_hash or "")
    except UnknownHashError:
        return False


def create_access_token(*, sub: str, role_key: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "role_key": role_key,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXPIRE_MIN)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def load_permissions(db: Session, role_key: str) -> Set[str]:
    rows = db.scalars(select(RolePermission).where(RolePermission.role_key == role_key)).all()
    return {r.perm_key for r in rows}


class CurrentUser:
    # простая структура (не Pydantic, чтобы меньше проблем)
    def __init__(self, *, id: str, role_key: str, department_id: str, section_id: str, perms: Set[str]):
        self.id = id
        self.role_key = role_key
        self.department_id = department_id
        self.section_id = section_id
        self.perms = perms


def get_current_user(
    db: Session = Depends(db_session),
    token: Optional[str] = Depends(oauth2_scheme),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
) -> CurrentUser:
    # 1) JWT first
    if token:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
            uid = payload.get("sub")
            if not uid:
                raise HTTPException(status_code=401, detail="Invalid token")

            u = db.scalar(select(User).where(User.id == uid))
            if not u:
                raise HTTPException(status_code=401, detail="User not found")
            profile = db.scalar(select(EmployeeProfile).where(EmployeeProfile.user_id == uid))
            if profile and profile.employment_status != "ACTIVE":
                raise HTTPException(status_code=403, detail="Employee account is inactive")

            perms = load_permissions(db, u.role_key)
            return CurrentUser(
                id=u.id,
                role_key=u.role_key,
                department_id=u.department_id,
                section_id=u.section_id,
                perms=perms,
            )
        except JWTError:
            raise HTTPException(status_code=401, detail="Invalid token")

    # 2) optional local-development fallback X-User-Id
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    if os.getenv("ALLOW_X_USER_ID", "false").lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=401, detail="X-User-Id authentication is disabled")

    u = db.scalar(select(User).where(User.id == x_user_id))
    if not u:
        raise HTTPException(status_code=401, detail="Unknown user. Set correct X-User-Id")
    profile = db.scalar(select(EmployeeProfile).where(EmployeeProfile.user_id == x_user_id))
    if profile and profile.employment_status != "ACTIVE":
        raise HTTPException(status_code=403, detail="Employee account is inactive")

    perms = load_permissions(db, u.role_key)
    return CurrentUser(
        id=u.id,
        role_key=u.role_key,
        department_id=u.department_id,
        section_id=u.section_id,
        perms=perms,
    )


def require_perm(user: CurrentUser, perm: str):
    if perm not in user.perms:
        raise HTTPException(status_code=403, detail=f"Missing permission: {perm}")

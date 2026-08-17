# app/exam_api.py
import random
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import db_session
from app.auth import get_current_user, CurrentUser
from app.models import ExamQuestion, ExamAttempt, ExamAnswer

from app.exam_models import (
    ExamStartOut, ExamQuestionOut,
    ExamAnswerIn, ExamAnswerOut,
    ExamUserStatOut, ExamQuestionStatOut
)

router = APIRouter(prefix="/exam", tags=["exam"])


def _norm_sel(s: str) -> str:
    parts = [p.strip().upper() for p in (s or "").split(",") if p.strip()]
    parts = sorted(set(parts))
    return ",".join(parts)


@router.post("/{exam_key}/start", response_model=ExamStartOut)
def exam_start(
    exam_key: str,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(get_current_user),
):
    exam_key = exam_key.upper().strip()
    exists = db.scalar(select(ExamQuestion.id).where(ExamQuestion.exam_key == exam_key, ExamQuestion.is_active == 1).limit(1))
    if not exists:
        raise HTTPException(status_code=404, detail="No active questions for this exam")

    a = ExamAttempt(exam_key=exam_key, user_id=user.id)
    db.add(a)
    db.commit()
    db.refresh(a)
    return ExamStartOut(attempt_id=a.id, exam_key=exam_key)


@router.get("/{attempt_id}/next", response_model=ExamQuestionOut)
def exam_next(
    attempt_id: int,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(get_current_user),
):
    attempt = db.scalar(select(ExamAttempt).where(ExamAttempt.id == attempt_id))
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")

    if attempt.user_id != user.id and "admin.manage" not in user.perms:
        raise HTTPException(status_code=403, detail="No access")

    answered = db.scalars(
        select(ExamAnswer.question_id).where(ExamAnswer.attempt_id == attempt_id)
    ).all()
    answered_set = set(answered)

    qs = db.scalars(
        select(ExamQuestion).where(
            ExamQuestion.exam_key == attempt.exam_key,
            ExamQuestion.is_active == 1
        )
    ).all()

    if not qs:
        raise HTTPException(status_code=404, detail="No active questions for this exam")

    remaining = [q for q in qs if q.id not in answered_set]
    if not remaining:
        remaining = qs

    q = random.choice(remaining)
    return ExamQuestionOut(
        id=q.id,
        exam_key=q.exam_key,
        qcode=q.qcode,
        topic=q.topic,
        question=q.question,
        qtype=q.qtype,
        options=q.options,
        media_path=q.media_path or "",
    )


@router.post("/answer", response_model=ExamAnswerOut)
def exam_answer(
    payload: ExamAnswerIn,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(get_current_user),
):
    attempt = db.scalar(select(ExamAttempt).where(ExamAttempt.id == payload.attempt_id))
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")

    if attempt.user_id != user.id and "admin.manage" not in user.perms:
        raise HTTPException(status_code=403, detail="No access")

    q = db.scalar(select(ExamQuestion).where(ExamQuestion.id == payload.question_id))
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    if q.exam_key != attempt.exam_key:
        raise HTTPException(status_code=400, detail="Question does not belong to this exam")

    sel = _norm_sel(payload.selected)
    corr = _norm_sel(q.correct)
    is_ok = 1 if sel == corr else 0

    ans = ExamAnswer(
        attempt_id=attempt.id,
        question_id=q.id,
        selected=sel,
        is_correct=is_ok,
    )
    db.add(ans)
    db.commit()

    return ExamAnswerOut(is_correct=bool(is_ok), correct=corr, explanation=q.explanation)


@router.get("/{exam_key}/stats/users", response_model=list[ExamUserStatOut])
def exam_stats_users(
    exam_key: str,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(get_current_user),
):
    if "admin.manage" not in user.perms and user.role_key != "HEAD":
        raise HTTPException(status_code=403, detail="No access")

    exam_key = exam_key.upper().strip()
    rows = db.execute(
        select(
            ExamAttempt.user_id,
            func.count(ExamAnswer.id).label("total"),
            func.sum(ExamAnswer.is_correct).label("correct"),
        )
        .join(ExamAnswer, ExamAnswer.attempt_id == ExamAttempt.id)
        .where(ExamAttempt.exam_key == exam_key)
        .group_by(ExamAttempt.user_id)
        .order_by(func.count(ExamAnswer.id).desc())
    ).all()

    out = []
    for user_id, total, correct in rows:
        total = int(total or 0)
        correct = int(correct or 0)
        pct = round((correct / total) * 100, 2) if total else 0.0
        out.append(ExamUserStatOut(user_id=user_id, total=total, correct=correct, percent=pct))
    return out


@router.get("/{exam_key}/stats/questions", response_model=list[ExamQuestionStatOut])
def exam_stats_questions(
    exam_key: str,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(get_current_user),
):
    if "admin.manage" not in user.perms and user.role_key != "HEAD":
        raise HTTPException(status_code=403, detail="No access")

    exam_key = exam_key.upper().strip()
    rows = db.execute(
        select(
            ExamQuestion.qcode,
            ExamQuestion.topic,
            ExamQuestion.question,
            func.count(ExamAnswer.id).label("total"),
            func.sum(ExamAnswer.is_correct).label("correct"),
        )
        .join(ExamAnswer, ExamAnswer.question_id == ExamQuestion.id)
        .where(ExamQuestion.exam_key == exam_key)
        .group_by(ExamQuestion.id)
        .order_by((func.sum(ExamAnswer.is_correct) / func.count(ExamAnswer.id)).asc())
    ).all()

    out = []
    for qcode, topic, question, total, correct in rows:
        total = int(total or 0)
        correct = int(correct or 0)
        pct = round((correct / total) * 100, 2) if total else 0.0
        out.append(ExamQuestionStatOut(
            qcode=qcode, topic=topic, question=question,
            total=total, correct=correct, percent=pct
        ))
    return out
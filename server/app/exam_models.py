from pydantic import BaseModel
from typing import Optional, List

class ExamStartOut(BaseModel):
    attempt_id: int
    exam_key: str

class ExamQuestionOut(BaseModel):
    id: int
    exam_key: str
    qcode: str
    topic: str
    question: str
    qtype: str
    options: str
    media_path: str = ""

class ExamAnswerIn(BaseModel):
    attempt_id: int
    question_id: int
    selected: str  # "A" or "A,C"

class ExamAnswerOut(BaseModel):
    is_correct: bool
    correct: str
    explanation: str

class ExamUserStatOut(BaseModel):
    user_id: str
    total: int
    correct: int
    percent: float

class ExamQuestionStatOut(BaseModel):
    qcode: str
    topic: str
    question: str
    total: int
    correct: int
    percent: float
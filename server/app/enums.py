# app/enums.py
from enum import Enum


class TicketType(str, Enum):
    REQUEST = "REQUEST"
    ALERT = "ALERT"
    INCIDENT = "INCIDENT"
    PLAN_ITEM = "PLAN_ITEM"


class Priority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Status(str, Enum):
    CREATED = "CREATED"
    DETECTED = "DETECTED"
    REGISTERED = "REGISTERED"
    ACTIVE = "ACTIVE"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING = "WAITING"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

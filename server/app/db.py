import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# Если DATABASE_URL не задан, работаем локально на SQLite
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./mini_gramm.db")

# SQLite требует special connect_args
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase):
    pass

def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


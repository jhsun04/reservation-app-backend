"""
DB 연결 설정.
개발 단계에서는 SQLite를 쓰고, 나중에 운영 환경에서는
SQLALCHEMY_DATABASE_URL 환경변수만 PostgreSQL 접속 문자열로 바꾸면 된다.
예: postgresql+psycopg2://user:password@localhost:5432/reservation_app
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL", "sqlite:///./reservation_app.db"
)

connect_args = {"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

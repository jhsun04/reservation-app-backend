"""
전화번호 + 비밀번호 인증.

- 비밀번호는 hashlib.pbkdf2_hmac(표준 라이브러리)로 해시 — bcrypt/passlib처럼
  컴파일이 필요한 패키지가 아니라서, 예전에 겪었던 윈도우 C-익스텐션 빌드 문제를
  또 겪을 일이 없다.
- 세션은 JWT(PyJWT, 순수 파이썬 패키지, 컴파일 불필요)로 발급한다. 유효기간 7일.
- SECRET_KEY는 환경변수(JWT_SECRET_KEY)로 설정 가능. 데모 단계라 안 정해도
  동작은 하지만, 실제 운영에서는 반드시 환경변수로 강한 값을 넣어야 한다.
"""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app import models
from app.database import get_db

# 데모/개발 기본값. 운영 배포 시에는 반드시 환경변수 JWT_SECRET_KEY로 강한 값을 넣을 것.
SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "insecure-dev-secret-change-me")
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 7

_PBKDF2_ITERATIONS = 260_000


def hash_password(plain_password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", plain_password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERATIONS
    ).hex()
    return f"{salt}${digest}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        salt, digest = hashed_password.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", plain_password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERATIONS
    ).hex()
    return hmac.compare_digest(candidate, digest)


def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _decode_token(token: str) -> int:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="invalid_token")


_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="not_authenticated")
    user_id = _decode_token(credentials.credentials)
    user = db.query(models.User).get(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user_not_found")
    return user


def require_self(user_id: int, current_user: models.User) -> None:
    """본인 소유가 아닌 리소스에 접근하려 하면 403."""
    if current_user.id != user_id:
        raise HTTPException(status_code=403, detail="not_your_resource")

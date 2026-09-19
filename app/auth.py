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
from typing import NamedTuple, Optional

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


def create_access_token(subject_id: int, subject_type: str) -> str:
    """subject_type은 "user"(손님) 또는 "restaurant"(매장 사장님) 둘 중 하나.
    토큰 안에 타입을 같이 넣어둬서, 손님 토큰으로 매장 API를 부르거나 그 반대로
    쓰는 걸 막는다."""
    expire = datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS)
    payload = {"sub": str(subject_id), "type": subject_type, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _decode_token(token: str) -> tuple[int, str]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return int(payload["sub"]), payload["type"]
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="invalid_token")


_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="not_authenticated")
    subject_id, subject_type = _decode_token(credentials.credentials)
    if subject_type != "user":
        raise HTTPException(status_code=401, detail="not_a_customer_token")
    user = db.query(models.User).get(subject_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user_not_found")
    return user


def get_current_restaurant(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> models.Restaurant:
    if credentials is None:
        raise HTTPException(status_code=401, detail="not_authenticated")
    subject_id, subject_type = _decode_token(credentials.credentials)
    if subject_type != "restaurant":
        raise HTTPException(status_code=401, detail="not_a_restaurant_token")
    restaurant = db.query(models.Restaurant).get(subject_id)
    if restaurant is None:
        raise HTTPException(status_code=401, detail="restaurant_not_found")
    return restaurant


class Actor(NamedTuple):
    """예약 이벤트(이행/노쇼 등)처럼 손님 또는 매장 둘 다 부를 수 있는 API에서
    "지금 누가 부르고 있는지"를 나타낸다."""
    type: str  # "user" 또는 "restaurant"
    id: int


def get_current_actor(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> Actor:
    if credentials is None:
        raise HTTPException(status_code=401, detail="not_authenticated")
    subject_id, subject_type = _decode_token(credentials.credentials)
    if subject_type == "user":
        exists = db.query(models.User).get(subject_id) is not None
    elif subject_type == "restaurant":
        exists = db.query(models.Restaurant).get(subject_id) is not None
    else:
        raise HTTPException(status_code=401, detail="invalid_token_type")
    if not exists:
        raise HTTPException(status_code=401, detail="actor_not_found")
    return Actor(type=subject_type, id=subject_id)


def require_self(user_id: int, current_user: models.User) -> None:
    """본인 소유가 아닌 손님 리소스에 접근하려 하면 403."""
    if current_user.id != user_id:
        raise HTTPException(status_code=403, detail="not_your_resource")


def require_restaurant_self(restaurant_id: int, current_restaurant: models.Restaurant) -> None:
    """본인 소유가 아닌 매장 리소스에 접근하려 하면 403."""
    if current_restaurant.id != restaurant_id:
        raise HTTPException(status_code=403, detail="not_your_restaurant")

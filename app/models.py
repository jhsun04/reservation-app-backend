"""
ORM 모델. 기획서 기준:
- User.trust_score 시작값 60점, 0~100 범위로 클램프
- ReservationEvent: 예약 하나에 발생한 이벤트(이행/노쇼/취소 등) 이력 -> 신뢰점수 계산의 근거
"""
import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import relationship

from app.database import Base


class TrustBand(str, enum.Enum):
    VIP = "VIP"          # 100~80
    STANDARD = "STANDARD"  # 79~50
    CAUTION = "CAUTION"    # 49~25
    RISK = "RISK"          # 25~0


class EventType(str, enum.Enum):
    FULFILLED = "FULFILLED"                  # 정상 이행
    NO_SHOW_SAME_DAY = "NO_SHOW_SAME_DAY"     # 당일 무단 노쇼
    CANCEL_DAY_BEFORE = "CANCEL_DAY_BEFORE"   # 하루 전 취소
    CANCEL_IMMINENT = "CANCEL_IMMINENT"       # 임박 취소 (3~6시간 전)
    NO_SHOW_DUPLICATE = "NO_SHOW_DUPLICATE"   # 중복 예약형 노쇼
    LATE = "LATE"                             # 30분 이상 지각 (감점 없음, 횟수만 기록)
    PARTIAL_NO_SHOW = "PARTIAL_NO_SHOW"       # 부분 노쇼 (감점 없음)
    FORCE_MAJEURE = "FORCE_MAJEURE"           # 불가항력 (증빙 제출, 감점 없음)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, unique=True, nullable=False)

    trust_score = Column(Integer, nullable=False, default=60)
    consecutive_success_streak = Column(Integer, nullable=False, default=0)

    # 상습 지각 카운트 (감점 없음, 매장 노출용 플래그 계산에 사용)
    late_count = Column(Integer, nullable=False, default=0)

    # 25점 이하 위험 구간 회복 트랙 추적용
    risk_band_entered_at = Column(DateTime, nullable=True)
    risk_band_success_count = Column(Integer, nullable=False, default=0)

    # 동일 사유 불가항력 증빙 반복 제출 감지용
    force_majeure_reasons = Column(String, nullable=True)  # 콤마로 구분된 사유 로그(간단 구현)
    flagged_for_review = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    reservations = relationship("Reservation", back_populates="user")


class Restaurant(Base):
    __tablename__ = "restaurants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=True)

    # 구독 티어: STARTER / STANDARD / PREMIUM (문자열로 단순 구현, 나중에 결제 붙이면 확장)
    subscription_tier = Column(String, nullable=False, default="STARTER")
    subscription_started_at = Column(DateTime, default=datetime.utcnow)
    subscription_free_trial_ends_at = Column(DateTime, nullable=True)

    # 매장이 직접 정하는 노쇼 리스크 허용도: STRICT(엄격) / NORMAL(보통) / LENIENT(관대)
    # 스탠다드 이상 티어에서만 NORMAL이 아닌 값으로 바꿀 수 있다 (app/subscription.py 참고)
    risk_tolerance = Column(String, nullable=False, default="NORMAL")

    reservations = relationship("Reservation", back_populates="restaurant")


class Reservation(Base):
    __tablename__ = "reservations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)

    party_size = Column(Integer, nullable=False)
    reserved_at = Column(DateTime, nullable=False)  # 예약된 방문 일시

    # 이 예약 시점에 계산된 보증금 정책 (신뢰점수 기반)
    deposit_rate = Column(Float, nullable=False, default=0.0)  # 0.0~1.0 (선결제 비율)
    deposit_flat_fee = Column(Integer, nullable=False, default=0)  # 가상 보증금(노쇼시만 청구) 금액

    status = Column(String, nullable=False, default="CONFIRMED")
    # CONFIRMED / FULFILLED / NO_SHOW / CANCELLED

    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="reservations")
    restaurant = relationship("Restaurant", back_populates="reservations")


class TrustScoreEvent(Base):
    """신뢰점수 변동 이력 (감사 로그 + 승급/회복 조건 판단 근거)"""
    __tablename__ = "trust_score_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reservation_id = Column(Integer, ForeignKey("reservations.id"), nullable=True)

    event_type = Column(Enum(EventType), nullable=False)
    score_delta = Column(Integer, nullable=False)
    score_before = Column(Integer, nullable=False)
    score_after = Column(Integer, nullable=False)

    note = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

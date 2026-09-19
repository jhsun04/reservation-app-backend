"""API 입출력용 Pydantic 스키마"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, computed_field

from app import subscription, trust_score
from app.models import EventType


class AuthRegister(BaseModel):
    name: str
    phone: str
    password: str


class AuthLogin(BaseModel):
    phone: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone: str
    trust_score: int
    consecutive_success_streak: int
    late_count: int
    flagged_for_review: bool

    @computed_field
    @property
    def trust_band(self) -> str:
        """VIP / STANDARD / CAUTION / RISK — 프론트엔드가 구간 경계값을 따로 알 필요 없게
        여기서 계산해서 내려준다 (신뢰점수 구간 로직은 app/trust_score.py 한 곳에만 존재해야 함)."""
        return trust_score.get_band(self.trust_score).value


class RestaurantAuthRegister(BaseModel):
    name: str
    category: Optional[str] = None
    # 코스/오마카세처럼 1인당 가격이 고정된 매장만 입력 (원). 없으면 CAUTION/RISK
    # 구간도 정액 노쇼시청구(HOLD) 방식으로 처리됨 — app/trust_score.py 참고.
    price_per_person: Optional[int] = None
    owner_phone: str
    password: str


class RestaurantAuthLogin(BaseModel):
    owner_phone: str
    password: str


class RestaurantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: Optional[str]
    subscription_tier: str
    risk_tolerance: str
    subscription_free_trial_ends_at: Optional[datetime]
    price_per_person: Optional[int]

    @computed_field
    @property
    def is_free_trial_active(self) -> bool:
        return subscription.is_free_trial_active(self.subscription_free_trial_ends_at)

    @computed_field
    @property
    def can_set_risk_tolerance(self) -> bool:
        """프론트엔드가 리스크 허용도 설정 UI를 보여줄지 판단할 때 쓰는 값
        (스탠다드 이상 티어만 True)."""
        return subscription.tier_at_least(self.subscription_tier, "STANDARD")


class SubscriptionTierUpdate(BaseModel):
    subscription_tier: str


class RiskToleranceUpdate(BaseModel):
    risk_tolerance: str


class PricePerPersonUpdate(BaseModel):
    # None으로 보내면 "코스가 없음" 상태로 되돌려서 CAUTION/RISK를 다시 HOLD 방식으로 되돌릴 수 있음
    price_per_person: Optional[int] = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class RestaurantTokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    restaurant: RestaurantOut


class ReservationCreate(BaseModel):
    restaurant_id: int
    party_size: int
    reserved_at: datetime


class ReservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    restaurant_id: int
    party_size: int
    reserved_at: datetime
    deposit_type: str  # NONE / HOLD / PREPAID
    deposit_amount: int  # 원 단위 총액 (party_size 반영됨)
    deposit_rate: Optional[float]  # PREPAID일 때만 값이 있음 (몇 %였는지 기록용)
    status: str


class ReservationEventIn(BaseModel):
    event_type: EventType
    # FORCE_MAJEURE일 때만 사용 (증빙 사유, 예: "질병", "사고")
    force_majeure_reason: Optional[str] = None
    # PARTIAL_NO_SHOW일 때만 사용: 일행 중 실제로 안 온 인원 수
    no_show_count: Optional[int] = None

"""API 입출력용 Pydantic 스키마"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, computed_field

from app import subscription, trust_score
from app.models import EventType


class UserCreate(BaseModel):
    name: str
    phone: str


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


class RestaurantCreate(BaseModel):
    name: str
    category: Optional[str] = None


class RestaurantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: Optional[str]
    subscription_tier: str
    risk_tolerance: str
    subscription_free_trial_ends_at: Optional[datetime]

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


class ReservationCreate(BaseModel):
    user_id: int
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
    deposit_rate: float
    deposit_flat_fee: int
    status: str


class ReservationEventIn(BaseModel):
    event_type: EventType
    # FORCE_MAJEURE일 때만 사용 (증빙 사유, 예: "질병", "사고")
    force_majeure_reason: Optional[str] = None

"""API 입출력용 Pydantic 스키마"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

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


class RestaurantCreate(BaseModel):
    name: str
    category: Optional[str] = None


class RestaurantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: Optional[str]
    subscription_tier: str


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

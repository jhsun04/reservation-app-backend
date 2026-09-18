"""
구독 티어 로직.

기획서 기준 3단계: 스타터(최초 3개월 무료 후 월 9,900원) / 스탠다드(3~4만원) / 프리미엄(7~9만원).
아직 실제 결제(PG) 연동은 없고, 관리자가 수동으로 티어를 부여하는 방식이다
(main.py의 PATCH /restaurants/{id}/subscription-tier).

이번에 실제로 기능 차이를 만든 건 "매장 리스크 허용도" 하나다 — 스탠다드 이상부터
매장이 직접 자기 가게의 노쇼 리스크 허용도(엄격/보통/관대)를 설정할 수 있고, 이게
신뢰점수 기반 보증금 계산에 곱해지는 배율로 반영된다. CRM 대시보드, POS 연동,
수요 예측 같은 나머지 티어별 기능은 아직 화면/로직이 없다 (다음 단계).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

FREE_TRIAL_DAYS = 90

TIER_ORDER = {"STARTER": 0, "STANDARD": 1, "PREMIUM": 2}
VALID_TIERS = set(TIER_ORDER.keys())

VALID_RISK_TOLERANCES = {"STRICT", "NORMAL", "LENIENT"}

# 리스크 허용도가 신뢰점수 기반 보증금(rate, flat_fee)에 곱해지는 배율.
# STRICT: 매장이 노쇼에 더 민감 -> 보증금을 더 세게. LENIENT: 반대로 더 관대하게.
RISK_TOLERANCE_MULTIPLIER = {
    "STRICT": 1.25,
    "NORMAL": 1.0,
    "LENIENT": 0.75,
}


class SubscriptionPermissionError(Exception):
    """현재 구독 티어로는 허용되지 않는 동작을 시도했을 때."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def tier_at_least(tier: str, required_tier: str) -> bool:
    return TIER_ORDER.get(tier, -1) >= TIER_ORDER.get(required_tier, 0)


def trial_ends_at(started_at: Optional[datetime] = None) -> datetime:
    started_at = started_at or datetime.utcnow()
    return started_at + timedelta(days=FREE_TRIAL_DAYS)


def is_free_trial_active(subscription_free_trial_ends_at: Optional[datetime], now: Optional[datetime] = None) -> bool:
    if subscription_free_trial_ends_at is None:
        return False
    now = now or datetime.utcnow()
    return now < subscription_free_trial_ends_at


def assert_can_set_risk_tolerance(tier: str) -> None:
    """스탠다드 미만 티어가 리스크 허용도를 바꾸려 하면 막는다."""
    if not tier_at_least(tier, "STANDARD"):
        raise SubscriptionPermissionError(
            "리스크 허용도 설정은 스탠다드 이상 구독에서만 가능해요. 현재 티어: " + tier
        )


def apply_risk_tolerance(deposit_policy: dict, risk_tolerance: str) -> dict:
    """신뢰점수만으로 계산된 보증금 정책에 매장의 리스크 허용도 배율을 곱해서 보정한다."""
    multiplier = RISK_TOLERANCE_MULTIPLIER.get(risk_tolerance, 1.0)
    adjusted_rate = deposit_policy["deposit_rate"] * multiplier
    adjusted_flat_fee = deposit_policy["deposit_flat_fee"] * multiplier
    return {
        "deposit_rate": round(min(1.0, max(0.0, adjusted_rate)), 4),
        "deposit_flat_fee": max(0, round(adjusted_flat_fee)),
    }

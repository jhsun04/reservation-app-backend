"""
신뢰점수 규칙 엔진.

기획서(예약 앱 기획서 — 캐치테이블 차별화 전략)에서 확정된 규칙을 그대로 코드로 옮긴 것.
아직 머신러닝은 붙이지 않았고, 전부 규칙 기반(rule-based)이다 — 실제 예약 데이터가
쌓이기 전에는 이 이상 복잡하게 만들 이유가 없다.

점수 구간 (0~100 클램프):
    80~100 VIP       : 보증금 없음
    50~79  STANDARD  : 노쇼 시에만 청구되는 가상 보증금 (인당 5,000원)
    25~49  CAUTION   : 예약금 선결제 (기본 25%, 20~30% 범위에서 조정 가능)
    0~24   RISK      : 예약금 선결제 (기본 75%, 50~100% 범위에서 조정 가능)
                       + 회복 조건: 이 구간에서는 스택 보너스 미적용, 최소 3회 성공 이행 +
                         진입 후 14일 경과해야 다음 구간으로 승급

설계상 임의로 정한 지점 (기획서에 명시되지 않아 구현하며 내린 결정, 필요하면 조정할 것):
- '노쇼'만 연속 성공 스택을 초기화한다. 취소(하루 전 / 임박)는 감점은 하지만
  스택 자체를 끊지는 않는다. 취소도 스택을 끊어야 한다면 RESETTING_EVENTS에
  CANCEL_DAY_BEFORE / CANCEL_IMMINENT를 추가하면 된다.
- CAUTION 구간 예약금율은 25%, RISK 구간은 75%로 각 범위의 중간값을 기본값으로 잡았다.

2차 보완 (사용자가 지적한 허점 반영, 이번에 추가):
- "예약금 몇 %"는 인원수만으로 예약하는 구조에서는 곱할 기준 금액이 없어서
  성립하지 않는 개념이었다. 그래서 매장이 price_per_person(코스가/객단가)을
  등록했을 때만 진짜 % 선결제(PREPAID)를 적용하고, 등록 안 했으면(대부분의
  캐주얼 식당) CAUTION/RISK도 STANDARD처럼 정액 노쇼시청구(HOLD)로 처리한다.
  자세한 이유는 get_deposit_policy 참고.
- 신규 유저(이 앱에서 예약 이력이 0건)는 시작 점수(60점)만 보면 STANDARD로
  분류되지만, "이력이 아예 없다"는 것 자체가 이력 기반 신뢰점수 시스템에서는
  가장 리스크가 큰 상태다. 그래서 첫 예약 1건만은 점수와 무관하게 최소
  CAUTION 수준으로 강제한다 (escalate_band_for_cold_start 참고).
- 그룹 예약 중 일부만 안 온 "부분 노쇼"는 신뢰점수는 깎지 않기로 했지만
  (기존 결정 유지), 매장이 실제로 보는 경제적 손해는 그대로 남는다. HOLD형
  보증금이면 안 온 인원 비율만큼 실제 청구액을 계산해준다
  (calculate_partial_no_show_charge 참고). PREPAID형(이미 전액 선결제된 경우)
  부분 환불 로직은 아직 안 만들었다 — 환불 정책을 먼저 정해야 해서 다음 과제로 남김.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from app.models import EventType, TrustBand, User

SCORE_MIN = 0
SCORE_MAX = 100
START_SCORE = 60

# 구간 경계값 (하한 포함)
BAND_THRESHOLDS = (
    (80, TrustBand.VIP),
    (50, TrustBand.STANDARD),
    (25, TrustBand.CAUTION),
    (0, TrustBand.RISK),
)

# 감점/가점이 고정값인 이벤트
FIXED_DELTAS = {
    EventType.NO_SHOW_SAME_DAY: -7,
    EventType.CANCEL_DAY_BEFORE: -4,
    EventType.CANCEL_IMMINENT: -5,
    EventType.NO_SHOW_DUPLICATE: -12,
    EventType.LATE: 0,
    EventType.PARTIAL_NO_SHOW: 0,
    EventType.FORCE_MAJEURE: 0,
}

# 연속 성공 스택 보너스: 1회째 +4, 2회째 +6, 3회째부터는 +8로 고정(상한)
STREAK_BONUS_BY_COUNT = {1: 4, 2: 6}
STREAK_BONUS_CAP = 8
FLAT_FULFILLED_BONUS_IN_RISK_BAND = 4  # RISK 구간에서는 스택 보너스 미적용

# 노쇼로 간주해 연속 성공 스택을 초기화하는 이벤트
STREAK_RESETTING_EVENTS = {EventType.NO_SHOW_SAME_DAY, EventType.NO_SHOW_DUPLICATE}

# 이벤트를 누가 발생시킬 수 있는지: 손님이 자기 예약을 취소/불가항력 신고하는 것과
# 매장이 손님의 실제 방문 여부(이행/노쇼/지각 등)를 보고하는 것은 다른 주체가 눌러야
# 의미가 있다. 손님 본인이 "저 노쇼했어요"를 스스로 누르는 건 실제 운영에서는 말이
# 안 되기 때문에, 매장 계정이 생긴 지금부터는 이벤트 종류별로 호출 주체를 나눈다.
CUSTOMER_INITIATED_EVENTS = {
    EventType.CANCEL_DAY_BEFORE,
    EventType.CANCEL_IMMINENT,
    EventType.FORCE_MAJEURE,
}
RESTAURANT_INITIATED_EVENTS = {
    EventType.FULFILLED,
    EventType.NO_SHOW_SAME_DAY,
    EventType.NO_SHOW_DUPLICATE,
    EventType.LATE,
    EventType.PARTIAL_NO_SHOW,
}

RISK_BAND_RECOVERY_MIN_SUCCESS = 3
RISK_BAND_RECOVERY_MIN_DAYS = 14

CAUTION_DEPOSIT_RATE_DEFAULT = 0.25  # 기획서 범위: 20~30% (price_per_person 등록된 매장에서만 적용)
RISK_DEPOSIT_RATE_DEFAULT = 0.75  # 기획서 범위: 50~100% (price_per_person 등록된 매장에서만 적용)

# price_per_person을 등록하지 않은 매장(대부분의 캐주얼 식당)에 적용되는 정액 노쇼시청구 금액
STANDARD_NO_SHOW_FLAT_FEE_PER_PERSON = 5000
CAUTION_NO_SHOW_FLAT_FEE_PER_PERSON = 15000
RISK_NO_SHOW_FLAT_FEE_PER_PERSON = 30000

# 콜드스타트 보정: 이력이 몇 건 이상 쌓여야 "이력 없음" 취급을 벗어나는지
COLD_START_RESERVATION_THRESHOLD = 1  # 첫 예약(0건째)에만 적용

# 밴드별 엄격도 순서 (숫자가 클수록 더 엄격 = 보증금 정책이 더 세짐)
BAND_SEVERITY = {TrustBand.VIP: 0, TrustBand.STANDARD: 1, TrustBand.CAUTION: 2, TrustBand.RISK: 3}


def clamp_score(score: int) -> int:
    return max(SCORE_MIN, min(SCORE_MAX, score))


def get_band(score: int) -> TrustBand:
    for threshold, band in BAND_THRESHOLDS:
        if score >= threshold:
            return band
    return TrustBand.RISK


def get_deposit_policy(
    band: TrustBand,
    party_size: int = 1,
    price_per_person: Optional[int] = None,
) -> dict:
    """예약 생성 시점에 저장해둘 보증금 정책을 반환한다.

    반환값의 deposit_type:
    - NONE: 보증금 없음 (VIP)
    - HOLD: 카드 사전승인만 걸어두고, 실제 노쇼가 발생했을 때만 청구
    - PREPAID: 예약 시점에 즉시 결제 (매장이 price_per_person을 등록한 경우에만
      CAUTION/RISK 구간에서 쓸 수 있음 — %를 곱할 기준 금액이 있어야 하기 때문)

    price_per_person이 없는 매장(순수 인원수 예약)에서는 CAUTION/RISK도 HOLD로
    처리하고, 대신 밴드가 나빠질수록 정액 청구 금액을 올린다.
    """
    if band == TrustBand.VIP:
        return {"deposit_type": "NONE", "deposit_amount": 0, "deposit_rate": None}

    if band == TrustBand.STANDARD:
        return {
            "deposit_type": "HOLD",
            "deposit_amount": STANDARD_NO_SHOW_FLAT_FEE_PER_PERSON * party_size,
            "deposit_rate": None,
        }

    if band == TrustBand.CAUTION:
        if price_per_person:
            amount = round(CAUTION_DEPOSIT_RATE_DEFAULT * price_per_person * party_size)
            return {"deposit_type": "PREPAID", "deposit_amount": amount, "deposit_rate": CAUTION_DEPOSIT_RATE_DEFAULT}
        return {
            "deposit_type": "HOLD",
            "deposit_amount": CAUTION_NO_SHOW_FLAT_FEE_PER_PERSON * party_size,
            "deposit_rate": None,
        }

    # RISK
    if price_per_person:
        amount = round(RISK_DEPOSIT_RATE_DEFAULT * price_per_person * party_size)
        return {"deposit_type": "PREPAID", "deposit_amount": amount, "deposit_rate": RISK_DEPOSIT_RATE_DEFAULT}
    return {
        "deposit_type": "HOLD",
        "deposit_amount": RISK_NO_SHOW_FLAT_FEE_PER_PERSON * party_size,
        "deposit_rate": None,
    }


def escalate_band_for_cold_start(band: TrustBand, total_reservations_count: int) -> TrustBand:
    """이력이 없는(총 예약 0건) 유저의 첫 예약은, 점수상 밴드가 그보다 느슨하더라도
    최소 CAUTION 수준으로 끌어올린다. 이미 CAUTION/RISK보다 엄격하면 그대로 둔다."""
    if total_reservations_count >= COLD_START_RESERVATION_THRESHOLD:
        return band
    if BAND_SEVERITY[band] < BAND_SEVERITY[TrustBand.CAUTION]:
        return TrustBand.CAUTION
    return band


def calculate_partial_no_show_charge(
    deposit_type: str,
    deposit_amount: int,
    party_size: int,
    no_show_count: int,
) -> int:
    """부분 노쇼(일행 중 일부만 불참) 시 실제로 청구할 금액.

    HOLD형 보증금은 원래 인원수 기준으로 총액이 계산돼 있으므로, 안 온 인원
    비율만큼만 청구한다. PREPAID형(이미 전액 선결제됨)은 부분 환불이 필요한
    문제라 아직 계산하지 않는다 (환불 정책 미정 — 0을 반환하고 별도 처리 필요).
    """
    if deposit_type != "HOLD" or party_size <= 0:
        return 0
    no_show_count = max(0, min(no_show_count, party_size))
    per_person = deposit_amount / party_size
    return round(per_person * no_show_count)


def _streak_bonus(streak_count: int) -> int:
    return STREAK_BONUS_BY_COUNT.get(streak_count, STREAK_BONUS_CAP)


@dataclass
class TrustScoreResult:
    score_before: int
    score_after: int
    delta: int
    note: Optional[str] = None


def apply_event(
    user: User,
    event_type: EventType,
    force_majeure_reason: Optional[str] = None,
    now: Optional[datetime] = None,
) -> TrustScoreResult:
    """유저에게 이벤트를 적용하고 신뢰점수를 갱신한다. user 객체를 in-place로 수정한다."""
    now = now or datetime.utcnow()
    score_before = user.trust_score
    band_before = get_band(score_before)
    note = None

    if event_type == EventType.FULFILLED:
        if band_before == TrustBand.RISK:
            # RISK 구간에서는 스택 보너스를 주지 않는다 (빠른 점수 세탁 방지)
            delta = FLAT_FULFILLED_BONUS_IN_RISK_BAND
            user.risk_band_success_count += 1
        else:
            user.consecutive_success_streak += 1
            delta = _streak_bonus(user.consecutive_success_streak)
    else:
        delta = FIXED_DELTAS[event_type]
        if event_type in STREAK_RESETTING_EVENTS:
            user.consecutive_success_streak = 0

        if event_type == EventType.LATE:
            user.late_count += 1
            note = "상습 지각 카운트 증가 (감점 없음)"

        if event_type == EventType.FORCE_MAJEURE:
            reasons = [r for r in (user.force_majeure_reasons or "").split(",") if r]
            if force_majeure_reason:
                if force_majeure_reason in reasons:
                    user.flagged_for_review = True
                    note = f"동일 사유('{force_majeure_reason}') 반복 제출 -> 검토 큐로 이동"
                reasons.append(force_majeure_reason)
                user.force_majeure_reasons = ",".join(reasons)

    score_after = clamp_score(score_before + delta)

    # RISK 구간 진입 시점 기록 (14일 경과 조건의 기준점)
    band_after_raw = get_band(score_after)
    if band_after_raw == TrustBand.RISK and band_before != TrustBand.RISK:
        user.risk_band_entered_at = now
        user.risk_band_success_count = 0

    # RISK -> 상위 구간 승급 게이트: 점수만으로는 부족하고 3회 이행 + 14일 경과가 필요
    if band_before == TrustBand.RISK and band_after_raw != TrustBand.RISK:
        days_elapsed = (
            (now - user.risk_band_entered_at).days
            if user.risk_band_entered_at
            else 0
        )
        eligible = (
            user.risk_band_success_count >= RISK_BAND_RECOVERY_MIN_SUCCESS
            and days_elapsed >= RISK_BAND_RECOVERY_MIN_DAYS
        )
        if not eligible:
            score_after = 24  # RISK 구간 최상단에 묶어둔다
            note = (note + " / " if note else "") + (
                f"RISK 구간 승급 보류: {user.risk_band_success_count}회 이행, "
                f"{days_elapsed}일 경과 (기준: {RISK_BAND_RECOVERY_MIN_SUCCESS}회 / "
                f"{RISK_BAND_RECOVERY_MIN_DAYS}일)"
            )

    user.trust_score = score_after
    return TrustScoreResult(score_before=score_before, score_after=score_after, delta=delta, note=note)

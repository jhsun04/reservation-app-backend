from datetime import datetime, timedelta

import pytest

from app.models import EventType, TrustBand, User
from app import trust_score


def make_user(score: int = 60, total_reservations_count: int = 5) -> User:
    # 기본값을 5로 둔 건 "이력 있는 유저" 기준 테스트가 대부분이라서.
    # 콜드스타트(이력 0건) 동작은 별도 테스트에서 total_reservations_count=0으로 명시.
    return User(
        id=1,
        name="테스트유저",
        phone="010-0000-0000",
        trust_score=score,
        consecutive_success_streak=0,
        late_count=0,
        risk_band_success_count=0,
        flagged_for_review=False,
        force_majeure_reasons=None,
        total_reservations_count=total_reservations_count,
    )


def test_streak_bonus_increases_then_caps_at_8():
    user = make_user(score=60)
    expected_deltas = [4, 6, 8, 8, 8]
    expected_scores = [64, 70, 78, 86, 94]

    for expected_delta, expected_score in zip(expected_deltas, expected_scores):
        result = trust_score.apply_event(user, EventType.FULFILLED)
        assert result.delta == expected_delta
        assert result.score_after == expected_score
    assert user.consecutive_success_streak == 5


def test_no_show_resets_streak_and_next_bonus_restarts():
    user = make_user(score=60)
    trust_score.apply_event(user, EventType.FULFILLED)  # streak=1, +4 -> 64
    trust_score.apply_event(user, EventType.FULFILLED)  # streak=2, +6 -> 70

    result = trust_score.apply_event(user, EventType.NO_SHOW_SAME_DAY)  # -7 -> 63
    assert result.delta == -7
    assert result.score_after == 63
    assert user.consecutive_success_streak == 0

    # 스택이 초기화됐으니 다음 정상 이행은 다시 +4부터
    result = trust_score.apply_event(user, EventType.FULFILLED)
    assert result.delta == 4
    assert result.score_after == 67


def test_cancel_day_before_deducts_but_does_not_reset_streak():
    """설계 노트: 취소는 감점하지만 '노쇼'가 아니므로 스택은 유지한다."""
    user = make_user(score=60)
    trust_score.apply_event(user, EventType.FULFILLED)  # streak=1, +4 -> 64
    result = trust_score.apply_event(user, EventType.CANCEL_DAY_BEFORE)  # -4 -> 60
    assert result.delta == -4
    assert user.consecutive_success_streak == 1  # 유지됨

    result = trust_score.apply_event(user, EventType.FULFILLED)  # streak=2, +6
    assert result.delta == 6


def test_duplicate_reservation_no_show_is_harshest_penalty_and_resets_streak():
    user = make_user(score=60)
    trust_score.apply_event(user, EventType.FULFILLED)  # streak=1
    result = trust_score.apply_event(user, EventType.NO_SHOW_DUPLICATE)
    assert result.delta == -12
    assert result.score_after == 52
    assert user.consecutive_success_streak == 0


def test_late_and_partial_no_show_do_not_change_score():
    user = make_user(score=60)
    result = trust_score.apply_event(user, EventType.LATE)
    assert result.delta == 0
    assert user.late_count == 1
    assert user.trust_score == 60

    result = trust_score.apply_event(user, EventType.PARTIAL_NO_SHOW)
    assert result.delta == 0
    assert user.trust_score == 60


def test_force_majeure_first_time_no_flag_second_time_same_reason_flags_review():
    user = make_user(score=60)
    result = trust_score.apply_event(user, EventType.FORCE_MAJEURE, force_majeure_reason="질병")
    assert result.delta == 0
    assert user.flagged_for_review is False

    result = trust_score.apply_event(user, EventType.FORCE_MAJEURE, force_majeure_reason="질병")
    assert user.flagged_for_review is True
    assert "반복 제출" in result.note


@pytest.mark.parametrize(
    "score,expected_band",
    [(100, TrustBand.VIP), (80, TrustBand.VIP), (79, TrustBand.STANDARD),
     (50, TrustBand.STANDARD), (49, TrustBand.CAUTION), (25, TrustBand.CAUTION),
     (24, TrustBand.RISK), (0, TrustBand.RISK)],
)
def test_band_boundaries(score, expected_band):
    assert trust_score.get_band(score) == expected_band


def test_deposit_policy_vip_and_standard_unaffected_by_price_per_person():
    assert trust_score.get_deposit_policy(TrustBand.VIP) == {
        "deposit_type": "NONE",
        "deposit_amount": 0,
        "deposit_rate": None,
    }
    assert trust_score.get_deposit_policy(TrustBand.STANDARD, party_size=2) == {
        "deposit_type": "HOLD",
        "deposit_amount": 10000,
        "deposit_rate": None,
    }


def test_deposit_policy_caution_and_risk_without_price_per_person_falls_back_to_flat_hold():
    """매장이 price_per_person(코스가)을 등록 안 했으면 -> %를 곱할 기준 금액이 없으니
    CAUTION/RISK도 정액 노쇼시청구(HOLD)로 처리해야 한다."""
    caution = trust_score.get_deposit_policy(TrustBand.CAUTION, party_size=2)
    assert caution["deposit_type"] == "HOLD"
    assert caution["deposit_rate"] is None
    assert caution["deposit_amount"] == trust_score.CAUTION_NO_SHOW_FLAT_FEE_PER_PERSON * 2

    risk = trust_score.get_deposit_policy(TrustBand.RISK, party_size=2)
    assert risk["deposit_type"] == "HOLD"
    assert risk["deposit_rate"] is None
    assert risk["deposit_amount"] == trust_score.RISK_NO_SHOW_FLAT_FEE_PER_PERSON * 2


def test_deposit_policy_caution_and_risk_with_price_per_person_uses_real_prepay_percentage():
    """매장이 코스가(1인 3만원)를 등록했으면, 이제서야 '%'가 진짜 의미를 갖는다."""
    caution = trust_score.get_deposit_policy(TrustBand.CAUTION, party_size=2, price_per_person=30000)
    assert caution["deposit_type"] == "PREPAID"
    assert 0.20 <= caution["deposit_rate"] <= 0.30
    assert caution["deposit_amount"] == round(0.25 * 30000 * 2)

    risk = trust_score.get_deposit_policy(TrustBand.RISK, party_size=2, price_per_person=30000)
    assert risk["deposit_type"] == "PREPAID"
    assert 0.50 <= risk["deposit_rate"] <= 1.0
    assert risk["deposit_amount"] == round(0.75 * 30000 * 2)


def test_escalate_band_for_cold_start_forces_caution_on_first_reservation_only():
    # 이력 0건이면 VIP/STANDARD 모두 CAUTION으로 강제 상향
    assert trust_score.escalate_band_for_cold_start(TrustBand.VIP, 0) == TrustBand.CAUTION
    assert trust_score.escalate_band_for_cold_start(TrustBand.STANDARD, 0) == TrustBand.CAUTION
    # 이미 그보다 엄격하면(RISK) 그대로 유지
    assert trust_score.escalate_band_for_cold_start(TrustBand.RISK, 0) == TrustBand.RISK
    # 이력이 한 건이라도 있으면 더 이상 강제하지 않음
    assert trust_score.escalate_band_for_cold_start(TrustBand.VIP, 1) == TrustBand.VIP


def test_calculate_partial_no_show_charge_prorates_by_missing_headcount():
    # 4인 중 1명 불참, HOLD 보증금 20000원(4인 기준) -> 1인분 5000원만 청구
    charge = trust_score.calculate_partial_no_show_charge(
        deposit_type="HOLD", deposit_amount=20000, party_size=4, no_show_count=1
    )
    assert charge == 5000

    # PREPAID형은 아직 환불 로직이 없으니 0 (부분 환불 정책 미정)
    charge = trust_score.calculate_partial_no_show_charge(
        deposit_type="PREPAID", deposit_amount=20000, party_size=4, no_show_count=1
    )
    assert charge == 0


def test_risk_band_promotion_is_gated_by_count_and_time_not_score_alone():
    user = make_user(score=30)  # CAUTION
    result = trust_score.apply_event(user, EventType.NO_SHOW_DUPLICATE)  # -12 -> 18, RISK 진입
    assert result.score_after == 18
    assert user.risk_band_entered_at is not None
    assert user.risk_band_success_count == 0

    # RISK 구간에서는 스택 보너스 없이 항상 +4, 3번 이행해도 시간 조건 전에는 24점에 묶인다
    trust_score.apply_event(user, EventType.FULFILLED)  # 18 -> 22
    trust_score.apply_event(user, EventType.FULFILLED)  # 22 -> 26 시도 -> 게이트에 걸려 24로 캡
    result = trust_score.apply_event(user, EventType.FULFILLED)
    assert result.score_after == 24  # 횟수는 채웠지만 아직 14일이 안 지남
    assert user.risk_band_success_count == 3

    # 14일이 지난 시점으로 이동 + 이미 3회 이행 조건을 만족했으므로 이제는 승급 허용
    later = user.risk_band_entered_at + timedelta(days=15)
    result = trust_score.apply_event(user, EventType.FULFILLED, now=later)
    assert result.score_after > 24
    assert trust_score.get_band(result.score_after) != TrustBand.RISK

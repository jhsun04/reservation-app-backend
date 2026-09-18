from datetime import datetime, timedelta

import pytest

from app.models import EventType, TrustBand, User
from app import trust_score


def make_user(score: int = 60) -> User:
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


def test_deposit_policy_per_band():
    assert trust_score.get_deposit_policy(TrustBand.VIP) == {"deposit_rate": 0.0, "deposit_flat_fee": 0}
    assert trust_score.get_deposit_policy(TrustBand.STANDARD, party_size=2) == {
        "deposit_rate": 0.0,
        "deposit_flat_fee": 10000,
    }
    caution = trust_score.get_deposit_policy(TrustBand.CAUTION)
    assert 0.20 <= caution["deposit_rate"] <= 0.30
    risk = trust_score.get_deposit_policy(TrustBand.RISK)
    assert 0.50 <= risk["deposit_rate"] <= 1.0


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

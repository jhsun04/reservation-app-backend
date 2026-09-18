from datetime import datetime, timedelta

import pytest

from app import subscription


# ---------------------------------------------------------------------------
# 순수 로직 단위 테스트 (app/subscription.py)
# ---------------------------------------------------------------------------

def test_tier_order_is_starter_lt_standard_lt_premium():
    assert subscription.tier_at_least("STARTER", "STARTER")
    assert not subscription.tier_at_least("STARTER", "STANDARD")
    assert subscription.tier_at_least("STANDARD", "STARTER")
    assert subscription.tier_at_least("STANDARD", "STANDARD")
    assert not subscription.tier_at_least("STANDARD", "PREMIUM")
    assert subscription.tier_at_least("PREMIUM", "STANDARD")


def test_assert_can_set_risk_tolerance_blocks_starter_allows_standard_and_premium():
    with pytest.raises(subscription.SubscriptionPermissionError):
        subscription.assert_can_set_risk_tolerance("STARTER")

    # 예외가 발생하지 않아야 통과
    subscription.assert_can_set_risk_tolerance("STANDARD")
    subscription.assert_can_set_risk_tolerance("PREMIUM")


def test_apply_risk_tolerance_strict_increases_lenient_decreases_normal_unchanged():
    base_policy = {"deposit_rate": 0.25, "deposit_flat_fee": 10000}

    strict = subscription.apply_risk_tolerance(base_policy, "STRICT")
    normal = subscription.apply_risk_tolerance(base_policy, "NORMAL")
    lenient = subscription.apply_risk_tolerance(base_policy, "LENIENT")

    assert strict == {"deposit_rate": 0.3125, "deposit_flat_fee": 12500}
    assert normal == {"deposit_rate": 0.25, "deposit_flat_fee": 10000}
    assert lenient == {"deposit_rate": 0.1875, "deposit_flat_fee": 7500}


def test_apply_risk_tolerance_deposit_rate_clamped_to_1():
    # RISK 구간 기본값(0.75)에 STRICT 배율(1.25)을 곱하면 0.9375로 1.0 미만이라 그대로,
    # 혹시 모를 극단값 대비 클램프 자체가 살아있는지도 같이 확인.
    high_policy = {"deposit_rate": 0.9, "deposit_flat_fee": 0}
    result = subscription.apply_risk_tolerance(high_policy, "STRICT")
    assert result["deposit_rate"] == 1.0  # 0.9 * 1.25 = 1.125 -> clamp


def test_trial_ends_at_is_90_days_after_started_at():
    started = datetime(2026, 1, 1)
    assert subscription.trial_ends_at(started) == started + timedelta(days=90)


def test_is_free_trial_active():
    now = datetime(2026, 6, 1)
    assert subscription.is_free_trial_active(None, now=now) is False
    assert subscription.is_free_trial_active(datetime(2026, 7, 1), now=now) is True
    assert subscription.is_free_trial_active(datetime(2026, 5, 1), now=now) is False


# ---------------------------------------------------------------------------
# 엔드포인트 통합 테스트: 티어 게이팅 + 보증금 배율 반영이 실제로 연결됐는지 확인
# ---------------------------------------------------------------------------

def _make_user(client, phone="010-1111-2222"):
    res = client.post("/users", json={"name": "테스트유저", "phone": phone})
    assert res.status_code == 200
    return res.json()


def _make_restaurant(client, name="테스트식당"):
    res = client.post("/restaurants", json={"name": name})
    assert res.status_code == 200
    return res.json()


def test_new_restaurant_starts_as_starter_with_free_trial(client):
    restaurant = _make_restaurant(client)
    assert restaurant["subscription_tier"] == "STARTER"
    assert restaurant["risk_tolerance"] == "NORMAL"
    assert restaurant["is_free_trial_active"] is True
    assert restaurant["can_set_risk_tolerance"] is False


def test_starter_tier_cannot_set_risk_tolerance(client):
    restaurant = _make_restaurant(client)

    res = client.patch(
        f"/restaurants/{restaurant['id']}/risk-tolerance",
        json={"risk_tolerance": "STRICT"},
    )
    assert res.status_code == 403


def test_upgrading_tier_unlocks_risk_tolerance_and_downgrade_resets_it(client):
    restaurant = _make_restaurant(client)
    rid = restaurant["id"]

    upgraded = client.patch(
        f"/restaurants/{rid}/subscription-tier", json={"subscription_tier": "STANDARD"}
    )
    assert upgraded.status_code == 200
    assert upgraded.json()["can_set_risk_tolerance"] is True

    set_strict = client.patch(
        f"/restaurants/{rid}/risk-tolerance", json={"risk_tolerance": "STRICT"}
    )
    assert set_strict.status_code == 200
    assert set_strict.json()["risk_tolerance"] == "STRICT"

    # 다시 STARTER로 내리면 리스크 허용도가 NORMAL로 강제 초기화된다
    downgraded = client.patch(
        f"/restaurants/{rid}/subscription-tier", json={"subscription_tier": "STARTER"}
    )
    assert downgraded.status_code == 200
    assert downgraded.json()["risk_tolerance"] == "NORMAL"


def test_deposit_reflects_restaurant_risk_tolerance_multiplier(client):
    user = _make_user(client)
    restaurant = _make_restaurant(client)
    rid = restaurant["id"]

    # 신뢰점수 60점(STANDARD 구간)으로 예약하면 리스크 허용도는 아직 NORMAL -> 배율 1.0
    first = client.post(
        "/reservations",
        json={
            "user_id": user["id"],
            "restaurant_id": rid,
            "party_size": 2,
            "reserved_at": "2026-10-01T19:00:00",
        },
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["deposit_rate"] == 0.0
    assert first_body["deposit_flat_fee"] == 10000  # 5000원 * 2인, NORMAL 배율 1.0

    # 매장을 STANDARD로 올리고 리스크 허용도를 STRICT로 설정
    client.patch(f"/restaurants/{rid}/subscription-tier", json={"subscription_tier": "STANDARD"})
    client.patch(f"/restaurants/{rid}/risk-tolerance", json={"risk_tolerance": "STRICT"})

    # 유저 신뢰점수를 CAUTION 구간(25~49)까지 떨어뜨린다: 60 -4 -4 -5 = 47
    for event_type in ("CANCEL_DAY_BEFORE", "CANCEL_DAY_BEFORE", "CANCEL_IMMINENT"):
        event_res = client.post(
            f"/reservations/{first_body['id']}/events", json={"event_type": event_type}
        )
        assert event_res.status_code == 200

    user_after = client.get(f"/users/{user['id']}").json()
    assert user_after["trust_band"] == "CAUTION"

    second = client.post(
        "/reservations",
        json={
            "user_id": user["id"],
            "restaurant_id": rid,
            "party_size": 2,
            "reserved_at": "2026-10-02T19:00:00",
        },
    )
    assert second.status_code == 200
    second_body = second.json()
    # CAUTION 기본 보증금율 0.25 * STRICT 배율 1.25 = 0.3125
    assert second_body["deposit_rate"] == 0.3125
    assert second_body["deposit_flat_fee"] == 0

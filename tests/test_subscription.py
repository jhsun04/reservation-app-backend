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


def test_apply_risk_tolerance_strict_increases_lenient_decreases_normal_unchanged_prepaid():
    base_policy = {"deposit_type": "PREPAID", "deposit_rate": 0.25, "deposit_amount": 10000}

    strict = subscription.apply_risk_tolerance(base_policy, "STRICT")
    normal = subscription.apply_risk_tolerance(base_policy, "NORMAL")
    lenient = subscription.apply_risk_tolerance(base_policy, "LENIENT")

    assert strict == {"deposit_type": "PREPAID", "deposit_rate": 0.3125, "deposit_amount": 12500}
    assert normal == {"deposit_type": "PREPAID", "deposit_rate": 0.25, "deposit_amount": 10000}
    assert lenient == {"deposit_type": "PREPAID", "deposit_rate": 0.1875, "deposit_amount": 7500}


def test_apply_risk_tolerance_on_hold_policy_only_scales_amount_rate_stays_none():
    # price_per_person 없는 매장(HOLD)은 % 개념이 없으니 deposit_rate는 계속 None이어야 함
    hold_policy = {"deposit_type": "HOLD", "deposit_rate": None, "deposit_amount": 10000}
    strict = subscription.apply_risk_tolerance(hold_policy, "STRICT")
    assert strict == {"deposit_type": "HOLD", "deposit_rate": None, "deposit_amount": 12500}


def test_apply_risk_tolerance_deposit_rate_clamped_to_1():
    # RISK 구간 기본값(0.75)에 STRICT 배율(1.25)을 곱하면 0.9375로 1.0 미만이라 그대로,
    # 혹시 모를 극단값 대비 클램프 자체가 살아있는지도 같이 확인.
    high_policy = {"deposit_type": "PREPAID", "deposit_rate": 0.9, "deposit_amount": 0}
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
    """회원가입 후 {user 딕셔너리 + auth 헤더}를 합쳐서 반환 — 예약/이벤트 API가
    이제 JWT 인증을 요구하므로, 반환값의 user 필드들 접근은 그대로 두고
    ["headers"]를 요청마다 같이 넘겨주면 된다."""
    res = client.post(
        "/auth/register",
        json={"name": "테스트유저", "phone": phone, "password": "테스트비번1234"},
    )
    assert res.status_code == 200
    body = res.json()
    user = dict(body["user"])
    user["headers"] = {"Authorization": f"Bearer {body['access_token']}"}
    return user


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


def test_first_ever_reservation_is_escalated_to_caution_even_at_standard_score(client):
    """콜드스타트 보정: 이력 0건인 유저의 첫 예약은 점수(60점=STANDARD)와 무관하게
    최소 CAUTION 수준으로 처리돼야 한다. 이 매장은 price_per_person을 등록 안 했으니
    (순수 인원수 예약) CAUTION도 정액 HOLD 방식이어야 한다 — %문제 해결의 핵심 케이스."""
    user = _make_user(client)
    restaurant = _make_restaurant(client)
    rid = restaurant["id"]

    first = client.post(
        "/reservations",
        json={
            "restaurant_id": rid,
            "party_size": 2,
            "reserved_at": "2026-10-01T19:00:00",
        },
        headers=user["headers"],
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["deposit_type"] == "HOLD"
    assert first_body["deposit_rate"] is None
    assert first_body["deposit_amount"] == 30000  # CAUTION 정액 15,000원 * 2인 (콜드스타트로 강제 상향)

    # 두 번째 예약부터는 이력이 생겼으니 콜드스타트 보정이 빠지고, 실제 점수(60=STANDARD)대로 처리
    second = client.post(
        "/reservations",
        json={
            "restaurant_id": rid,
            "party_size": 2,
            "reserved_at": "2026-10-02T19:00:00",
        },
        headers=user["headers"],
    )
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["deposit_type"] == "HOLD"
    assert second_body["deposit_amount"] == 10000  # STANDARD 정액 5,000원 * 2인


def test_deposit_reflects_price_per_person_and_risk_tolerance_multiplier(client):
    """price_per_person(코스가)을 등록한 매장에서만 진짜 %선결제가 성립한다는 걸 end-to-end로 확인."""
    user = _make_user(client)
    restaurant = _make_restaurant(client)
    rid = restaurant["id"]

    # 오마카세처럼 1인당 3만원 코스가 있는 매장으로 설정
    price_res = client.patch(f"/restaurants/{rid}/price-per-person", json={"price_per_person": 30000})
    assert price_res.status_code == 200
    assert price_res.json()["price_per_person"] == 30000

    # 첫 예약(콜드스타트로 CAUTION 강제) -> 이제 price_per_person이 있으니 PREPAID로 계산됨
    first = client.post(
        "/reservations",
        json={
            "restaurant_id": rid,
            "party_size": 2,
            "reserved_at": "2026-10-01T19:00:00",
        },
        headers=user["headers"],
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["deposit_type"] == "PREPAID"
    assert first_body["deposit_rate"] == 0.25
    assert first_body["deposit_amount"] == 15000  # 0.25 * 30000 * 2인

    # 매장을 STANDARD로 올리고 리스크 허용도를 STRICT로 설정
    client.patch(f"/restaurants/{rid}/subscription-tier", json={"subscription_tier": "STANDARD"})
    client.patch(f"/restaurants/{rid}/risk-tolerance", json={"risk_tolerance": "STRICT"})

    # 유저 신뢰점수를 CAUTION 구간(25~49)까지 떨어뜨린다: 60 -4 -4 -5 = 47
    for event_type in ("CANCEL_DAY_BEFORE", "CANCEL_DAY_BEFORE", "CANCEL_IMMINENT"):
        event_res = client.post(
            f"/reservations/{first_body['id']}/events",
            json={"event_type": event_type},
            headers=user["headers"],
        )
        assert event_res.status_code == 200

    user_after = client.get(f"/users/{user['id']}").json()
    assert user_after["trust_band"] == "CAUTION"

    second = client.post(
        "/reservations",
        json={
            "restaurant_id": rid,
            "party_size": 2,
            "reserved_at": "2026-10-02T19:00:00",
        },
        headers=user["headers"],
    )
    assert second.status_code == 200
    second_body = second.json()
    # CAUTION 기본 보증금율 0.25 * STRICT 배율 1.25 = 0.3125, 금액도 그만큼 커짐
    assert second_body["deposit_rate"] == 0.3125
    assert second_body["deposit_amount"] == 18750  # 15000 * 1.25


def test_partial_no_show_charges_only_the_missing_headcount_not_full_deposit(client):
    """그룹 예약 중 일부만 노쇼: 신뢰점수는 안 깎이지만(기존 결정), 매장 손해는
    실제로 안 온 인원 비율만큼 charged_amount로 잡혀야 한다."""
    user = _make_user(client)
    restaurant = _make_restaurant(client)
    rid = restaurant["id"]

    # 이력을 하나 쌓아서 콜드스타트 보정을 피하고(순수 STANDARD 밴드로 테스트), party_size=4
    client.post(
        "/reservations",
        json={"restaurant_id": rid, "party_size": 4, "reserved_at": "2026-10-01T19:00:00"},
        headers=user["headers"],
    )
    reservation = client.post(
        "/reservations",
        json={"restaurant_id": rid, "party_size": 4, "reserved_at": "2026-10-05T19:00:00"},
        headers=user["headers"],
    ).json()
    assert reservation["deposit_type"] == "HOLD"
    assert reservation["deposit_amount"] == 20000  # STANDARD 5,000원 * 4인

    event_res = client.post(
        f"/reservations/{reservation['id']}/events",
        json={"event_type": "PARTIAL_NO_SHOW", "no_show_count": 1},
        headers=user["headers"],
    )
    assert event_res.status_code == 200
    body = event_res.json()
    assert body["score_delta"] == 0  # 신뢰점수는 그대로 (기존 결정 유지)
    assert body["charged_amount"] == 5000  # 4인분 20000원 중 1인분만 청구

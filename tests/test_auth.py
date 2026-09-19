"""전화번호+비밀번호 인증 테스트: 회원가입/로그인, 그리고 예약 관련 엔드포인트가
실제로 로그인한 본인(손님) 또는 그 예약을 받은 매장만 건드릴 수 있는지 확인한다."""


def _register(client, phone="010-9999-0000", password="비밀번호1234"):
    res = client.post(
        "/auth/register",
        json={"name": "인증테스트", "phone": phone, "password": password},
    )
    assert res.status_code == 200
    return res.json()


def _register_restaurant(client, owner_phone="010-8888-0000", password="사장님비번1234", name="인증테스트식당"):
    res = client.post(
        "/restaurant-auth/register",
        json={"name": name, "owner_phone": owner_phone, "password": password},
    )
    assert res.status_code == 200
    return res.json()


def test_register_returns_token_and_user(client):
    body = _register(client, phone="010-1000-0001", password="비밀번호1234")
    assert body["access_token"]
    assert body["user"]["phone"] == "010-1000-0001"
    assert body["user"]["trust_score"] == 60


def test_register_then_login_succeeds(client):
    _register(client, phone="010-1000-0011", password="비밀번호1234")

    login = client.post(
        "/auth/login", json={"phone": "010-1000-0011", "password": "비밀번호1234"}
    )
    assert login.status_code == 200
    body = login.json()
    assert body["access_token"]
    assert body["user"]["phone"] == "010-1000-0011"


def test_duplicate_phone_registration_rejected(client):
    _register(client, phone="010-1000-0002")
    dup = client.post(
        "/auth/register",
        json={"name": "다른이름", "phone": "010-1000-0002", "password": "다른비번1234"},
    )
    assert dup.status_code == 400


def test_login_with_wrong_password_rejected(client):
    _register(client, phone="010-1000-0003", password="올바른비번1234")
    res = client.post(
        "/auth/login", json={"phone": "010-1000-0003", "password": "틀린비번"}
    )
    assert res.status_code == 401


def test_reservation_creation_requires_auth(client):
    restaurant = _register_restaurant(client, owner_phone="010-8888-0001")["restaurant"]
    res = client.post(
        "/reservations",
        json={
            "restaurant_id": restaurant["id"],
            "party_size": 2,
            "reserved_at": "2026-10-01T19:00:00",
        },
    )
    assert res.status_code == 401


def test_user_cannot_view_or_cancel_another_users_reservation(client):
    restaurant = _register_restaurant(client, owner_phone="010-8888-0002")["restaurant"]

    owner = _register(client, phone="010-1000-0004", password="비밀번호1234")
    other = _register(client, phone="010-1000-0005", password="비밀번호1234")

    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}

    reservation = client.post(
        "/reservations",
        json={
            "restaurant_id": restaurant["id"],
            "party_size": 2,
            "reserved_at": "2026-10-01T19:00:00",
        },
        headers=owner_headers,
    ).json()

    # 다른 사람이 내 예약 목록을 조회하려 하면 403
    forbidden_list = client.get(
        f"/users/{owner['user']['id']}/reservations", headers=other_headers
    )
    assert forbidden_list.status_code == 403

    # 본인은 조회 가능
    ok_list = client.get(
        f"/users/{owner['user']['id']}/reservations", headers=owner_headers
    )
    assert ok_list.status_code == 200

    # 다른 손님이 내 예약을 취소(손님 쪽 이벤트)하려 하면 403
    forbidden_event = client.post(
        f"/reservations/{reservation['id']}/events",
        json={"event_type": "CANCEL_DAY_BEFORE"},
        headers=other_headers,
    )
    assert forbidden_event.status_code == 403


def test_only_the_receiving_restaurant_can_mark_fulfilled_or_no_show(client):
    """이행/노쇼처럼 실제 방문 여부를 판단하는 이벤트는 그 예약을 받은 매장만 처리할 수 있다.
    손님 본인 토큰으로도, 다른 매장 토큰으로도 처리할 수 없어야 한다."""
    restaurant_auth = _register_restaurant(client, owner_phone="010-8888-0003", name="식당A")
    restaurant = restaurant_auth["restaurant"]
    restaurant_headers = {"Authorization": f"Bearer {restaurant_auth['access_token']}"}

    other_restaurant_auth = _register_restaurant(client, owner_phone="010-8888-0004", name="식당B")
    other_restaurant_headers = {"Authorization": f"Bearer {other_restaurant_auth['access_token']}"}

    user = _register(client, phone="010-1000-0006", password="비밀번호1234")
    user_headers = {"Authorization": f"Bearer {user['access_token']}"}

    reservation = client.post(
        "/reservations",
        json={
            "restaurant_id": restaurant["id"],
            "party_size": 2,
            "reserved_at": "2026-10-01T19:00:00",
        },
        headers=user_headers,
    ).json()

    # 손님 본인은 "이행 완료" 같은 매장 쪽 이벤트를 스스로 누를 수 없다
    forbidden_self_report = client.post(
        f"/reservations/{reservation['id']}/events",
        json={"event_type": "FULFILLED"},
        headers=user_headers,
    )
    assert forbidden_self_report.status_code == 403

    # 이 예약을 받지 않은 다른 매장도 처리할 수 없다
    forbidden_other_restaurant = client.post(
        f"/reservations/{reservation['id']}/events",
        json={"event_type": "FULFILLED"},
        headers=other_restaurant_headers,
    )
    assert forbidden_other_restaurant.status_code == 403

    # 실제로 이 예약을 받은 매장은 처리할 수 있다
    ok = client.post(
        f"/reservations/{reservation['id']}/events",
        json={"event_type": "FULFILLED"},
        headers=restaurant_headers,
    )
    assert ok.status_code == 200


def test_restaurant_owner_can_manage_own_restaurant_but_not_others(client):
    """구독 티어/리스크 허용도/코스가 설정, 그리고 예약 목록 조회는 이제 그 매장
    사장님 본인만 할 수 있어야 한다."""
    mine_auth = _register_restaurant(client, owner_phone="010-8888-0005", name="내식당")
    mine = mine_auth["restaurant"]
    mine_headers = {"Authorization": f"Bearer {mine_auth['access_token']}"}

    other_auth = _register_restaurant(client, owner_phone="010-8888-0006", name="남의식당")
    other_headers = {"Authorization": f"Bearer {other_auth['access_token']}"}

    # 인증 없이는 아예 접근 불가
    no_auth = client.patch(f"/restaurants/{mine['id']}/price-per-person", json={"price_per_person": 20000})
    assert no_auth.status_code == 401

    # 다른 매장 사장님은 내 매장 설정을 바꿀 수 없다
    forbidden = client.patch(
        f"/restaurants/{mine['id']}/price-per-person",
        json={"price_per_person": 20000},
        headers=other_headers,
    )
    assert forbidden.status_code == 403

    forbidden_list = client.get(f"/restaurants/{mine['id']}/reservations", headers=other_headers)
    assert forbidden_list.status_code == 403

    # 본인은 가능
    ok = client.patch(
        f"/restaurants/{mine['id']}/price-per-person",
        json={"price_per_person": 20000},
        headers=mine_headers,
    )
    assert ok.status_code == 200
    assert ok.json()["price_per_person"] == 20000

    ok_list = client.get(f"/restaurants/{mine['id']}/reservations", headers=mine_headers)
    assert ok_list.status_code == 200

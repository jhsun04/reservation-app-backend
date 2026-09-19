"""전화번호+비밀번호 인증 테스트: 회원가입/로그인, 그리고 예약 관련 엔드포인트가
실제로 로그인한 본인만 건드릴 수 있는지 확인한다."""


def _register(client, phone="010-9999-0000", password="비밀번호1234"):
    res = client.post(
        "/auth/register",
        json={"name": "인증테스트", "phone": phone, "password": password},
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
    restaurant = client.post("/restaurants", json={"name": "인증테스트식당"}).json()
    res = client.post(
        "/reservations",
        json={
            "restaurant_id": restaurant["id"],
            "party_size": 2,
            "reserved_at": "2026-10-01T19:00:00",
        },
    )
    assert res.status_code == 401


def test_user_cannot_view_or_act_on_another_users_reservation(client):
    restaurant = client.post("/restaurants", json={"name": "인증테스트식당2"}).json()

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

    # 다른 사람이 내 예약에 이벤트(이행/노쇼 등)를 적용하려 하면 403
    forbidden_event = client.post(
        f"/reservations/{reservation['id']}/events",
        json={"event_type": "FULFILLED"},
        headers=other_headers,
    )
    assert forbidden_event.status_code == 403

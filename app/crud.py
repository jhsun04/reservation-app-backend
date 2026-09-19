from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app import auth, models, schemas, subscription, trust_score


def register_user(db: Session, register_in: schemas.AuthRegister) -> models.User:
    if get_user_by_phone(db, register_in.phone) is not None:
        raise ValueError("phone_already_registered")
    user = models.User(
        name=register_in.name,
        phone=register_in.phone,
        hashed_password=auth.hash_password(register_in.password),
        trust_score=trust_score.START_SCORE,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, phone: str, password: str) -> models.User:
    user = get_user_by_phone(db, phone)
    if user is None or not auth.verify_password(password, user.hashed_password):
        raise ValueError("invalid_credentials")
    return user


def get_user_by_phone(db: Session, phone: str) -> models.User | None:
    return db.query(models.User).filter(models.User.phone == phone).first()


def get_restaurant_by_owner_phone(db: Session, owner_phone: str) -> models.Restaurant | None:
    return db.query(models.Restaurant).filter(models.Restaurant.owner_phone == owner_phone).first()


def register_restaurant(db: Session, register_in: schemas.RestaurantAuthRegister) -> models.Restaurant:
    if get_restaurant_by_owner_phone(db, register_in.owner_phone) is not None:
        raise ValueError("phone_already_registered")
    now = datetime.utcnow()
    restaurant = models.Restaurant(
        name=register_in.name,
        category=register_in.category,
        price_per_person=register_in.price_per_person,
        owner_phone=register_in.owner_phone,
        hashed_password=auth.hash_password(register_in.password),
        subscription_started_at=now,
        # 가입 즉시 무료 체험 기간(90일)을 부여한다.
        subscription_free_trial_ends_at=subscription.trial_ends_at(now),
    )
    db.add(restaurant)
    db.commit()
    db.refresh(restaurant)
    return restaurant


def authenticate_restaurant(db: Session, owner_phone: str, password: str) -> models.Restaurant:
    restaurant = get_restaurant_by_owner_phone(db, owner_phone)
    if restaurant is None or not auth.verify_password(password, restaurant.hashed_password):
        raise ValueError("invalid_credentials")
    return restaurant


def update_price_per_person(db: Session, restaurant_id: int, price_per_person: Optional[int]) -> models.Restaurant:
    restaurant = db.query(models.Restaurant).get(restaurant_id)
    if restaurant is None:
        raise ValueError("restaurant_not_found")
    restaurant.price_per_person = price_per_person
    db.commit()
    db.refresh(restaurant)
    return restaurant


def update_subscription_tier(db: Session, restaurant_id: int, tier: str) -> models.Restaurant:
    restaurant = db.query(models.Restaurant).get(restaurant_id)
    if restaurant is None:
        raise ValueError("restaurant_not_found")
    if tier not in subscription.VALID_TIERS:
        raise ValueError("invalid_tier")

    restaurant.subscription_tier = tier
    # 스탠다드 미만으로 내려가면 리스크 허용도 설정 권한도 같이 없어지므로 기본값(NORMAL)으로 되돌린다.
    if not subscription.tier_at_least(tier, "STANDARD"):
        restaurant.risk_tolerance = "NORMAL"

    db.commit()
    db.refresh(restaurant)
    return restaurant


def update_risk_tolerance(db: Session, restaurant_id: int, risk_tolerance: str) -> models.Restaurant:
    restaurant = db.query(models.Restaurant).get(restaurant_id)
    if restaurant is None:
        raise ValueError("restaurant_not_found")
    if risk_tolerance not in subscription.VALID_RISK_TOLERANCES:
        raise ValueError("invalid_risk_tolerance")

    # 티어 미달이면 SubscriptionPermissionError를 던진다 (호출부에서 403으로 변환).
    subscription.assert_can_set_risk_tolerance(restaurant.subscription_tier)

    restaurant.risk_tolerance = risk_tolerance
    db.commit()
    db.refresh(restaurant)
    return restaurant


def list_restaurants(db: Session) -> list[models.Restaurant]:
    return db.query(models.Restaurant).order_by(models.Restaurant.id).all()


def list_user_reservations(db: Session, user_id: int) -> list[models.Reservation]:
    return (
        db.query(models.Reservation)
        .filter(models.Reservation.user_id == user_id)
        .order_by(models.Reservation.reserved_at.desc())
        .all()
    )


def list_restaurant_reservations(db: Session, restaurant_id: int) -> list[models.Reservation]:
    return (
        db.query(models.Reservation)
        .filter(models.Reservation.restaurant_id == restaurant_id)
        .order_by(models.Reservation.reserved_at.desc())
        .all()
    )


def create_reservation(
    db: Session, reservation_in: schemas.ReservationCreate, user_id: int
) -> models.Reservation:
    user = db.query(models.User).get(user_id)
    if user is None:
        raise ValueError("user_not_found")
    restaurant = db.query(models.Restaurant).get(reservation_in.restaurant_id)
    if restaurant is None:
        raise ValueError("restaurant_not_found")

    band = trust_score.get_band(user.trust_score)
    # 이력이 없는(총 예약 0건) 유저의 첫 예약은 점수와 무관하게 최소 CAUTION 수준으로.
    band = trust_score.escalate_band_for_cold_start(band, user.total_reservations_count)

    policy = trust_score.get_deposit_policy(
        band,
        party_size=reservation_in.party_size,
        price_per_person=restaurant.price_per_person,
    )
    # 신뢰점수만으로 계산된 보증금에 매장이 설정한 리스크 허용도 배율을 반영한다.
    policy = subscription.apply_risk_tolerance(policy, restaurant.risk_tolerance)

    reservation = models.Reservation(
        user_id=user_id,
        restaurant_id=reservation_in.restaurant_id,
        party_size=reservation_in.party_size,
        reserved_at=reservation_in.reserved_at,
        deposit_type=policy["deposit_type"],
        deposit_amount=policy["deposit_amount"],
        deposit_rate=policy["deposit_rate"],
        status="CONFIRMED",
    )
    db.add(reservation)
    user.total_reservations_count += 1
    db.commit()
    db.refresh(reservation)
    return reservation


def apply_reservation_event(
    db: Session,
    reservation_id: int,
    event_in: schemas.ReservationEventIn,
    actor_type: str,
    actor_id: int,
) -> models.TrustScoreEvent:
    reservation = db.query(models.Reservation).get(reservation_id)
    if reservation is None:
        raise ValueError("reservation_not_found")

    # 취소/불가항력 신고는 손님 본인만, 실제 방문 여부(이행/노쇼/지각 등) 보고는
    # 그 예약을 받은 매장만 할 수 있다 — 손님이 스스로 "노쇼했어요"를 누르는 건
    # 실제 운영에서 말이 안 되기 때문에 이벤트 종류별로 호출 주체를 나눈다.
    if event_in.event_type in trust_score.CUSTOMER_INITIATED_EVENTS:
        if actor_type != "user" or reservation.user_id != actor_id:
            raise ValueError("not_your_reservation")
    elif event_in.event_type in trust_score.RESTAURANT_INITIATED_EVENTS:
        if actor_type != "restaurant" or reservation.restaurant_id != actor_id:
            raise ValueError("not_your_restaurant_reservation")
    else:
        raise ValueError("unsupported_event_type")

    user = db.query(models.User).get(reservation.user_id)
    if user is None:
        raise ValueError("user_not_found")

    result = trust_score.apply_event(
        user=user,
        event_type=event_in.event_type,
        force_majeure_reason=event_in.force_majeure_reason,
    )

    # 부분 노쇼: 신뢰점수는 안 깎지만(기존 결정 유지), HOLD형 보증금이면
    # 실제로 안 온 인원 비율만큼 매장에 청구할 금액을 계산해서 기록해준다.
    charged_amount = 0
    if event_in.event_type == models.EventType.PARTIAL_NO_SHOW and event_in.no_show_count is not None:
        charged_amount = trust_score.calculate_partial_no_show_charge(
            deposit_type=reservation.deposit_type,
            deposit_amount=reservation.deposit_amount,
            party_size=reservation.party_size,
            no_show_count=event_in.no_show_count,
        )
        if reservation.deposit_type == "PREPAID":
            result.note = (result.note + " / " if result.note else "") + (
                "PREPAID(선결제)형 보증금의 부분 노쇼 환불 로직은 아직 없음 — 별도 정산 필요"
            )

    # 예약 상태 갱신
    status_map = {
        models.EventType.FULFILLED: "FULFILLED",
        models.EventType.NO_SHOW_SAME_DAY: "NO_SHOW",
        models.EventType.NO_SHOW_DUPLICATE: "NO_SHOW",
        models.EventType.CANCEL_DAY_BEFORE: "CANCELLED",
        models.EventType.CANCEL_IMMINENT: "CANCELLED",
        models.EventType.PARTIAL_NO_SHOW: "FULFILLED",
    }
    if event_in.event_type in status_map:
        reservation.status = status_map[event_in.event_type]

    event_record = models.TrustScoreEvent(
        user_id=user.id,
        reservation_id=reservation.id,
        event_type=event_in.event_type,
        score_delta=result.delta,
        score_before=result.score_before,
        score_after=result.score_after,
        charged_amount=charged_amount,
        note=result.note,
    )
    db.add(event_record)
    db.commit()
    db.refresh(event_record)
    return event_record

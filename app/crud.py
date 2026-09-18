from datetime import datetime

from sqlalchemy.orm import Session

from app import models, schemas, subscription, trust_score


def create_user(db: Session, user_in: schemas.UserCreate) -> models.User:
    user = models.User(
        name=user_in.name,
        phone=user_in.phone,
        trust_score=trust_score.START_SCORE,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_phone(db: Session, phone: str) -> models.User | None:
    return db.query(models.User).filter(models.User.phone == phone).first()


def create_restaurant(db: Session, restaurant_in: schemas.RestaurantCreate) -> models.Restaurant:
    now = datetime.utcnow()
    restaurant = models.Restaurant(
        name=restaurant_in.name,
        category=restaurant_in.category,
        subscription_started_at=now,
        # 가입 즉시 무료 체험 기간(90일)을 부여한다.
        subscription_free_trial_ends_at=subscription.trial_ends_at(now),
    )
    db.add(restaurant)
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


def create_reservation(db: Session, reservation_in: schemas.ReservationCreate) -> models.Reservation:
    user = db.query(models.User).get(reservation_in.user_id)
    if user is None:
        raise ValueError("user_not_found")
    restaurant = db.query(models.Restaurant).get(reservation_in.restaurant_id)
    if restaurant is None:
        raise ValueError("restaurant_not_found")

    band = trust_score.get_band(user.trust_score)
    policy = trust_score.get_deposit_policy(band, party_size=reservation_in.party_size)
    # 신뢰점수만으로 계산된 보증금에 매장이 설정한 리스크 허용도 배율을 반영한다.
    policy = subscription.apply_risk_tolerance(policy, restaurant.risk_tolerance)

    reservation = models.Reservation(
        user_id=reservation_in.user_id,
        restaurant_id=reservation_in.restaurant_id,
        party_size=reservation_in.party_size,
        reserved_at=reservation_in.reserved_at,
        deposit_rate=policy["deposit_rate"],
        deposit_flat_fee=policy["deposit_flat_fee"],
        status="CONFIRMED",
    )
    db.add(reservation)
    db.commit()
    db.refresh(reservation)
    return reservation


def apply_reservation_event(
    db: Session,
    reservation_id: int,
    event_in: schemas.ReservationEventIn,
) -> models.TrustScoreEvent:
    reservation = db.query(models.Reservation).get(reservation_id)
    if reservation is None:
        raise ValueError("reservation_not_found")
    user = db.query(models.User).get(reservation.user_id)
    if user is None:
        raise ValueError("user_not_found")

    result = trust_score.apply_event(
        user=user,
        event_type=event_in.event_type,
        force_majeure_reason=event_in.force_majeure_reason,
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
        note=result.note,
    )
    db.add(event_record)
    db.commit()
    db.refresh(event_record)
    return event_record

from datetime import datetime

from sqlalchemy.orm import Session

from app import models, schemas, trust_score


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
    restaurant = models.Restaurant(name=restaurant_in.name, category=restaurant_in.category)
    db.add(restaurant)
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

    band = trust_score.get_band(user.trust_score)
    policy = trust_score.get_deposit_policy(band, party_size=reservation_in.party_size)

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

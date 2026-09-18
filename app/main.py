from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from app import crud, models, schemas
from app.database import Base, SessionLocal, engine, get_db

Base.metadata.create_all(bind=engine)

app = FastAPI(title="예약 앱 MVP — 신뢰점수 기반 예약 시스템")


@app.post("/users", response_model=schemas.UserOut)
def create_user(user_in: schemas.UserCreate, db: Session = Depends(get_db)):
    return crud.create_user(db, user_in)


@app.get("/users/{user_id}", response_model=schemas.UserOut)
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    return user


@app.post("/restaurants", response_model=schemas.RestaurantOut)
def create_restaurant(restaurant_in: schemas.RestaurantCreate, db: Session = Depends(get_db)):
    return crud.create_restaurant(db, restaurant_in)


@app.get("/restaurants/{restaurant_id}", response_model=schemas.RestaurantOut)
def get_restaurant(restaurant_id: int, db: Session = Depends(get_db)):
    restaurant = db.query(models.Restaurant).get(restaurant_id)
    if restaurant is None:
        raise HTTPException(status_code=404, detail="restaurant_not_found")
    return restaurant


@app.post("/reservations", response_model=schemas.ReservationOut)
def create_reservation(reservation_in: schemas.ReservationCreate, db: Session = Depends(get_db)):
    try:
        return crud.create_reservation(db, reservation_in)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/reservations/{reservation_id}/events")
def apply_reservation_event(
    reservation_id: int,
    event_in: schemas.ReservationEventIn,
    db: Session = Depends(get_db),
):
    try:
        event = crud.apply_reservation_event(db, reservation_id, event_in)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {
        "event_type": event.event_type,
        "score_delta": event.score_delta,
        "score_before": event.score_before,
        "score_after": event.score_after,
        "note": event.note,
    }

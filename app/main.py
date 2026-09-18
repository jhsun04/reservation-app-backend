from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app import crud, models, schemas
from app.database import Base, SessionLocal, engine, get_db

Base.metadata.create_all(bind=engine)

app = FastAPI(title="예약 앱 MVP — 신뢰점수 기반 예약 시스템")

# 개발 단계 CORS 설정: 플러터 앱(모바일/웹)에서 자유롭게 호출할 수 있도록 전체 허용.
# 운영 배포 시에는 allow_origins를 실제 프론트엔드 도메인으로 좁혀야 한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/users", response_model=schemas.UserOut)
def create_user(user_in: schemas.UserCreate, db: Session = Depends(get_db)):
    return crud.create_user(db, user_in)


@app.get("/users/{user_id}", response_model=schemas.UserOut)
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    return user


@app.get("/users/by-phone/{phone}", response_model=schemas.UserOut)
def get_user_by_phone(phone: str, db: Session = Depends(get_db)):
    """플러터 앱의 '전화번호로 시작하기' 흐름용: 없으면 404 -> 프론트에서 POST /users로 새로 만듦."""
    user = crud.get_user_by_phone(db, phone)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    return user


@app.get("/users/{user_id}/reservations", response_model=list[schemas.ReservationOut])
def list_user_reservations(user_id: int, db: Session = Depends(get_db)):
    return crud.list_user_reservations(db, user_id)


@app.post("/restaurants", response_model=schemas.RestaurantOut)
def create_restaurant(restaurant_in: schemas.RestaurantCreate, db: Session = Depends(get_db)):
    return crud.create_restaurant(db, restaurant_in)


@app.get("/restaurants", response_model=list[schemas.RestaurantOut])
def list_restaurants(db: Session = Depends(get_db)):
    return crud.list_restaurants(db)


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

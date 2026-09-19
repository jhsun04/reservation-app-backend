from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app import auth, crud, models, schemas, subscription
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


@app.post("/auth/register", response_model=schemas.TokenOut)
def register(register_in: schemas.AuthRegister, db: Session = Depends(get_db)):
    try:
        user = crud.register_user(db, register_in)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    token = auth.create_access_token(user.id)
    return schemas.TokenOut(access_token=token, user=user)


@app.post("/auth/login", response_model=schemas.TokenOut)
def login(login_in: schemas.AuthLogin, db: Session = Depends(get_db)):
    try:
        user = crud.authenticate_user(db, login_in.phone, login_in.password)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    token = auth.create_access_token(user.id)
    return schemas.TokenOut(access_token=token, user=user)


@app.get("/users/{user_id}", response_model=schemas.UserOut)
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    return user


@app.get("/users/{user_id}/reservations", response_model=list[schemas.ReservationOut])
def list_user_reservations(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    auth.require_self(user_id, current_user)
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


@app.patch("/restaurants/{restaurant_id}/subscription-tier", response_model=schemas.RestaurantOut)
def update_subscription_tier(
    restaurant_id: int,
    payload: schemas.SubscriptionTierUpdate,
    db: Session = Depends(get_db),
):
    """지금은 결제(PG) 연동 전이라 관리자가 수동으로 티어를 바꿔주는 용도.
    실제 결제 붙으면 이 엔드포인트를 결제 성공 콜백에서 호출하는 식으로 바뀔 것."""
    if payload.subscription_tier not in subscription.VALID_TIERS:
        raise HTTPException(status_code=422, detail="invalid_tier")
    try:
        return crud.update_subscription_tier(db, restaurant_id, payload.subscription_tier)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.patch("/restaurants/{restaurant_id}/risk-tolerance", response_model=schemas.RestaurantOut)
def update_risk_tolerance(
    restaurant_id: int,
    payload: schemas.RiskToleranceUpdate,
    db: Session = Depends(get_db),
):
    """스탠다드 이상 구독 매장만 자기 가게의 노쇼 리스크 허용도를 바꿀 수 있다."""
    if payload.risk_tolerance not in subscription.VALID_RISK_TOLERANCES:
        raise HTTPException(status_code=422, detail="invalid_risk_tolerance")
    try:
        return crud.update_risk_tolerance(db, restaurant_id, payload.risk_tolerance)
    except subscription.SubscriptionPermissionError as e:
        raise HTTPException(status_code=403, detail=e.message)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.patch("/restaurants/{restaurant_id}/price-per-person", response_model=schemas.RestaurantOut)
def update_price_per_person(
    restaurant_id: int,
    payload: schemas.PricePerPersonUpdate,
    db: Session = Depends(get_db),
):
    """코스/오마카세처럼 1인당 가격이 고정된 매장만 설정. 이 값이 있어야 CAUTION/RISK
    구간에서 진짜 %선결제(PREPAID)가 적용되고, 없으면 정액 노쇼시청구(HOLD)로 처리된다."""
    try:
        return crud.update_price_per_person(db, restaurant_id, payload.price_per_person)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/reservations", response_model=schemas.ReservationOut)
def create_reservation(
    reservation_in: schemas.ReservationCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    try:
        return crud.create_reservation(db, reservation_in, user_id=current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/reservations/{reservation_id}/events")
def apply_reservation_event(
    reservation_id: int,
    event_in: schemas.ReservationEventIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    try:
        event = crud.apply_reservation_event(
            db, reservation_id, event_in, acting_user_id=current_user.id
        )
    except ValueError as e:
        if str(e) == "not_your_reservation":
            raise HTTPException(status_code=403, detail=str(e))
        raise HTTPException(status_code=404, detail=str(e))

    return {
        "event_type": event.event_type,
        "score_delta": event.score_delta,
        "score_before": event.score_before,
        "score_after": event.score_after,
        "charged_amount": event.charged_amount,
        "note": event.note,
    }

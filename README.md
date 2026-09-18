# 예약 앱 MVP — 신뢰점수 기반 예약 백엔드

기획서("예약 앱 기획서 — 캐치테이블 차별화 전략")에서 확정된 신뢰점수 규칙과
예약 흐름을 그대로 구현한 백엔드 1단계 MVP다. 이 API를 호출하는 플러터 프론트엔드는
`reservation_app_flutter` 폴더에 별도로 있다.

## 지금 되는 것

- 유저 / 식당 / 예약 생성, 식당 목록 조회, 유저별 예약 목록 조회
- 전화번호로 기존 유저 조회 (`GET /users/by-phone/{phone}`) — 프론트엔드 로그인용
- 예약 생성 시 유저의 현재 신뢰점수 구간(VIP/STANDARD/CAUTION/RISK)에 맞춰
  보증금 정책(선결제 비율 또는 노쇼시만 청구되는 가상 보증금)을 자동 계산
- 예약에 이벤트(정상 이행 / 당일 노쇼 / 하루 전 취소 / 임박 취소 / 중복예약형 노쇼 /
  지각 / 부분 노쇼 / 불가항력)를 적용하면 신뢰점수가 기획서 규칙대로 갱신
- RISK 구간(25점 미만)은 스택 보너스 없이 flat +4점만 주고, 3회 이행 + 14일 경과
  조건을 채우기 전까지는 24점에 묶어두는 회복 게이트까지 구현됨
- `UserOut` 응답에 `trust_band`를 서버에서 계산해서 내려줌 (프론트엔드가 구간 경계값을
  따로 알 필요 없게)
- 구독 티어(스타터/스탠다드/프리미엄)가 실제로 기능 차이를 만듦: 스탠다드 이상부터
  매장이 자기 가게의 "리스크 허용도"(엄격/보통/관대)를 설정할 수 있고, 이게 신뢰점수
  기반 보증금 계산에 배율(1.25 / 1.0 / 0.75)로 반영됨. 스타터 매장이 시도하면 403 에러.
  매장 생성 시 자동으로 90일 무료 체험이 시작됨 (`is_free_trial_active`로 확인 가능)

## 아직 안 된 것 (다음 단계)

- 실제 결제/PG 연동 (지금은 관리자가 PATCH로 수동으로 티어를 바꿔주는 방식이고,
  보증금도 "금액 계산"까지만 하고 실제 청구는 안 함)
- 구독 티어별 나머지 기능 차이 (CRM 대시보드, POS 연동, 수요 예측 등 — 지금은
  리스크 허용도 하나만 연결됨)
- 진짜 후기 리뷰 시스템
- 유료 노출/추천 알고리즘
- 인증(로그인), 권한 분리(식당 사장님 vs 손님)

## 실행 방법

```bash
cd reservation_app
pip install -r requirements.txt

# 서버 실행 (로컬 SQLite로 자동 생성됨: reservation_app.db)
uvicorn app.main:app --reload

# http://localhost:8000/docs 에서 Swagger UI로 바로 테스트 가능
```

## 테스트

신뢰점수 규칙(스택 보너스 상한, 노쇼 시 스택 초기화, 구간별 보증금, RISK 구간
회복 게이트 등)이 기획서대로 동작하는지 검증하는 단위 테스트:

```bash
python3 -m pytest tests/ -v
```

현재 26개 테스트 모두 통과 (신뢰점수 16개 + 구독 티어/리스크 허용도 게이팅 10개).

## 빠른 사용 예시

```bash
# 유저 생성 (신뢰점수 60점으로 시작)
curl -X POST localhost:8000/users -H "Content-Type: application/json" \
  -d '{"name":"김철수","phone":"010-1234-5678"}'

# 식당 생성
curl -X POST localhost:8000/restaurants -H "Content-Type: application/json" \
  -d '{"name":"맛있는집","category":"한식"}'

# 예약 생성 (신뢰점수 구간에 맞춰 보증금 자동 계산됨)
curl -X POST localhost:8000/reservations -H "Content-Type: application/json" \
  -d '{"user_id":1,"restaurant_id":1,"party_size":2,"reserved_at":"2026-10-01T19:00:00"}'

# 노쇼 처리 -> 신뢰점수 -7점
curl -X POST localhost:8000/reservations/1/events -H "Content-Type: application/json" \
  -d '{"event_type":"NO_SHOW_SAME_DAY"}'

# 매장을 스탠다드로 업그레이드 (지금은 결제 연동 전이라 수동 호출)
curl -X PATCH localhost:8000/restaurants/1/subscription-tier -H "Content-Type: application/json" \
  -d '{"subscription_tier":"STANDARD"}'

# 스탠다드 이상이면 리스크 허용도 변경 가능 (스타터에서 시도하면 403)
curl -X PATCH localhost:8000/restaurants/1/risk-tolerance -H "Content-Type: application/json" \
  -d '{"risk_tolerance":"STRICT"}'
```

## 코드 읽을 때 참고할 것

- `app/trust_score.py` — 신뢰점수 규칙의 전부가 여기 있다. 파일 맨 위 docstring에
  "기획서에 명시되지 않아 구현하며 임의로 정한 것"을 따로 적어뒀으니 꼭 한번
  읽어보고 맞는지 확인할 것 (예: 취소는 노쇼와 달리 연속 성공 스택을 끊지 않도록
  구현했음. 다르게 가고 싶으면 `STREAK_RESETTING_EVENTS`만 수정하면 됨).
- `app/models.py` — DB 테이블 구조.
- `app/crud.py`, `app/main.py` — API 엔드포인트.

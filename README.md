# 예약 앱 MVP — 신뢰점수 기반 예약 백엔드

기획서("예약 앱 기획서 — 캐치테이블 차별화 전략")에서 확정된 신뢰점수 규칙과
예약 흐름을 그대로 구현한 백엔드 1단계 MVP다. 프론트엔드(안드로이드/플러터)는
아직 없고, 이 API를 그대로 붙여서 쓰면 된다.

## 지금 되는 것

- 유저 / 식당 / 예약 생성
- 예약 생성 시 유저의 현재 신뢰점수 구간(VIP/STANDARD/CAUTION/RISK)에 맞춰
  보증금 정책(선결제 비율 또는 노쇼시만 청구되는 가상 보증금)을 자동 계산
- 예약에 이벤트(정상 이행 / 당일 노쇼 / 하루 전 취소 / 임박 취소 / 중복예약형 노쇼 /
  지각 / 부분 노쇼 / 불가항력)를 적용하면 신뢰점수가 기획서 규칙대로 갱신
- RISK 구간(25점 미만)은 스택 보너스 없이 flat +4점만 주고, 3회 이행 + 14일 경과
  조건을 채우기 전까지는 24점에 묶어두는 회복 게이트까지 구현됨

## 아직 안 된 것 (다음 단계)

- 실제 결제/PG 연동 (지금은 보증금 "금액 계산"까지만 하고 실제 청구는 안 함)
- 구독료 티어별 기능 제한 (지금은 restaurants.subscription_tier 컬럼만 있고 로직에서 안 씀)
- 진짜 후기 리뷰 시스템
- 유료 노출/추천 알고리즘
- 인증(로그인), 권한 분리(식당 사장님 vs 손님)
- 프론트엔드

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

현재 16개 테스트 모두 통과.

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
```

## 코드 읽을 때 참고할 것

- `app/trust_score.py` — 신뢰점수 규칙의 전부가 여기 있다. 파일 맨 위 docstring에
  "기획서에 명시되지 않아 구현하며 임의로 정한 것"을 따로 적어뒀으니 꼭 한번
  읽어보고 맞는지 확인할 것 (예: 취소는 노쇼와 달리 연속 성공 스택을 끊지 않도록
  구현했음. 다르게 가고 싶으면 `STREAK_RESETTING_EVENTS`만 수정하면 됨).
- `app/models.py` — DB 테이블 구조.
- `app/crud.py`, `app/main.py` — API 엔드포인트.

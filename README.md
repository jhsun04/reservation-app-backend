# 예약 앱 MVP — 신뢰점수 기반 예약 백엔드

기획서("예약 앱 기획서 — 캐치테이블 차별화 전략")에서 확정된 신뢰점수 규칙과
예약 흐름을 그대로 구현한 백엔드 1단계 MVP다. 이 API를 호출하는 플러터 프론트엔드는
`reservation_app_flutter` 폴더에 별도로 있다.

## 지금 되는 것

- **전화번호 + 비밀번호 인증 (JWT).** `POST /auth/register`로 가입, `POST /auth/login`으로
  로그인하면 JWT 토큰을 받는다. 예약 생성(`POST /reservations`), 내 예약 목록 조회
  (`GET /users/{id}/reservations`), 예약 이벤트 적용(`POST /reservations/{id}/events`)은
  이 토큰(`Authorization: Bearer <token>`)이 있어야 호출 가능하고, 본인 소유가 아닌
  예약을 건드리려 하면 403이 뜬다. 비밀번호는 `pbkdf2_hmac`(표준 라이브러리)으로
  해시해서 저장하고 평문은 저장 안 함.
  **범위 안내**: 이건 "손님" 계정 인증만 해결한 것이고, 식당(매장) 관리 엔드포인트
  (식당 생성/구독 티어/리스크 허용도/코스가 설정)는 아직 인증이 없다 — "사장님용
  별도 입점 절차"가 생기기 전까지는 데모 편의상 계속 열어둘 예정.
- 유저 / 식당 / 예약 생성, 식당 목록 조회, 유저별 예약 목록 조회
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
- **"예약금 몇 %"의 기준 금액 문제를 해결함.** 순수 인원수 예약에는 %를 곱할 기준
  금액이 없어서, 매장이 `price_per_person`(코스가/오마카세 등 1인 고정가)을 등록했을
  때만 진짜 %선결제(`deposit_type: PREPAID`)를 적용하고, 등록 안 한 매장(대부분의
  캐주얼 식당)은 CAUTION/RISK도 STANDARD처럼 정액 노쇼시청구(`deposit_type: HOLD`,
  1인당 15,000원/30,000원)로 처리함
- **콜드스타트(이력 없는 신규 유저) 보정.** 신뢰점수 60점으로 시작하면 STANDARD로
  분류되지만, "이력이 아예 없다"는 것 자체가 이력 기반 시스템에서 가장 위험한 상태라서,
  유저의 첫 예약 1건만은 점수와 무관하게 최소 CAUTION 수준으로 강제함
- **부분 노쇼의 실제 경제적 손해 반영.** 일행 중 일부만 안 온 경우 신뢰점수는 안
  깎지만(기존 결정 유지), HOLD형 보증금이면 안 온 인원 비율만큼 실제 청구 금액을
  계산해서 `charged_amount`로 내려줌 (`POST /reservations/{id}/events`에
  `no_show_count` 파라미터로 전달)

## 알고 있지만 코드로는 못 푸는 것 (사용자 판단이 필요한 열린 과제)

- **매장 유치의 계란-닭 문제**: 정액 구독료는 매장 입장에서 "초기 트래픽 없이 돈만
  나가는" 리스크라서, 신생 앱이 첫 매장들을 어떻게 설득할지는 로직이 아니라
  영업/가격 전략의 문제
- **PREPAID형 보증금의 부분 노쇼 환불 로직**: 코스가 있는 매장에서 일행 일부만 안 왔을
  때 이미 선결제된 금액 중 일부를 환불해줘야 하는데, 환불 정책(전액/비율/수수료 차감
  등)을 먼저 정해야 구현 가능 (지금은 0을 반환하고 미구현 상태로 남겨둠)
- **그룹 예약의 신뢰점수 책임 소재**: 지금은 예약자 한 명의 신뢰점수만 관리하는데,
  실제로 안 온 사람이 예약자 본인이 아닐 수도 있음 — 이건 본인 인증(SMS 등)이 있어야
  풀리는 문제라 인증 기능 자체가 먼저 필요함
- **보증금 몰수/청구의 법적·PG 실무**: 카드 사전승인(HOLD), 노쇼 시 실제 청구,
  환불 분쟁, 이의제기 절차 — PG사 계약과 이용약관 정비가 필요한 부분

## 아직 안 된 것 (다음 단계)

- 실제 결제/PG 연동 (지금은 관리자가 PATCH로 수동으로 티어를 바꿔주는 방식이고,
  보증금도 "금액 계산"까지만 하고 실제 청구는 안 함)
- 구독 티어별 나머지 기능 차이 (CRM 대시보드, POS 연동, 수요 예측 등 — 지금은
  리스크 허용도 하나만 연결됨)
- 진짜 후기 리뷰 시스템
- 유료 노출/추천 알고리즘
- 식당(사장님) 계정 인증 및 권한 분리 — 지금은 손님 인증만 있고 식당 관리 엔드포인트는
  아직 열려 있음
- 그룹 예약에서 "안 온 사람 = 예약자 본인이 아닐 수도 있는" 문제 — 비밀번호 인증까지는
  해결이 안 되고, 실제로 닫으려면 유료 SMS 실명 인증 등이 필요함

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

현재 39개 테스트 모두 통과 (신뢰점수/보증금 정책 20개 + 구독 티어·리스크 허용도 게이팅 13개
+ 인증/권한 6개).

## 빠른 사용 예시

```bash
# 회원가입 (신뢰점수 60점으로 시작, 토큰이 바로 발급됨)
curl -X POST localhost:8000/auth/register -H "Content-Type: application/json" \
  -d '{"name":"김철수","phone":"010-1234-5678","password":"비밀번호1234"}'
# -> {"access_token": "...", "token_type": "bearer", "user": {...}}

# 로그인 (이미 가입한 경우)
curl -X POST localhost:8000/auth/login -H "Content-Type: application/json" \
  -d '{"phone":"010-1234-5678","password":"비밀번호1234"}'

# 이후 요청에는 access_token을 Authorization 헤더로 실어보낸다
TOKEN="위에서 받은 access_token"

# 식당 생성 (아직 인증 없음 — 데모 편의)
curl -X POST localhost:8000/restaurants -H "Content-Type: application/json" \
  -d '{"name":"맛있는집","category":"한식"}'

# 예약 생성 (신뢰점수 구간에 맞춰 보증금 자동 계산됨, 로그인한 본인 명의로 생성됨)
curl -X POST localhost:8000/reservations \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"restaurant_id":1,"party_size":2,"reserved_at":"2026-10-01T19:00:00"}'

# 노쇼 처리 -> 신뢰점수 -7점 (본인 예약만 가능, 남의 예약이면 403)
curl -X POST localhost:8000/reservations/1/events \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"event_type":"NO_SHOW_SAME_DAY"}'

# 매장을 스탠다드로 업그레이드 (지금은 결제 연동 전이라 수동 호출)
curl -X PATCH localhost:8000/restaurants/1/subscription-tier -H "Content-Type: application/json" \
  -d '{"subscription_tier":"STANDARD"}'

# 스탠다드 이상이면 리스크 허용도 변경 가능 (스타터에서 시도하면 403)
curl -X PATCH localhost:8000/restaurants/1/risk-tolerance -H "Content-Type: application/json" \
  -d '{"risk_tolerance":"STRICT"}'

# 코스/오마카세처럼 1인당 가격이 고정된 매장이면 등록 -> 그래야 CAUTION/RISK가
# 정액이 아니라 진짜 %선결제(PREPAID)로 계산됨
curl -X PATCH localhost:8000/restaurants/1/price-per-person -H "Content-Type: application/json" \
  -d '{"price_per_person":30000}'

# 그룹 예약 중 일부만 노쇼 -> 신뢰점수는 안 깎이지만 안 온 인원만큼 청구액(charged_amount) 계산됨
curl -X POST localhost:8000/reservations/1/events \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"event_type":"PARTIAL_NO_SHOW","no_show_count":1}'
```

> **DB 스키마가 또 바뀜**: `User`에 `hashed_password`가 추가됐다 (nullable=False라서
> 예전 데이터가 있으면 마이그레이션 없이는 서버가 못 켜진다). 로컬에서 실행 중이면
> 지난번처럼 `reservation_app.db` 파일을 지우고 서버를 다시 켜서 새 스키마로
> 재생성해야 한다. 기존에 `POST /users`, `GET /users/by-phone/{phone}`으로 만든
> 계정은 비밀번호가 없으므로 어차피 다시 가입해야 한다.

## 코드 읽을 때 참고할 것

- `app/trust_score.py` — 신뢰점수 규칙의 전부가 여기 있다. 파일 맨 위 docstring에
  "기획서에 명시되지 않아 구현하며 임의로 정한 것"을 따로 적어뒀으니 꼭 한번
  읽어보고 맞는지 확인할 것 (예: 취소는 노쇼와 달리 연속 성공 스택을 끊지 않도록
  구현했음. 다르게 가고 싶으면 `STREAK_RESETTING_EVENTS`만 수정하면 됨).
- `app/auth.py` — 비밀번호 해시(pbkdf2_hmac)와 JWT 발급/검증, `get_current_user`/
  `require_self` 의존성.
- `app/models.py` — DB 테이블 구조.
- `app/crud.py`, `app/main.py` — API 엔드포인트.

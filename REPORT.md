# Secure Coding 과제 보고서 — Tiny Second-hand Shopping Platform

이름: `조원재`
반: `36반`
GitHub: `https://github.com/oneJaep/secure-coding`

---

## 1. 개요

"중고거래가 가능한 플랫폼(Tiny Second-hand Shopping Platform)"을
Flask + Flask-SocketIO + SQLAlchemy로 구현했다. 시작 코드(`app.py` 초기 버전)는 회원가입/로그인/상품
등록·조회·상세/전체 채팅/신고 정도만 구현되어 있었고, 보안 요소는 전혀 적용되어 있지 않았다(비밀번호
평문 저장, CSRF 없음, 세션 설정 없음 등). 본 보고서는 요구사항 분석부터 구현, 체크리스트 기반 테스트,
유지보수 계획까지 개발 전 과정과, 그 과정에서 발견/보완한 보안 약점을 정리한다.

## 2. 요구사항 분석

### 2.1 기능 요구사항

| 대분류 | 세부 기능 | 비고 |
|---|---|---|
| 유저 관리 | 회원가입, 로그인/로그아웃, 사용자 조회(공개 프로필), 마이페이지(소개글·비밀번호 변경) | |
| 상품 관리 | 상품 등록, 목록/검색, 상세 조회, 내 상품 수정·삭제 | 검색은 슬라이드 24p 요구사항에는 있으나 예시 코드 범위 밖 → 직접 설계 |
| 유저 소통 | 전체 채팅(실시간), 1:1 채팅 | 1:1 채팅은 신규 설계 |
| 악성 유저/상품 필터링 | 신고(사유 작성), 신고 누적 시 상품 자동 차단·유저 자동 휴면 | 신고 남용 방지(중복신고 차단) 포함 |
| 송금 | 유저 간 잔액 송금, 거래내역 조회 | 신규 설계(요구사항엔 있으나 시스템 설계는 자체 진행) |
| 관리자 | 유저 정지/해제/관리자 지정, 상품 차단/해제/삭제, 신고 내역 조회 | 신규 설계 |

### 2.2 비기능 요구사항

- 보안: `secure_coding_checklist.csv`의 5개 섹션, 28개 항목(아래 5장에서 매핑)
- 디자인: 기존 미니멀(Notion 스타일) 유지, CSS를 `static/css/style.css`로 분리
- 서버 구조: SQLAlchemy ORM 기반으로 전환(원래 raw sqlite3 사용 → 파라미터 바인딩은 되어 있었으나 ORM 요구사항 미충족)

## 3. 시스템 설계

### 3.1 데이터 모델

- **User**: id, username, password_hash, bio, balance, is_admin, is_active(휴면 여부), failed_attempts, locked_until
- **Product**: id, title, description, price, seller_id, status(active/blocked)
- **Report**: id, reporter_id, target_type(user/product), target_id, reason + `(reporter_id, target_type, target_id)` 유니크 제약(중복신고 방지)
- **Message**: 1:1 채팅 메시지(sender_id, receiver_id, content)
- **Transaction**: 송금 기록(sender_id, receiver_id, amount)

### 3.2 페이지/라우트 구조

```
/                     기본 페이지
/register /login /logout
/dashboard            상품 목록 + 검색(?q=) + 전체 채팅
/profile              소개글/비밀번호 변경
/user/<id>            공개 프로필(사용자 조회)
/product/new /<id> /<id>/edit /<id>/delete
/my/products          내 상품 관리
/report               신고 접수
/transfer             송금 + 거래내역
/messages, /messages/<id>   쪽지함 / 1:1 채팅방
/admin, /admin/users, /admin/products, /admin/reports   관리자
```

### 3.3 실시간 통신 설계

- 전체 채팅: 기존 소켓 이벤트(`send_message`) 유지, 서버가 세션에서 사용자명을 직접 조회(클라이언트가
  보낸 username은 신뢰하지 않음 — 기존 코드는 클라이언트 입력을 그대로 신뢰하는 스푸핑 취약점이 있었음)
- 1:1 채팅: 두 사용자 id를 정렬해 만든 room(`dm_<id1>_<id2>`)에 join 후 메시지 송수신, DB에 영속 저장

## 4. 시스템 구현

- `extensions.py`: `db`, `socketio`, `csrf`(Flask-WTF), `limiter`(Flask-Limiter) 인스턴스
- `models.py`: SQLAlchemy 모델 정의
- `app.py`: 라우트, 보안 헤더, 에러 핸들러, 인증/인가 데코레이터, Socket.IO 이벤트
- `static/css/style.css`, `static/js/global_chat.js`, `static/js/dm_chat.js`: 인라인 스타일/스크립트를 분리해
  CSP에서 `unsafe-inline` 없이 스크립트를 허용할 수 있도록 함
- `templates/`: 기존 템플릿 보강 + 신규 템플릿(사용자 프로필, 상품 수정, 내 상품, 송금, 쪽지함/채팅방,
  관리자 대시보드 3종, 에러 페이지 3종)

## 5. 발견한 보안 약점과 개선 내역

시작 코드에서 발견한 주요 문제와, `secure_coding_checklist.csv` 항목별 대응:

| 시작 코드의 문제 | 개선 내용 |
|---|---|
| 비밀번호를 평문으로 저장·비교 | `werkzeug.security.generate_password_hash`(scrypt, 호출마다 랜덤 salt 자동 부여)로 해시 저장 후 `check_password_hash`로 검증 |
| CSRF 보호 전무 | `Flask-WTF`의 `CSRFProtect`를 전역 적용, 모든 POST 폼에 `csrf_token()` 히든 필드 추가. 토큰 없이 POST 시 400 반환(직접 확인) |
| `SECRET_KEY = 'secret!'` 하드코딩 | 환경변수(`SECRET_KEY`) 우선, 없으면 `instance/secret_key`에 랜덤 값을 생성해 영속 저장(재시작해도 세션 유지, 코드에 노출 안 됨) |
| 세션 쿠키 설정 없음 | `HttpOnly`, `SameSite=Lax`, (운영 환경) `Secure`, `PERMANENT_SESSION_LIFETIME=30분` 적용 |
| 로그인 실패 제한 없음 | 계정별 5회 실패 시 15분 잠금 + IP당 분당 10회로 `flask-limiter` 이중 제한 |
| 회원가입/로그인 입력 검증 없음 | username(3~20자, 영문/숫자/`_`), password(8자 이상, 영문+숫자 포함) 정규식 검증 |
| 상품 가격 등 입력 검증 없음(TEXT 컬럼, 자유 입력) | price를 정수 컬럼으로 변경, 1원~1억원 범위 검증. 제목/설명 길이 제한 |
| 상품 수정/삭제 기능 자체가 없었음(소유자 검증 불가) | 신규 구현 + `seller_id == 현재 사용자` 검증(불일치 시 403) |
| `view_product`, `report` 등 접근 제어 미흡(로그인 없이도 접근 가능한 라우트 다수) | 전체 라우트에 `login_required` 적용, 관리자 전용 라우트는 `admin_required` |
| 신고 기능 남용 방지 없음(무제한 중복 신고 가능) | `(reporter_id, target_type, target_id)` 유니크 제약으로 동일 대상 재신고 차단 |
| 신고 누적에 따른 조치가 없었음(요구사항 미구현) | 5건 누적 시 상품 자동 차단 / 유저 자동 휴면, `logging`으로 감사 로그 기록 |
| 실시간 채팅: 클라이언트가 보낸 username을 그대로 신뢰(스푸핑 가능), 인증 확인 없음, 메시지 검증/제한 없음 | 서버가 세션에서 신원 확인(연결 시 미인증이면 연결 거부), 메시지 300자 제한, 유저당 10초 5개 Rate limit |
| SQL은 raw sqlite3 + `?` 파라미터 바인딩 사용(인젝션 자체는 방지됐으나 ORM 요구사항 미충족) | SQLAlchemy ORM으로 전면 전환 |
| 오류 시 내부 정보 노출 가능성(Flask 기본 에러 페이지) | 커스텀 403/404/500 핸들러, 500은 스택트레이스 대신 서버 로그에만 기록 |
| 보안 헤더 없음 | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy` 적용(인라인 스크립트를 static 파일로 옮겨 `script-src 'self'` 유지 가능) |
| 재인증 로직 없음(비밀번호 변경, 송금 등 민감 작업) | 비밀번호 변경과 송금 시 현재 비밀번호 재입력 요구 |

### 5.1 그대로 남겨둔/의도적으로 타협한 부분 (한계와 이유)

- **DB 권한 최소화**: SQLite는 파일 기반 DB로 DB 사용자/권한 개념이 없다. 대신 `market.db` 파일 권한을
  `0600`으로 제한했다. 운영 환경이라면 PostgreSQL 등으로 옮기고 least-privilege 계정을 쓰는 것이 정답이다.
- **HTTPS 강제**: 로컬 개발 서버(Werkzeug)는 TLS를 직접 종단하지 않는다. `FORCE_HTTPS=1` 환경변수로
  HTTPS 리다이렉트를 켤 수 있게 해두었고, 실제 테스트는 README 안내대로 ngrok(https) 터널로 수행한다.
- **Rate limiting 저장소**: `flask-limiter`가 기본 in-memory 저장소를 사용한다(실행 시 경고 발생). 단일
  프로세스로 로컬 실행하는 이 과제 규모에서는 문제 없으나, 다중 워커/분산 환경이라면 Redis 등 외부
  저장소가 필요하다.
- **로그인 실패 메시지**: 계정 잠금/휴면 안내 메시지는 해당 username이 존재한다는 사실을 간접적으로
  드러낼 수 있다(일반적인 트레이드오프). 사용성을 위해 유지했다.

## 6. 체크리스트 및 테스트

`secure_coding_checklist.csv`의 5개 섹션(회원가입/프로필, 상품, 채팅, 신고, 전체 시스템) 28개 항목을
5장의 표와 대조하며 구현·점검했다. 실제로 로컬에서 수행한 테스트(curl 기반):

- [x] 회원가입: 정책 위반 username/password 거부 확인
- [x] 로그인: 5회 연속 실패 후 계정 잠금 메시지 확인, IP당 분당 10회 제한(429) 확인
- [x] CSRF: 토큰 없는 POST 요청 → 400 확인
- [x] 상품 등록: 잘못된 가격(음수) 입력 시 DB에 저장되지 않음을 확인
- [x] 상품 삭제: 소유자가 아닌 계정(관리자 포함) 요청 시 403 확인
- [x] 검색: `?q=` 쿼리로 제목/설명 부분일치 검색 동작 확인
- [x] 신고: 동일 대상 중복 신고 차단, 5건 누적 시 상품 자동 차단 + 감사 로그 기록 확인
- [x] 송금: 현재 비밀번호 재인증, 정상 송금 시 잔액·거래내역 반영 확인
- [x] 관리자: 비관리자 계정의 `/admin` 접근 시 403, 관리자 계정은 200 확인
- [x] 보안 헤더: `curl -I`로 CSP/X-Frame-Options/X-Content-Type-Options/Referrer-Policy 확인

## 7. 유지보수

- `market.db`는 `.gitignore`에 포함되어 있어 스키마 변경 시 파일을 지우고 재생성하는 방식(Alembic 등
  마이그레이션 도구는 과제 규모상 도입하지 않음)
- 향후 개선 아이디어: 상품 이미지 업로드, 알림(신고 처리 결과, 송금 알림), 페이지네이션(상품/거래내역),
  Redis 기반 rate limit 저장소로 교체, 프로덕션 배포 시 eventlet/gunicorn + Nginx(TLS 종단) 구성

## 8. 실행 방법

```bash
git clone <본인 repo>
conda env create -f enviroments.yaml
conda activate secure_coding

# 최초 관리자 계정을 만들고 싶다면 가입 전에 아래 환경변수를 지정
export ADMIN_USERNAME=admin

python app.py
# ngrok으로 외부 공개 시 https URL이 발급되어 Secure 쿠키 요구사항도 함께 충족됨
```

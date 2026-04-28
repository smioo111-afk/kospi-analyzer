# Stage 1 — 디렉토리 구조 + 메트릭

**날짜**: 2026-04-28
**브랜치**: `review/full-code-audit-20260428`

---

## 1. 전체 규모

| 지표 | 값 |
|------|-----|
| .py 파일 수 | **44** |
| 총 LoC | **14,656** |
| 평균 파일 크기 | 333 줄 |
| 1000줄 이상 파일 | 1 (database/models.py) |

---

## 2. 가장 큰 파일 TOP 10

| 순위 | 파일 | LoC | 비고 |
|------|------|-----|------|
| 1 | `database/models.py` | **1702** | 11+ 테이블, CRUD, 마이그레이션, cascade |
| 2 | `collectors/kis_api.py` | 964 | async/sync 양쪽, 토큰, 파서 |
| 3 | `bot/formatter.py` | 921 | 메시지 포맷 (단일 책임 큰 모듈) |
| 4 | `main.py` | 882 | 오케스트레이션 + 스케줄러 |
| 5 | `collectors/dart_api.py` | 798 | DART API + 캐시 |
| 6 | `analysis/scorer.py` | 794 | 5카테고리 스코어링 |
| 7 | `bot/telegram_bot.py` | 669 | 봇 명령어 + 발송 |
| 8 | `tests/test_integration.py` | 657 | 통합 테스트 |
| 9 | `monitoring/health_check.py` | 615 | 자가 진단 (오늘 추가) |
| 10 | `tests/test_dart_api.py` | 461 | DART 단위 테스트 |

**관찰**: `database/models.py`는 11+ 테이블·100여 메소드를 한 파일에 집중. 분리 후보지만 회귀 위험 큼 → 별도 작업.

---

## 3. 의존성

### 선언 (requirements.txt) vs 설치 (pip freeze)
| 패키지 | 선언 버전 | 설치 버전 | 사용처 |
|--------|----------|----------|--------|
| requests | 2.31+ | 2.33.1 | `collectors/{kis_api,dart_api}.py` ✓ |
| aiohttp | 3.13+ | 3.13.5 | `collectors/kis_api.py` ✓ |
| aiolimiter | 1.1+ | 1.2.0 | `collectors/kis_api.py` ✓ |
| pandas | 2.1+ | 2.3.3 | 43회 사용 ✓ |
| numpy | 1.26+ | 2.3.4 | 12회 사용 ✓ |
| pyarrow | 14+ | 23.0.1 | DART 캐시 parquet ✓ |
| **ta** | 0.11+ | **0.11.0** | **import 0건 (미사용)** |
| python-telegram-bot | 20.7+ | 22.7 | `bot/` ✓ |
| APScheduler | 3.10+ | 3.11.2 | `main.py` ✓ |
| python-dotenv | 1.0+ | 1.2.2 | `config/settings.py` ✓ |

### 발견 ⚠️

**M1 [MED] `ta` 라이브러리 미사용**: requirements.txt 선언, pip 설치, 그러나 `import ta` / `from ta` 0건. ATR/이동평균 등은 직접 구현(`analysis/stoploss.py`). → 의존성 청소 후보.

---

## 4. 코드 마커

| 마커 | 건수 | 위치 |
|------|------|------|
| TODO | 3 | signals/dart_api/telegram_bot |
| FIXME | 0 | - |
| XXX | 0 | - |
| HACK | 0 | - |
| DEPRECATED | 1 | settings.py:32 |

### 상세
1. **`analysis/signals.py:225`** [LOW] `_is_financial_sector` TODO — 업종 코드 매핑 미구현. 현재는 항상 `False` 반환. 금융주 별도 분류 효력 없음 → Stage 4에서 영향 평가.
2. **`collectors/dart_api.py:35`** [INFO] 신규 종목 상장 시 corp_codes 갱신 필요 — 운영 메모.
3. **`bot/telegram_bot.py:321`** [LOW] 사용자별 설정 저장 — 미구현.
4. **`config/settings.py:32`** [INFO] DEPRECATED sync 동기 호출 간격 상수 — async 전환 후 잔재. 미사용이면 제거 가능.

---

## 5. 외부 의존 사용 분포

- **HTTP**: `requests` (sync, DART 5건 + KIS 토큰 1건) + `aiohttp` (async, KIS 본체) — 비대칭이지만 의도적 (DART는 호출량 적고 sync로 충분).
- **데이터**: pandas (43) + numpy (12) + pyarrow (캐시) — 정상.
- **HTTP 클라이언트 중첩**: `python-telegram-bot`이 `httpx` 끌고 옴 — 직접 코드는 아님.

---

## 6. 후속 단계 우선순위

코드 라인 수와 어제~오늘 변경 영역을 가중치로:

| Stage | 영역 | LoC | 위험 |
|-------|------|-----|------|
| **Stage 2** | main.py + orchestration | 882 | **HIGH** (오늘 분기 변경, 어제 KOSPI async 패치) |
| **Stage 3** | collectors (kis + dart) | 1762 | HIGH (어제 FCF/sector 보강, account_id 매칭) |
| **Stage 4** | analysis (scorer + signals + stoploss + admin_filter) | 1563 | MED (오늘 admin_filter 추가, signals threshold) |
| **Stage 5** | database/models.py | 1702 | MED (어제 컬럼 추가) |
| **Stage 6** | bot/formatter + telegram_bot | 1590 | MED (오늘 send_health_alert 추가) |
| **Stage 7** | monitoring + tools | 1500+ | MED (오늘 health_check, backup_db 추가) |
| **Stage 8** | tests | 3300+ | MED (커버리지 갭 점검) |
| **Stage 9** | settings + config | 435 | LOW (단순 상수) |

---

## 7. Stage 1 발견 사항 요약

| ID | 심각도 | 내용 |
|----|--------|------|
| M1 | MED | `ta` 라이브러리 미사용, requirements 청소 후보 |
| L1 | LOW | `_is_financial_sector` TODO, 항상 False (영향 Stage 4 검증) |
| L2 | LOW | telegram_bot.py 사용자별 설정 TODO |
| I1 | INFO | settings.py DEPRECATED sync 호출 간격 — 미사용이면 제거 |
| I2 | INFO | DART corp_codes 운영 메모 |

CRIT/HIGH 발견 0건.

---

**다음**: Stage 2 (main.py + 오케스트레이션) 진행 승인 요청.

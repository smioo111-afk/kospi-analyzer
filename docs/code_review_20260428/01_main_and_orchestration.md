# Stage 2 — main.py + 오케스트레이션

**대상**: `main.py` (882 LoC) + 스케줄러 + L1 영향 추적
**날짜**: 2026-04-28

---

## 1. 함수 목록 (15개) + 크기

| 위치 | 함수 | 라인 수 | 책임 | 평가 |
|------|------|---------|------|------|
| 44 | `setup_logging` | 39 | 로깅 핸들러 | ✓ |
| 83 | `is_trading_day` | 47 | 거래일 판정 | ✓ |
| 130 | `AnalysisPipeline.__init__` | 12 | 인스턴스 생성 | ✓ |
| 142 | `AnalysisPipeline._get_top_stock_codes` | 20 | TOP N 코드 조회 | **dead** (보존 정책) |
| 162 | `AnalysisPipeline._determine_target_codes` | 13 | 분기 → 항상 None | ✓ (오늘 단순화) |
| **175** | **`AnalysisPipeline.run`** | **247** | **6단계 통합 실행** | ⚠️ **너무 큼** |
| 422 | `AnalysisPipeline._collect_price_data` | 104 | 시세 수집 + admin filter | ⚠️ 큼 |
| 526 | `AnalysisPipeline._collect_data` | 108 | 데이터 통합 수집 | ⚠️ 큼 |
| 634 | `AnalysisPipeline.cleanup` | 8 | DB 닫기 | ✓ |
| 642 | `scheduled_analysis` | 21 | 스케줄러 진입 | ✓ |
| 663 | `scheduled_performance_report` | 36 | 월간/분기/반기/연간 분기 | ✓ |
| 699 | `start_scheduler` | 108 | 4 잡 등록 | ✓ |
| 807 | `main` | 43 | 엔트리포인트 | ✓ |
| 850 | `run_bot` | 18 | 봇+스케줄러 | ✓ |

**관찰**: 가장 긴 함수 `run()` 247줄. 단일 책임 위반이지만 회귀 위험 큰 리팩토링 → MED 등급.

---

## 2. L1 영향 추적: `_is_financial_sector` 데드 플래그

### 발견
- **정의**: `analysis/signals.py:222` — 항상 `False` 반환
- **할당**: `analysis/signals.py:201` `stock["is_financial"] = ...`
- **소비처**: **0건** (전수 grep 결과)

### 실제 금융주 분기 경로 (별도 동작 중)
1. `config/settings.py:393` `FINANCIAL_SECTOR_CODES = ["0500", "0600", "0700"]` (KIS 업종)
2. `collectors/dart_api.py:328+` `_calc_financial_revenue` — sector 인자로 분기
3. `main.py:598+` `price_sector_map` → DART에 KIS sector 전달

### 결론
**dead flag**. `is_financial` 키는 set만 되고 read 0건. 어제 묶음 F (금융주 매출 합산)는 KIS sector 문자열 직접 매칭으로 처리 — 이 함수와 무관.

→ **영향 없음**, 단지 코드 인지 부담. **L1 등급 유지** (Stage 4에서 별도 PR 후보로 등록 권고: 함수 제거 + assignment 제거).

---

## 3. 데드 코드 검증

| 항목 | 상태 | 비고 |
|------|------|------|
| `_get_top_stock_codes` | dead (직접 호출 0건, test mock만) | 의도적 보존 (오늘 docstring 명시) |
| `_collect_price_data`의 `target_codes is not None` 분기 (line 491-499) | dead (current 정책상 항상 None) | 보존 — 향후 별도 명령에서 재활용 가능 |
| `_collect_price_data`의 `target_codes is None` 분기 (line 449-512) | active | 매일 풀 스캔 경로 |
| `is_financial` 플래그 | **truly dead** (참조 0건) | 제거 후보 (별도 PR) |

dead path는 모두 의도적이거나 영향 없음. **회귀 위험 0**.

---

## 4. 비동기 정합성 검사

`main.py` 내 모든 `self.kis.aget_*` / `self.kis.acheck_token` 호출 점검:

| 라인 | 호출 | async with 컨텍스트 | 평가 |
|------|------|-------------------|------|
| 189 | `await self.kis.acheck_token()` | ❌ 없음 | ⚠️ **Stage 3 검증 필요** |
| 282 | `await self.kis.aget_kospi_index()` | ✓ line 281 | OK (어제 패치) |
| 322 | `await self.kis.aget_stock_price(c)` (gather) | ✓ line 320 | OK (어제 패치) |
| 327 | `await self.kis.aget_daily_chart(c, ...)` (gather) | ✓ line 320 | OK |
| 394 | `update_performance_tracking` (sync 내부) | `asyncio.to_thread` 격리 | OK |
| 422 (`_collect_price_data`) | 호출 시 docstring 요구: "async with 컨텍스트 안" | 호출자 `_collect_data` line 549 안 | OK |
| 549 (`_collect_data` 시작) | `async with self.kis:` | ✓ | OK |

### 발견
**A1 [HIGH 후보, Stage 3에서 정밀 검증]**: `acheck_token` (line 189)이 `async with self.kis:` 컨텍스트 밖에서 호출됨. `acheck_token` 내부가 세션을 사용한다면 동일 회귀 (어제 KOSPI 패턴). Stage 3에서 collectors/kis_api.py acheck_token 본문 확인 필요.

---

## 5. 스케줄러 검토

### 등록 잡 (start_scheduler, line 699-801)

| 잡 ID | 시간 | day_of_week | misfire_grace | 비고 |
|-------|------|------------|---------------|------|
| `daily_analysis` | 15:40 (KST) | mon-fri | 3600s | 약 2분 44초 |
| `daily_health_check` | 16:00 | mon-fri | 3600s | DB SELECT only |
| `daily_auto_backup` | 16:30 | mon-fri | 3600s | sqlite3 backup API |
| `monthly_performance` | 매월 1일 09:00 | (any) | 7200s | 월/분기/반기/연간 |
| `sample_threshold_notifier` | 16:30 | mon-fri | 3600s | **이미 등록 스킵** (플래그 존재) |

### 충돌 분석

| 시점 | 동시 실행 잡 | 평가 |
|------|------------|------|
| 15:40 시작 → 15:42:45 종료 | daily_analysis 단독 | ✓ |
| 16:00 | health_check 단독 (분석 17분 후) | ✓ |
| 16:30 | auto_backup + (sample_threshold_notifier 등록 스킵) | ✓ |

`AsyncIOScheduler` + 기본 `AsyncIOExecutor` → 단일 이벤트 루프 cooperative 스케줄링 → 진짜 병렬 실행 없음.

WAL 모드 + sqlite3 backup API → reader 동시 가능, writer 충돌 없음 (분석 사이클은 16:30 전 종료).

**결론**: 잡 충돌 위험 **없음**.

### 발견 (관찰)
**O1 [INFO]** `sample_threshold_notifier`가 이미 발송된 상태로 매번 등록 스킵 로그 발생 → 봇 재시작마다 한 줄 잔존. 깨끗한 정리: 일회성 잡 코드 자체를 별도 명령으로 이동하거나 docs/sample_notifier_cleanup.md 절차로 코드 제거. **별도 PR 후보**.

---

## 6. 예외 처리 검토

| 위치 | 패턴 | 평가 |
|------|------|------|
| `run()` line 186-420 try | 6단계 전체 감싸기 | ⚠️ 광범위, 단계별 식별 어려움. send_error_alert에 모듈명만 전달. traceback에 정보 충분 → 운영상 OK |
| line 235 sector 평균 try | 부분 실패 graceful | ✓ |
| line 280 KOSPI try | 부분 실패 graceful (kospi_index=0.0 fallback) | ⚠️ T1-2에서 검증 필수 (오늘 추가) |
| line 393 perf tracking try | 실패해도 분석 결과 보존 | ✓ |
| line 414 send_error_alert except: pass | 알림 실패는 무시 | ✓ |

### 발견
**O2 [INFO]**: KOSPI 지수 수집 실패 시 `kospi_index = 0.0` fallback. T1-2 health check가 0이면 FAIL — 즉 다음날 16:00 알림으로 노출됨. 안전망 OK.

---

## 7. 리소스 정리

| 자원 | 정리 |
|------|------|
| `self.kis` aiohttp 세션 | `async with self.kis:` 블록 종료 시 닫힘 (`__aexit__`) ✓ |
| `self.db` sqlite 연결 | `cleanup()` → `db.close()` (run/scheduled_analysis finally) ✓ |
| WAL | `checkpoint_wal("PASSIVE")` 매 사이클 종료 (line 404) ✓ |

**누수 없음**.

---

## 8. 가독성 / 코멘트

### 발견
**X1 [LOW] line 379**: `# 9. 리포트 로그 저장 (TOP 10 스���샷)` — UTF-8 깨진 코멘트. "스냅샷" 추정. 동작 영향 없음.

### 단계 번호 불일치 [LOW]
- 코멘트 단계 표기: `# 1.`, `# 2.`, `# 3.`, `# 4.`, `# 5.`, `# 6.`, `# 7. KOSPI`, `# 8. DB 저장`, `# 8. 포트폴리오`, `# 9. 리포트 로그`, `# 10. 성과 추적`
- 그러나 logger는 `[1/6]` ... `[6/6]` 표기
- 코멘트 번호와 logger 단계 일치 안 함. 가독성 저하지만 동작 영향 없음.

---

## 9. 누적 발견 (Stage 2)

| ID | 심각도 | 내용 | 후속 |
|----|--------|------|------|
| A1 | HIGH (잠정) | `acheck_token` async with 컨텍스트 외 호출 | **Stage 3에서 정밀 검증** |
| L1 → confirmed | LOW | `_is_financial_sector` 진짜 dead flag (참조 0건). 제거 별도 PR. | Stage 4 등록 |
| O1 | INFO | sample_threshold_notifier 등록 스킵 로그 잔존 | 별도 PR (정리) |
| O2 | INFO | KOSPI fallback 0.0은 T1-2가 잡음 | 안전망 충분 |
| X1 | LOW | line 379 코멘트 한글 깨짐 (스���샷) | 별도 PR |
| MED1 | MED | `run()` 247줄 단일 함수, 분리 회귀 위험 큼 | 장기 리팩토링 후보 |

**CRIT 0건**. Stage 2 통과.

---

## 10. 다음 단계

**Stage 3 (collectors)** 진행:
- A1 정밀 검증 (acheck_token 본문 확인)
- DART account_id 매칭 (어제 패치)
- KIS rate limiter / 토큰 만료
- 응답 파서 silent fail

**다음 진행 승인 요청.**

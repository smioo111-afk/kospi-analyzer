# Stage 8 — Tests

**대상**: `tests/` 16개 파일, 4186 LoC
**날짜**: 2026-04-28

---

## 1. 인벤토리

| 파일 | LoC | 대상 모듈 | 신규 |
|------|-----|----------|------|
| test_admin_filter.py | 156 | analysis/admin_filter | 오늘 |
| test_backup.py | 169 | tools/backup_db | 오늘 |
| test_bot_auth.py | 125 | bot/telegram_bot 권한 | 오늘 |
| test_daily_full_scan.py | 84 | main.py 분기 | 오늘 |
| test_dart_api.py | 461 | collectors/dart_api | - |
| test_fair_value_gap.py | 127 | analysis/scorer 적정주가 | 오늘 |
| test_formatter.py | 308 | bot/formatter | 일부 신규 |
| test_health_check.py | 360 | monitoring/health_check | 오늘 |
| test_integration.py | 657 | 통합 (mock) | - |
| test_kis_async.py | 327 | collectors/kis_api 비동기 | - |
| test_kis_sync_compat.py | 157 | KIS 동기 호환 | - |
| test_kospi_index_parse.py | 89 | KOSPI 응답 파서 | 오늘 |
| test_recover_performance.py | 277 | tools/recover_performance | - |
| test_sample_notifier.py | 204 | tools/sample_threshold | - |
| test_scorer.py | 235 | analysis/scorer | - |
| test_survivorship.py | 450 | 생존편향 / cascade | - |

**170 PASS / 0 FAIL** (전체 + 오늘 추가 신규 13건).

---

## 2. 커버리지 (전체 64%)

### 핵심 모듈
| 모듈 | LoC | Cov% | 평가 |
|------|-----|------|------|
| analysis/admin_filter | 35 | **100%** | ✓ (오늘) |
| analysis/signals | 109 | **94%** | ✓ |
| analysis/scorer | 460 | **87%** | ✓ |
| monitoring/health_check | 237 | **83%** | ✓ (오늘) |
| analysis/stoploss | 101 | 76% | OK (외각 경고 미커버) |
| collectors/dart_api | 373 | 60% | △ (실 API 경로 미커버) |
| database/models | 446 | 52% | △ (CRUD 일부 갭) |
| bot/formatter | 505 | **49%** | ⚠️ |
| collectors/kis_api | 434 | **45%** | ⚠️ |
| **bot/telegram_bot** | 331 | **20%** | ⚠️ **HIGH** |
| analysis/performance_analyzer | 107 | **16%** | ⚠️ **HIGH** |
| **main.py** | 397 | **12%** | ⚠️ **HIGH** |
| tools/backfill_* | 358 (4 files) | **0%** | ⚠️ |
| tools/benchmark_collect | 95 | 0% | OK (운영 도구) |
| tools/dry_run_async | 68 | 0% | OK (운영 도구) |

### 발견
**T8-1 [MED] main.py 12% 커버리지**: `AnalysisPipeline.run()` 247줄 함수의 통합 테스트 부재. 어제 KOSPI async with 회귀처럼 사이클 흐름의 silent fail이 mock 단위 테스트로 잡히지 않음. test_pipeline_simulation은 mock 데이터로 분석 단계만 검증, run() 직접 호출 없음.

**T8-2 [MED] bot/telegram_bot.py 20%**: 11개 명령어 핸들러 본문 미커버. `send_health_alert`, `send_signal_changes` 등 신규 메소드 단위 테스트만 있고 종단 통합 부재.

**T8-3 [MED] performance_analyzer 16%**: 월간/분기/반기/연간 성과 리포트 로직 90/107 미커버. 실 데이터 의존성 큼.

**T8-4 [LOW] backfill_* 0%**: 4개 백필 도구 모두 0%. 일회성 도구라 회귀 위험은 적지만, dry-run 출력 형식 변경 시 silent 에러 가능. 권고: dry-run 모드만이라도 단위 테스트 추가.

---

## 3. 강건성 점검

### assert 부재 테스트
- **0건** ✓ (전수 grep `grep -L "assert" tests/test_*.py`)

### 외부 HTTP 호출
- **0건** ✓ (전수 grep `requests\.(get|post)|aiohttp\.|http://`)
- 모든 KIS API: `aioresponses` 모킹 (test_kis_async.py)
- 모든 DART: parquet 캐시 mock 또는 합성 DataFrame
- 모든 DB: 임시 sqlite (`tmp_path` fixture)

### 시간 의존 (5건)
| 위치 | 용도 | 안전 |
|------|------|------|
| test_integration:640 | print 출력만 | ✓ |
| test_kis_sync_compat:30 | 토큰 만료 24h | ✓ (셋업, 비교 X) |
| test_kis_async:35,184 | 토큰 만료 24h | ✓ |
| test_survivorship:107 | "최근 N주" 시뮬 | ✓ (상대 시간) |

flaky 테스트 0건.

### random 의존
- `test_integration::test_pipeline_simulation`: `random.seed(42)` ✓ 결정적

---

## 4. 통합 테스트 갭 (어제 KOSPI 패턴 외)

### 어제 KOSPI 패턴 회귀
- 패치 이후 현재 main.py:281, 320, 544 모든 위치에 `async with self.kis:` 명시
- test_kis_async.py가 단위 모킹으로 검증
- 그러나 **main.py에서 self.kis 인스턴스 재사용 + 여러 사이클 호출** 패턴 통합 테스트 0건
- 어제 KOSPI mock test_kospi_index_fetch_success(line 264)가 통과해도 실 main.py 호출에서 RuntimeError 발생했던 이유 = mock은 self.kis 인스턴스 재사용 시나리오 미반영

### 잠재 동일 패턴 갭
- `update_performance_tracking` (sync KIS 호출, to_thread 격리) — 단위 테스트 (test_recover_performance) 있으나 main.py 호출 흐름 미검증.
- `acheck_token` (Stage 3 검증으로 안전 확정) — 통합 테스트 부재. 토큰 만료 후 재발급 시나리오 미커버.

### 발견
**T8-5 [MED] AnalysisPipeline 종단 테스트 부재**: KIS 모킹 + DART 캐시 모킹으로 `pipeline.run()` 1회 종단 통합 테스트 권고. 회귀 가드 가치 큼.

---

## 5. 핵심 시나리오 회귀

### silent fail 가드 함수 9개 테스트 커버
| 함수 | 가드 테스트 |
|------|------------|
| `_score_growth(0)` | test_scorer.py 어제 추가 ✓ |
| `_score_debt(<=0)` | test_scorer.py 일부 |
| `_score_fcf_yield/margin` | test_scorer.py |
| `_score_psr/peg/ev_ebitda` | test_scorer.py |
| `_threshold_above/below` | 간접 |
| `_calc_fair_value` (오늘 수정) | test_fair_value_gap.py 5건 ✓ |

### 어제 패치 회귀 테스트
- `_score_growth(0)`: test_scorer.py 검증 ✓
- DART account_id 매칭: test_dart_api.py ✓
- KOSPI async with: test_kis_async에 직접 회귀 가드 (test_kospi_index_async_with_required, line 307+)
- KOSPI change_rate 키: 오늘 신규 test_kospi_index_parse.py ✓
- 적정주가 gap: 오늘 신규 test_fair_value_gap.py ✓
- 봇 권한: 오늘 신규 test_bot_auth.py ✓

---

## 6. 픽스처 정합성

### Phase 0 수정 (test_signals MIN_MARKET_CAP)
- test_integration.py:288-296: 5000억/50억 임계 통과 데이터로 갱신 ✓
- 향후 settings 임계 변경 시 같은 갱신 필요 — 픽스처 헬퍼 함수로 분리 권고 (별도 PR).

### KIS 응답 픽스처
- test_kis_async.py `_kospi_index_response` (Phase 0 mock helper 갱신, line 72-83) ✓
- 4-28 라이브 응답 패턴 캡처 (test_kospi_index_parse.py LIVE_KOSPI_OUTPUT) ✓ — N4 같은 회귀 차단

### DART 캐시 픽스처
- test_dart_api.py에 실 캐시 사례 (예: test_real_cache_023530_lotte_shopping)
- 합성 DataFrame과 실 캐시 모두 사용 ✓

---

## 7. 테스트 실행 시간

### 전체: 26초 (170 테스트)

### 느린 테스트 TOP 5 (의도적, 모두 sleep/timeout 검증)
| 테스트 | 시간 | 비고 |
|--------|------|------|
| test_batch_failure_above_threshold_raises | 6.02s | 재시도+백오프 |
| test_batch_partial_failure_below_threshold | 6.02s | 동일 |
| test_kospi_index_failure_returns_zero | 6.02s | 동일 |
| test_rate_limit_enforced | 3.01s | 실 sleep |
| test_retry_exhausted_raises | 2.02s | 재시도 |

→ flaky 위험 없음. 의도적 sleep 검증.

---

## 8. mock 사용 적정성

### 패턴
- aioresponses (5 파일): KIS HTTP 모킹 — aiohttp 내장 호환
- AsyncMock/MagicMock (3 파일): 함수 단위 격리
- pandas DataFrame 직접 생성: DART 캐시 시뮬 ✓
- tmp_path fixture: sqlite 임시 DB ✓

### 발견
**T8-6 [INFO]** test_kis_async.py 일부에서 `_install_fake_token(kis)` 패턴 — 토큰 모킹이 인스턴스 직접 조작. 패턴 일관성 OK지만, 토큰 만료 자동 갱신 시나리오 단위 커버 부족.

---

## 9. Stage 8 누적 발견

| ID | 심각도 | 내용 | 권고 |
|----|--------|------|------|
| T8-1 | MED | main.py 12% / pipeline.run() 통합 테스트 부재 | 별도 PR (KIS+DART mock 종단 테스트) |
| T8-2 | MED | bot/telegram_bot.py 20% / 명령어 핸들러 통합 테스트 부재 | 별도 PR (선택) |
| T8-3 | MED | analysis/performance_analyzer 16% | 별도 PR (선택) |
| T8-4 | LOW | tools/backfill_* 0% | 별도 PR (dry-run 모드만이라도) |
| T8-5 | MED | AnalysisPipeline 종단 테스트 — 어제 KOSPI 같은 회귀 차단 | 별도 PR |
| T8-6 | INFO | 토큰 만료 자동 갱신 시나리오 단위 커버 부족 | 선택 |

**CRIT 0 / HIGH 0**.

테스트 강건성은 우수:
- assert 부재 0건
- 외부 HTTP 호출 0건
- flaky 0건
- 결정적 (random.seed 사용)

핵심 비즈니스 로직(scorer 87% / signals 94% / admin_filter 100% / health_check 83%)은 잘 커버됨. 약점은 **오케스트레이션(main.py) 종단 테스트** + **봇 핸들러 통합 테스트**.

---

**다음**: Stage 9 (settings + config + 보안) 진행 승인 요청.

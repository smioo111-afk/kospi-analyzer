# Stage 7 — Monitoring + Tools

**대상**: `monitoring/health_check.py` (615) + `tools/` 9개 (1664 LoC 합)
**날짜**: 2026-04-28

---

## 1. monitoring/health_check.py (오늘 추가)

### 12 검증 항목 + 임계값
| ID | 항목 | 임계 |
|----|------|-----|
| T1-1 | analysis_results 행 존재 | cnt ≥ 1 |
| T1-2 | kospi_index 범위 | 3000~10000 |
| T1-3 | foreign_net_buy | \|x\| < 10조 |
| T1-4a | growth=0 비율 | < 30% |
| T1-4b | quality=0 비율 | < 20% |
| T1-5 | FCF=0 비율 | < 10% |
| T1-6 | revenue=0 비율 | < 5% |
| T1-7 | perf_tracking last_updated ≥ 분석일 | - |
| T1-8 | cascade 재발 (return_1w=-100% / 7일) | < 5건 |
| T2-1 | total_score == 5카테고리 합 | 불일치 < 5% |
| T2-2 | 신호 vs 점수 임계값 | 위반 0건 |
| T2-3 | cascade circuit-breaker 발동 | 0회 |

### 평가 ✓
- 격리 검증 (4-28 PASS / 4-27 FAIL) — silent fail 즉시 노출 입증
- 임계값 합리적 (한국 시장 특성 반영)
- DB 직접 쿼리 (Database 클래스 의존성 없음 — 백업/장애 시에도 독립 실행 가능)
- logs/health_check.log 매일 추가 + warning/fail 시만 텔레그램

### 발견
**M-HC1 [LOW]**: KOSPI **change_rate** 검증 항목 부재. T1-2는 index 값만 검사 → N4 silent fail (4-28 패치 전 0.0)이 발생해도 못 잡음. N4 같은 회귀 차단 위해 검증 추가 권고:
```python
# T1-2b: |change_rate| < 30% (정상 일일 변동) AND change_rate != 0 + change != 0
```

**M-HC2 [INFO]**: T1-7 (perf_tracking 갱신) — `last_updated >= 분석일`. 휴장일 분석 스킵 시 last_updated가 직전 거래일에 머무름 → 다음 거래일 16:00 health에서 false positive 가능. 실측 4-28는 정상 (perf 4-28에 갱신됨), 향후 휴일 다음 거래일 모니터링 권고.

**M-HC3 [INFO]**: T2-3 (circuit-breaker) — `logs/kospi_analyzer.log`를 매번 전체 스캔 (line 380+ `_check_circuit_breaker`). 로그 파일 크기 커지면 IO 부담. RotatingFileHandler로 보호되나 단일 파일 100MB+ 가능. tail-only 검사 권고 (선택).

---

## 2. tools/ 9개 도구 분류

| 도구 | LoC | 진입점 | argparse | dry-run | 평가 |
|------|-----|--------|----------|---------|------|
| `backfill_dart_pl.py` | 267 | ✓ | ✓ | ✓ | ✓ |
| `backfill_dividend.py` | 107 | ✓ | ✓ | ✓ | ✓ |
| `backfill_fcf.py` (오늘) | 184 | ✓ | ✓ | ✓ | ✓ |
| `backfill_kospi_index.py` | 106 | ✓ | ✓ | ✓ | ✓ |
| `backup_db.py` (오늘) | 149 | ✓ | ❌ | ❌ | T-1, T-2 |
| `benchmark_collect.py` | 352 | ✓ | ❌ | n/a (읽기) | ✓ |
| `dry_run_async.py` | 123 | ✓ | ❌ | ✓ (이름대로) | ✓ |
| `recover_performance_tracking.py` | 226 | ✓ | ✓ | ✓ | ✓ |
| `sample_threshold_notifier.py` | 157 | ✓ | ✓ (`--force`) | n/a (알림) | ✓, 일회성 |

### 백필 4종 공통 패턴 (안전 ✓)
```python
parser.add_argument("--apply", action="store_true", help="실제 DB 갱신")
# 기본은 dry-run, --apply 명시해야 INSERT/UPDATE 실행
```
- 모든 mutating 도구가 fail-safe 디폴트 ✓
- conn.commit()은 `--apply` 분기 안에서만

### 발견
**T-1 [INFO]** `backup_db.py`에 `--dry-run` 플래그 부재. 30일 retain 정리 시 어떤 파일이 삭제될지 사전 확인 불가. 단, 정규식 매칭으로 자동 백업만 정리 (수동 보존) — 실제 위험 낮음. 권고: `--dry-run` 추가 (선택).

**T-2 [INFO]** `backup_db.py`에 argparse 부재. `retain_days`/`db_path` 등 함수 인자만 가능 (코드 import 시). 스케줄러 호출은 `scheduled_auto_backup()` (인자 없음, defaults). 수동 1회 retain 변경 시 코드 수정 필요. 권고: argparse 추가 (선택).

---

## 3. backup_db.py 안전성 (오늘 추가)

### `_purge_old_auto_backups` (line 100-130)
```python
m = AUTO_BACKUP_PATTERN.match(entry.name)
if not m:
    continue  # 자동 백업 패턴 외 파일은 보존
```
- 정규식 `^\d{8}_kospi_analyzer\.db$`로만 매칭 ✓
- `.bak_before_*`, 임의 파일은 절대 건드리지 않음 ✓
- `entry.unlink()` 단 한 곳 (line 126), 매칭 통과한 파일만

### 9 회귀 테스트 통과 (test_backup.py)
- `test_existing_manual_backups_preserved` — 수동 백업 보존 검증
- `test_backup_old_files_cleaned` — 30일+ 정리

평가 ✓.

---

## 4. backfill 도구별 영향 범위

| 도구 | 갱신 컬럼 | 위험 | 비고 |
|------|----------|------|------|
| `backfill_dart_pl.py` | revenue, op_income, net_income | DART API 재호출 | 백업 권고 |
| `backfill_dividend.py` | dividend_yield | DART API 호출 | - |
| `backfill_fcf.py` | free_cash_flow | 캐시만 사용 (API 호출 X) | ✓ 안전 |
| `backfill_kospi_index.py` | analysis_results.kospi_index | KIS API 호출 | - |

### 발견
**T-3 [INFO]**: backfill 도구들이 백업 강제(force) 옵션 없음. 사용자가 `--apply` 전에 수동 백업 권고 docstring에 명시 부족. 권고: docstring/help에 "백업 후 실행" 명시 (별도 PR).

---

## 5. recover_performance_tracking.py

### 안전 패턴
- `--dry-run` 기본
- `--apply` 강제
- KIS async with 컨텍스트 사용 (Stage 3 검증)

평가 ✓.

---

## 6. sample_threshold_notifier.py (일회성)

### 흐름
- 매일 16:30 등록 시도 → 플래그 파일(`data/.sample_notified`) 존재 시 등록 스킵 (main.py:773)
- 발송 후 플래그 생성 → 재발송 차단

### 발견
**T-4 [INFO]**: 일회성 잡인데 영구 보존됨. `docs/sample_notifier_cleanup.md` 절차에 따라 코드 자체 제거 권고. 봇 재시작마다 등록 스킵 로그 잔존 (Stage 2 O1 동일). 별도 PR 후보.

---

## 7. dry_run_async.py / benchmark_collect.py

### 평가
- 읽기 전용 (INSERT/UPDATE 0건) ✓
- KIS async with 정상 (Stage 3 검증) ✓
- 운영 영향 없음

발견 없음.

---

## 8. 도구 실행 권한 / 경로

### 발견
**T-5 [INFO]**: 모든 도구가 `data/kospi_analyzer.db` 직접 접근. 봇이 동시 실행 중이면 WAL 모드라 reader 동시 가능. mutating 도구(`--apply`)는 단일 writer 직렬화에 의존 — 운영 봇 종료 권고 docstring 부족. 권고: backfill 도구 docstring에 "실행 전 봇 종료 권고" 명시.

---

## 9. Stage 7 누적 발견

| ID | 심각도 | 내용 | 권고 |
|----|--------|------|------|
| M-HC1 | LOW | health_check에 KOSPI change_rate 검증 부재 (N4 회귀 차단) | 별도 PR (T1-2b 추가) |
| M-HC2 | INFO | 휴장일 다음 T1-7 false positive 가능 | 모니터링 |
| M-HC3 | INFO | circuit-breaker 로그 전체 스캔 IO 부담 | 선택 |
| T-1 | INFO | backup_db.py `--dry-run` 부재 | 선택 |
| T-2 | INFO | backup_db.py argparse 부재 | 선택 |
| T-3 | INFO | backfill 도구 백업 권고 docstring 부족 | 별도 PR |
| T-4 | INFO | sample_threshold_notifier 정리 (일회성 잡 영구 잔존) | docs 절차 |
| T-5 | INFO | mutating 도구 docstring에 "봇 종료 권고" 부족 | 별도 PR |

**CRIT 0 / HIGH 0 / MED 0**.

---

## 10. CRIT 즉시 항목

**0건**. monitoring + tools 영역은 신규 모듈 위주(오늘 추가)라 회귀 위험 낮고 격리 테스트 통과.

---

**다음**: Stage 8 (tests) 진행 승인 요청.

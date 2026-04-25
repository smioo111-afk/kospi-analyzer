# performance_tracking 100% 미수집 조사 보고서 (2026-04-26)

조사 브랜치: main (DB SELECT + 격리 KIS 호출만)
조사 결과: **코드 버그 아님 — 일시적 상태**. 4-27 월요일 첫 분석 사이클에서 자동 회복 예상.

## 요약

- **결손 규모**: performance_tracking 82건 전수에서 `return_*=0`, `last_updated` 80건 빈 문자열. 18일 경과한 행도 미갱신.
- **원인**: 코드 버그 아닌 시계열 상태 누적의 결과:
  1. 4-22 ~ 4-24: async 리팩토링 회귀(`_run_sync` RuntimeError, 이미 패치됨)로 KIS 가격 조회 3회 연속 실패 → 4-24에 80건 cascade 오탐.
  2. 4-24 21:03: `tools/recover_performance_tracking.py`로 80건을 "미계산" 상태(`return_*=0`, `last_updated=''`)로 복구.
  3. 4-25(토)·4-26(일): `is_trading_day()` 휴장 판정으로 분석 파이프라인 미실행 → 복구 후 update_performance_tracking 호출 자체가 아직 한 번도 일어나지 않음.
- **영향**: 백테스트·적중률 분석 자료 일시 부재. 코드 변경 없이 4-27 월요일 사이클로 자동 회복 예상.
- **권고 다음 작업**: 4-27 사이클 결과 모니터링 후, 갱신 안 되면 추가 조사. 갱신되면 본 이슈 종결.

## 1. 현재 상태 (Phase 2)

### 전수 통계
| 항목 | 값 |
|---|---:|
| total | 82 |
| `last_updated`=='' (빈 문자열) | 80 |
| `last_updated`='2026-04-24' | 2 |
| `return_1w`=0 | 82 |
| `return_1m`=0 | 82 |
| `is_delisted`=1 | 0 |
| `consecutive_fetch_failures`>0 | 2 |
| earliest `report_date` | 2026-04-07 |
| latest `report_date` | 2026-04-17 |

가장 오래된 행 18일 경과(2026-04-07→2026-04-26), `return_1w`/`return_1m` 모두 0 유지. fails 카운터는 0 = 복구 도구가 리셋한 후 update가 한 번도 안 돈 상태.

### 미갱신 행의 출처
원인은 `tools/recover_performance_tracking.py:122 restore_stock`의 UPDATE문으로 추적됨:

```sql
UPDATE performance_tracking
   SET is_delisted = 0,
       consecutive_fetch_failures = 0,
       price_after_1w = 0, return_1w = 0,
       ... (1m/3m/6m/1y 동일) ...
       signal_correct = 0,
       last_updated = ''
   WHERE stock_code = ? AND is_delisted = 1
```

현재 80건의 `fails=0, last_updated=''` 패턴은 이 복구 도구 출력과 정확히 일치 (cascade 오탐 80건 reset).

## 2. 코드 경로 (Phase 3)

### 호출 흐름
```
main.py:629 scheduled_analysis (매일 15:40 KST, 평일만)
  └─ is_trading_day() — 주말/공휴일 체크
      └─ True 시: kospi_analyzer 인스턴스의 run_daily_analysis()
          └─ main.py:391-402 update_performance_tracking 호출
              └─ asyncio.to_thread(self.db.update_performance_tracking, self.kis)
                  └─ database/models.py:1170 update_performance_tracking
                      ├─ daily_report_log에서 distinct (report_date, stock_code) 조회
                      ├─ existing_status 로드 (is_delisted, fails)
                      ├─ for each row:
                      │   ├─ if price_at_report<=0: continue
                      │   ├─ if code in delisted_codes: continue
                      │   ├─ if elapsed_days<7: continue (line 1248)
                      │   ├─ kis.get_stock_price(code) 캐시
                      │   ├─ if current_price<=0:
                      │   │   ├─ fails+=1, INSERT(line 1320, fails<3)
                      │   │   └─ if fails>=3: cascade or skip (line 1278/1308)
                      │   └─ else: 수익률 계산 + INSERT(line 1361)
                      └─ logger.info("성과 추적 N건 업데이트")
```

### 호출 로그 (logs/kospi_analyzer.log grep)
```
2026-04-14 15:41:26  성과 추적 11건 업데이트
2026-04-15 15:41:23  성과 추적 21건 업데이트
2026-04-16 15:41:22  성과 추적 31건 업데이트
2026-04-17 15:41:22  성과 추적 41건 업데이트
2026-04-20 15:48:41  성과 추적 51건 업데이트
2026-04-21 15:41:28  성과 추적 61건 업데이트
2026-04-22 15:40:12  성과 추적 0건 업데이트
2026-04-23 15:40:12  성과 추적 0건 업데이트
2026-04-24 15:40:11  성과 추적 80건 업데이트  ← cascade 오탐 (mark_delisted)
[2026-04-25, 26: 휴장 — 호출 없음]
```

4-14~21 정상 작동. 4-22, 23 0건은 KIS 호출 실패 시점 — 행은 fails 카운터 1, 2로 stub INSERT만 발생, `updated` 변수는 증가 안 함(코드 line 1418 메인 INSERT 경로에서만 증가). 4-24 fails=3 도달, cascade로 80건이 `is_delisted=1` 마킹되며 80건 카운트.

## 3. 직접 호출 결과 (Phase 4)

### 격리된 KIS get_stock_price 호출 (DB write 없음)
```
✓ 030200 KT          : current_price=61,700
✓ 005930 삼성전자     : current_price=219,500
✓ 000990 DB하이텍    : current_price=152,000
✓ 000270 기아        : current_price=153,400
✓ 012330 현대모비스   : current_price=422,500
성공 5/5, 실패 0/5
```

KIS API 자체는 100% 정상. async 리팩토링 회귀로 4-22~24 발생했던 RuntimeError는 4-24 21:03 fix 커밋(`2447f64`) 이후 재현되지 않음.

### update_performance_tracking 흐름 시뮬레이션 (DB write 없음)
DB의 daily_report_log 141건 distinct (report_date, stock_code)을 코드 흐름대로 분기:

| 분기 | 건수 |
|---|---:|
| price_at_report=0 skip | 0 |
| is_delisted skip | 0 (복구로 전부 0) |
| elapsed_days<7 skip | 50 |
| **updates 있음 (INSERT 시도 대상)** | **91** |
| updates 없음 (skip) | 0 |
| 고유 KIS 호출 종목 (캐시) | 15 |

실제 호출되면 91행에 대해 INSERT 호출 → `updated += 1` 91번 → 로그 "성과 추적 91건 업데이트" 예상.

## 4. 가설 검증 (Phase 5)

| 가설 | 결과 | 증거 |
|---|---|---|
| A. 호출 자체가 안 일어남 | △ 부분 | 4-25(토), 4-26(일) 휴장으로 호출 없음. 그러나 4-22, 23, 24는 호출됐음. **현 시점 미갱신은 휴장 때문**. |
| B. 호출은 되는데 return 계산 잘못됨 | ✗ 반증 | 시뮬레이션 91건 정상 분기. 코드 흐름에 무결. |
| C. KIS get_stock_price 실패 silent | △ 시간대 한정 | **4-22~24** 시점에는 async 리팩토링 회귀로 실패. 격리 호출 결과 현재는 5/5 정상. |
| D. days_passed 계산 오류 | ✗ 반증 | 시뮬레이션 elapsed_days 계산 정상, 7일+ 종목 91건 분류됨. |
| E. update가 일어나는데 다른 곳에서 0으로 덮어씀 | ✗ 반증 | `tools/recover_performance_tracking.py:122 restore_stock`은 일회성 도구로 4-24에만 호출됨. 그 외 INSERT 경로는 모두 update_performance_tracking 내부. |
| **F (신규). 휴장+복구 직후 미경과** | **✓ 확정** | 4-24 복구 후 4-25/26은 주말이라 update 미실행. 4-27 첫 실행 시 91건 정상 갱신 예상. |

## 5. 백필 영향 (Phase 6)

오늘(2026-04-26) DART CIS 백필을 적용했으나 performance_tracking에는 영향 없음:
- 백필 도구(`tools/backfill_dart_pl.py`)는 `financial_metrics` 테이블만 갱신.
- performance_tracking은 별도 흐름. 봇 재시작 후 휴장으로 update 미호출.

`MAX(last_updated)` from performance_tracking = `2026-04-24` (백필·재시작 이후 갱신 0건). 정상.

## 6. 결론

**코드 버그 아님 — 시간 진행에 따른 일시적 상태**:

1. 4-22~24 async 리팩토링 회귀로 cascade 오탐 80건 발생.
2. 4-24 21:03 `tools/recover_performance_tracking.py`로 80건을 미계산 상태로 복구. 복구 후 첫 update 사이클을 기다리는 중.
3. 4-25(토), 4-26(일) 한국 증시 휴장 → `is_trading_day()=False` → 분석 파이프라인 미실행 → update_performance_tracking 호출 자체가 일어나지 않음.

격리 호출과 시뮬레이션 모두 코드 흐름이 정상이고 KIS 호출도 100% 성공. **다음 평일 2026-04-27 15:40 분석 사이클에서 91건 정상 갱신될 것으로 예상**.

## 7. 권고 다음 작업

### 즉시 조치 (없음)
코드 변경 불필요. 자연 회복 대기.

### 모니터링 (4-27 월요일 15:40 이후)
- `logs/kospi_analyzer.log` grep "성과 추적" — 91건 내외 갱신 로그 확인
- DB 검증 쿼리:
  ```sql
  SELECT COUNT(*) FROM performance_tracking WHERE last_updated >= '2026-04-27';
  -- 기대값: 91 내외
  SELECT COUNT(*) FROM performance_tracking WHERE return_1w != 0;
  -- 기대값: 90+ (1주 이상 경과 행 다수)
  ```

### 만약 4-27에도 0건이면 (재발 시나리오)
재조사 항목:
1. KIS sync 래퍼가 async 컨텍스트 안에서 다시 RuntimeError 발생하는지 (asyncio.to_thread fix 회귀)
2. `is_trading_day()` 자체가 KIS 토큰 만료로 실패해 분석 전체가 스킵되는지
3. `existing_status` 로드 시점에 `is_delisted=1`이 아직 남아있는 행이 있는지

### 별도 P2 후속 작업 (개선 여지)
- **카운터 의미 명확화**: `updated` 변수가 cascade 카운트(line 1308 `updated += affected`)와 정상 INSERT(line 1418 `updated += 1`) 두 의미로 혼합됨. 별도 카운터 분리해 로그에 "정상 갱신 N건, cascade M건" 표기.
- **fails=0 + last_updated='' 표시**: 복구 도구 사용 후의 "미계산 상태"가 명시적이지 않음. 별도 컬럼 또는 약속된 `last_updated` 값(예: 'restored')으로 구분.
- **휴장일 보호**: 휴장 판정이 KIS 토큰 의존. `pykrx` 캘린더 1차 체크는 이미 P2 등록됨.

## 8. CRIT-3 등급 재평가

감사 보고서(2026-04-26)에서 CRIT-3로 분류했으나, 본 조사 결과 **코드 버그 없음**으로 확인됨. 재분류 권고:

- 기존 등급: **CRIT** (백테스트 자료 자체 부재)
- 권고 등급: **MED** (자연 회복 가능, 재발 시 추가 조사. 4-27 모니터링 결과에 따라 종결 또는 재조사)

# 시스템 데이터 무결성 감사 보고서 (2026-04-26)

조사 브랜치: `fix/dart-cis-fallback` (코드 변경 없이 SELECT 위주)
조사 범위: SQLite DB(`data/kospi_analyzer.db`), DART 캐시(`data/dart_cache/`),
            scorer/signals/main 코드 정적 분석.
머지 보류 중인 PR: DART CIS fallback (커밋 `c61c9a4`, `c4ffcb3`).

## 요약

발견 이슈 16건.

| 등급 | 개수 | 비고 |
|---|---:|---|
| **CRITICAL** | 3 | 점수 신뢰도 또는 백테스트 자료에 직접 영향. 즉시 처리 권장. |
| **HIGH** | 4 | 일부 종목/필드 영향. CIS 머지와 함께 또는 직후 처리 권장. |
| **MEDIUM** | 5 | 작동은 하나 비효율 또는 위험 잠재. |
| **LOW** | 4 | 개선 여지. 별도 작업으로 묶기. |

CIS 머지에 즉시 동반 처리 권장: **CRIT-2 한 건만**. 나머지는 별도 PR.

## 1. 핵심 테이블 결손율 (Phase 1-2)

### `financial_metrics` (2025 annual, n=233) — CIS 버그 영향

| 컬럼 | zero/null | % | 비고 |
|---|---:|---:|---|
| revenue | 180 | **77.3%** | CIS 버그. 백필로 168개 회복 가능 |
| operating_income | 179 | **76.8%** | 동상 |
| net_income | 178 | **76.4%** | 동상 |
| roe | 181 | **77.7%** | 파생 |
| operating_margin | 181 | **77.7%** | 파생 |
| revenue_growth_yoy | 180 | **77.3%** | PL 의존 |
| op_income_growth_yoy | 179 | **76.8%** | PL 의존 |
| ebitda | 137 | **58.8%** | 일부 회복 가능 |
| free_cash_flow | 130 | **55.8%** | 일부 회복 가능 |
| depreciation | 184 | **79.0%** | 일부 회복 가능 |
| prev_revenue/op/ni | ~179 | **77%** | 동상 |
| consecutive_loss_years | 221 | **94.8%** | PL 의존 (백필 시 일부 회복) |
| consecutive_op_decline_years | 206 | **88.4%** | 동상 |
| consecutive_revenue_decline_years | 212 | **91.0%** | 동상 |
| dividend_yield | 67 | 28.8% | 별개 — DART 배당 API 응답 누락 |
| total_assets | 20 | 8.6% | BS 정상 다수 |

### `stock_scores` (2026-04-24, n=49) — 분석 대상

| 컬럼 | zero/null | % |
|---|---:|---:|
| roe | 18 | **36.7%** |
| operating_margin | 19 | **38.8%** |
| dividend_yield | 10 | 20.4% |
| 그 외 (per/pbr/total_score 등) | <5% | 정상 |

분석 대상 49개 중에서도 결손율 36~38%. CIS 백필 적용 시 다음 사이클부터 자동 회복.

### `daily_report_log` (TOP 10 스냅샷, 2026-04-24, n=10)

v3 5카테고리 컬럼(growth_score, quality_score, fair_value_*) 모두 정상. 단 표본 작음(n=10).

### `performance_tracking` (전수 n=82) — **별도 CRITICAL**

| 컬럼 | zero | % |
|---|---:|---:|
| price_after_1w | 82 | **100.0%** |
| price_after_1m..1y | 82 | 100.0% |
| return_1w..1y | 82 | 100.0% |
| signal_correct | 82 | 100.0% |

`last_updated` 분포: 80건 빈 문자열, 2건 2026-04-24 (실패 카운터만 갱신).
가장 오래된 행(2026-04-07)도 19일 경과했으나 1주 후 가격 미수집. 백테스트·적중률 분석 자료로 사실상 사용 불가.

### `analysis_results` (n=26)

`kospi_index`, `foreign_net_buy` 모두 0/26 (100% 결손). 이미 TODO P1 등록됨.

### `sector_averages` (n=17)

보험/의료·정밀기기/증권 sector의 `avg_ev_ebitda=0` (EBITDA 결손 영향). 그 외 정상.

## 2. 응답 파싱 점검 — CIS 동형 패턴 (Phase 3)

| sj_div | 캐시 보유 | 비고 |
|---|---:|---|
| BS | 223/223 (100%) | 변종(SFP 등) 없음 |
| IS | 55/223 (24.7%) | **CIS 버그 — 이미 fix/dart-cis-fallback에서 수정** |
| CIS | 223/223 (100%) | |
| CF | 223/223 (100%) | |
| SCE | 223/223 (100%) | |

**결론**: CIS 동형 패턴은 IS에만 국한. 이미 패치 완료. BS/CF는 sj_div 단일 필터로 안전.

KIS 응답 측면:
- `stock_master.stock_name` 결손 0건. 최근 분석 49개 모두 master 있음.
- `stock_scores.stock_name` 결손 0건. `current_price`/`market_cap` 결손 0건.
- 일봉/투자자별 데이터: scorer가 `len(chart) < 5`/`len(closes) < 20` 등 길이 체크로 보호. 데이터 정상 흐름 추정 (DB에 raw 저장 안 됨).

## 3. 점수 계산 silent fail (Phase 4)

`analysis/scorer.py` 분석 결과:

### 보수적 처리 (안전)
| 함수 | 결손 시 동작 | 영향 |
|---|---|---|
| `_score_peg(per≤0 or growth≤0)` | DEFAULT_SCORE=0 | 안전 |
| `_score_ev_ebitda(ebitda≤0)` | DEFAULT_SCORE=0 | 안전 |
| `_score_psr(revenue≤0)` | DEFAULT_SCORE=0 | 안전 |
| `_score_fcf_yield/margin(fcf≤0)` | DEFAULT_SCORE=0 | 안전 |
| `_threshold_above(roe=0)` | DEFAULT_SCORE=0 | 안전 |

### 위험 (silent fail)
| 함수 | 결손 시 동작 | 영향 |
|---|---|---|
| **`_score_growth(rate=0.0)`** | thresholds 첫 매칭 `0.0 → 2점` | **결손이 가산점.** 매출+영업이익 성장에서 +4점 자동 부여. 결손 77% 종목이 이 가산점 받고 있음. 백필 시 진짜 음수면 0점으로 떨어져 점수 변동 클 수 있음. |
| **`_score_debt(ratio=0)`** | `ratio<=0 → DEBT_RATIO_MAX_SCORE=5` | **결손이 만점.** 부채비율 결손 11.6%(financial_metrics 기준)가 5점 만점 받음. |
| `_calc_growth_score` 페널티 | `consecutive_loss_years=0` (결손)이면 페널티 없음 | 진짜 적자 기업도 결손이면 페널티 면제. 단 PL 결손과 같이 발생하므로 직접적 영향은 PL 회복 후 자연 해결 |

### 이미 알려진 placeholder
- `signals.py:_is_financial_sector` 항상 `False` 반환 (TODO). 금융주 태그 미동작.

## 4. 데이터 흐름 무결성 (Phase 5) — 023530 추적

| 단계 | 상태 |
|---|---|
| DART 응답 (parquet) | **정상** (CIS=36행, 매출 13.7조, 영업이익 547억, 당기순이익 736억) |
| `financial_metrics` (2025 annual) | **결손** (revenue=0, op_income=0, net_income=0, BS는 정상) |
| `stock_scores` (2026-04-20) | **잘못된 값** (roe=0, op_margin=0, total_score=24, signal=sell) |
| `daily_report_log` (2026-04-24) | TOP 10 외 (sell 신호로 제외) |
| `analysis_results` | top_10_json에 미포함 |

CIS 백필 적용 후:
- `financial_metrics` PL 채워짐 (즉시)
- `stock_scores` ROE/op_margin은 다음 분석 사이클에서 재계산
- 점수가 24 → 더 높게 변동 가능 → signal 변경 (sell → hold/buy 가능성)
- TOP 10 구성·signal 분포 변동 예상

같은 종목 시간 일관성 별도 이상 없음 (analysis_date 정렬, last_updated의 빈 문자열 외).

## 5. 외부 의존성 (Phase 6)

| 항목 | 상태 |
|---|---|
| KIS 토큰 (`token_cache/kis_token.json`) | expired_at=2026-04-25T15:40, 현재 만료 상태. 다음 호출 시 자동 갱신될 것. **문제 아님**. |
| DART corp_codes 캐시 | 2026-04-06 다운로드, 30일 정책 — **다음 호출 시 재다운로드 트리거**. 정상. |
| KOSPI 지수 수집 | 26/26 모두 0. 이미 TODO P1 등록(2026-04-24). |
| `is_trading_day()` (main.py:82) | 주말/삼성전자 시세 부재 기반 판정. KIS 호출 의존성 1회 발생. 추가 토큰 갱신 부하. TODO P2 등록됨(`pykrx 1차 체크`). |

## 6. CRITICAL 이슈 상세

### CRIT-1. CIS 버그로 인한 점수 신뢰도 붕괴 (이미 PR 진행 중)
- **증거**: financial_metrics PL 77% 결손. ROE/operating_margin 77.7% 결손. revenue_growth_yoy/op_income_growth_yoy 77% 결손.
- **영향**: TOP 10 구성과 신호 판정에 강한 편향. 결손 종목이 ROE=0/성장=0으로 처리되어 점수 저평가, 또는 `_score_growth(0)=2점` 가산점으로 부분 보정되는 비논리적 상태.
- **수정 방향**: `fix/dart-cis-fallback` 브랜치 + 백필 도구 적용. **이미 준비됨**.
- **머지 동반**: ✓ (이번 PR 자체)

### CRIT-2. `_score_growth(0)`이 결손 데이터에 가산점 부여
- **증거**: `analysis/scorer.py:520-524` `_score_growth`. settings.py REVENUE_GROWTH_THRESHOLDS = `[(20.0,7),(10.0,5),(5.0,4),(0.0,2),(-10.0,1),(-20.0,0)]`. rate=0이면 첫 매칭 `0.0 → 2점`. OP_INCOME_GROWTH_THRESHOLDS도 마찬가지로 0에서 2점.
- **영향**: 현재 결손 77%×(+4점). CIS 백필 후 일부 종목은 진짜 0이 아니라 음수(예: 020150 net_income=-1672억)로 변경 → 점수 -2점~-4점 떨어질 가능성. 백필을 하면 점수 변동이 단순 회복 방향이 아니라 일부는 *내려간다*. TOP 10 변동 예측 어려움.
- **수정 방향** (옵션):
  - (a) `_score_growth`에서 결손값(예: `rate is None` 또는 `prev_revenue=0` 신호)을 명시 식별해 DEFAULT_SCORE=0으로 보내기. dict의 "결손" 신호를 만들어야 함.
  - (b) THRESHOLDS의 0.0 라인을 약간 음수로 옮겨 `(0.01, 2)`로 명시 — 결손 0이 매칭 안 되도록.
  - (b)가 1줄 수정으로 가능. 그러나 진짜 0% 성장(드물지만 가능) 종목도 1점 강등. 보수적 안전 방향.
- **머지 동반 여부**: **CIS 머지와 함께 처리 권장**. 백필을 적용하는 그 순간 점수 변동에 silent fail 영향이 섞이기 때문에, fallback 도입과 동시에 가산점 버그도 막아야 점수 변동이 단방향(상승 또는 회복)으로 해석 가능.

### CRIT-3. `performance_tracking` 100% 미수집 → 백테스트 자료 부재
- **증거**: 82건 전수에서 `price_after_1w`/`return_1w` 모두 0. `last_updated` 80건 빈 문자열. `daily_report_log`에는 19일 전(2026-04-07) 11건의 분석 종목이 있는데 추적 결과 0.
- **영향**: TODO P1 "performance_tracking 실제 적중률 분석" 진입 불가. 신호 적중률을 누구도 측정할 수 없는 상태에서 시스템 운영. 백테스트 엔진(P2) 선결조건도 미충족.
- **수정 방향**:
  - `database/models.py:1170 update_performance_tracking` 흐름 디버깅 필요. 가설: `kis_client.get_stock_price`가 sync 래퍼 내부에서 0 반환하는 케이스가 다수. 또는 `daily_report_log`에서 distinct 종목이 빠지는 경로.
  - 실제 운영 로그(`logs/`) 확인 필요. "성과 추적 N건 업데이트" 로그가 매번 0이거나 미발생인지 점검.
- **머지 동반 여부**: **별도 작업** (이번 PR 스코프 외).

## 7. HIGH 이슈 상세

### HIGH-1. `_score_debt(ratio=0)` → 5점 만점 부여
- **증거**: scorer.py:431-433. settings.py DEBT_RATIO_MAX_SCORE=5.
- **영향**: 부채비율 결손 11.6%(financial_metrics)에서 5점 자동. 결손 종목 점수 인플레이션.
- **수정**: `_score_debt`에서 `ratio<=0` 분기를 DEFAULT_SCORE(0)로 변경. CRIT-2와 한 PR로 묶을 수 있음.

### HIGH-2. `dividend_yield` DART 배당 API 결손 28.8%
- **증거**: financial_metrics 67/233. scorer의 dividend 점수 결손 종목에서 0점 부여.
- **수정**: `_get_dividend_yield`(dart_api.py:397) 응답 케이스 점검. 별도 조사 필요.

### HIGH-3. 결손 분석일 누적 (전체 233 vs 분석 49)
- **증거**: 평일 분석 49개 vs 월요일 215개. 종목 풀이 사이클별로 변동.
- **영향**: 평일 분석에서 누락된 종목들의 데이터는 staleness 누적. 단 의도된 동작일 가능성도 있음(필터 통과 종목만 분석 vs 전수 분석).
- **수정**: 운영 정책 확인 후 결정. 코드 변경은 아닐 수 있음.

### HIGH-4. `consecutive_loss_years` 결손 94.8%
- **증거**: financial_metrics 221/233. 백필을 해도 prev_year(2024) DB 행 부재라 일부만 회복.
- **수정**: `_check_consecutive_*`(dart_api.py:486)는 DART API 재호출/캐시 조회를 함. 캐시에 2024 parquet 있음. 백필 시 prev 캐시도 재처리하는 로직 추가하면 회복 가능. 별도 작업.

## 8. MEDIUM/LOW 이슈

| ID | 등급 | 내용 |
|---|---|---|
| MED-1 | M | `_is_financial_sector` always False (이미 TODO P1) |
| MED-2 | M | `analysis_results.kospi_index` 100% 0 (이미 TODO P1) |
| MED-3 | M | `sector_averages.avg_ev_ebitda=0`이 보험/의료/증권 3개 sector. EBITDA 결손에 의존. 백필 후 일부 회복 추정. |
| MED-4 | M | `is_trading_day()`가 KIS API 호출 (토큰 갱신 부하). 이미 TODO P2 (pykrx 캘린더). |
| MED-5 | M | `signals.py` 3년 연속 적자 필터가 결손 데이터에서 무력화 (consecutive_loss_years=0이 결손과 무손실 둘 다 의미). |
| LOW-1 | L | `signal_correct` 의미 불명확 (best_return>0 vs <0의 비대칭 — sell만 음수 적중 인정). 정의 명확화. |
| LOW-2 | L | `dart_cache/corp_codes.csv` 30일 캐시 — 30일 동안 신규 상장 종목 매핑 못함. |
| LOW-3 | L | `_get_account_value`의 `regex=False` 적용은 했으나 다른 contains 호출이 있는지 추가 점검. |
| LOW-4 | L | `update_performance_tracking` UPSERT의 last_updated가 빈 문자열로 들어가는 경로(추정). 명시 검증 필요. |

## 9. 권고

### 함께 머지할 수정 (작은 변경, 같은 PR 또는 동일 사이클)
- **CRIT-2**: `_score_growth` 0점 silent fail 차단 — `THRESHOLDS`에서 `(0.0, 2)` → `(0.01, 2)` 1줄 수정 + 단위 테스트.
- **HIGH-1**: `_score_debt(0)` → DEFAULT_SCORE 1줄 수정 + 단위 테스트.
- 이유: 백필을 적용하는 순간 점수가 결손→정상값으로 흐름. 동시에 silent fail이 살아있으면 점수 변동 해석 불가.

### 별도 작업으로 분리할 수정
- **CRIT-3** performance_tracking 미수집 — 별도 디버깅(로그 분석 + 직접 호출 테스트). 공수 M.
- **HIGH-2** dividend_yield 결손 — DART 배당 API 응답 점검. 공수 S.
- **HIGH-4** consecutive_*_years 백필 — `tools/backfill_dart_pl.py` 확장(prev 캐시 사용). 공수 S.
- **MED-1/2/4** TODO 기존 항목 — 일정대로.
- **MED-3/5, LOW-1~4** — 백필·CIS 머지 후 사이클이 한 번 돌고 점수 변동 안정되면 재평가.

### 추가 조사 필요한 영역
- KIS 응답 raw 결손율 (DB에 stash 안 됨). 봇 재시작 후 첫 사이클에 표본 로깅 권장.
- TOP 10에서 사라지거나 새로 진입할 종목 후보 — 백필 dry-run에서 점수 재계산 시뮬레이션.
- 사이클별 분석 종목 수 (49 vs 215) 차이의 코드 원인.

## 10. 결정용 요약

권장 머지 시나리오:

**옵션 A (보수, 추천)**: CIS 머지에 CRIT-2 + HIGH-1 두 줄 수정 추가 → 한 PR로 머지. 그 후 백필 `--apply` → 봇 재시작.
- 변경 라인 ≤ 5줄, 테스트 2~3건 추가.
- 점수 변동 해석이 단방향이고 회귀 위험 작음.

**옵션 B**: CIS만 단독 머지 → 백필 → 봇 재시작 → 별도 PR로 silent fail 수정.
- CIS PR 변경량 최소.
- 단점: 백필 후~silent fail 수정 사이 한 사이클 동안 결손→정상 변동에 silent fail이 섞여 점수 변동을 깔끔히 해석 불가.

**옵션 C**: 모든 CRITICAL/HIGH 처리 후 일괄 머지.
- 가장 안전하나 일정 길어짐. CRIT-3(performance_tracking)는 디버깅 시간 불확실.

추천 = **옵션 A**. 그 후 별도 브랜치로 CRIT-3, HIGH-2/4를 차례로 처리.

---

## 11. 패치 영향 시뮬레이션 (2026-04-26 추가)

CRIT-2 + HIGH-1 패치를 동일 브랜치에 적용 후, financial_metrics(2025 annual) 233건 전수에 대해 패치 전/후 `_score_growth(매출) + _score_growth(영업) + _score_debt(부채)`(최대 17점)의 변화를 시뮬레이션.

### 표본 (정상 종목)

| code | name | rev_g | op_g | debt | legacy | patched | Δ |
|---|---|---:|---:|---:|---:|---:|---:|
| 005930 | 삼성전자 | 10.88 | 33.23 | 29.94 | 17 | 17 | 0 |
| 004170 | 신세계 | 5.47 | 0.62 | 240.91 | 6 | 6 | 0 |
| 000270 | 기아 | 6.23 | -28.33 | 61.76 | 7 | 7 | 0 |
| 008770 | 호텔신라 | 3.06 | 0.00 | 320.14 | 4 | 2 | -2 |
| 035250 | 강원랜드 | 0.00 | 0.00 | 122.21 | 5 | 1 | -4 |

005930·004170·000270 변화 0. 호텔신라(영업이익 0)·강원랜드(rev/op 모두 0)는 사실상 결손값을 가산점으로 받던 케이스 — 패치가 일관 0점으로 정정.

### 표본 (결손 종목)

| code | name | rev_g | op_g | debt | legacy | patched | Δ |
|---|---|---:|---:|---:|---:|---:|---:|
| 023530 | 롯데쇼핑 | 0.00 | 0.00 | 124.77 | 5 | 1 | -4 |
| 000660 | SK하이닉스 | 0.00 | 0.00 | 45.95 | 9 | 5 | -4 |
| 035720 | 카카오 | 0.00 | 0.00 | 182.49 | 5 | 1 | -4 |
| 011170 | 롯데케미칼 | 0.00 | 0.00 | 76.52 | 7 | 3 | -4 |
| 207940 | 삼성바이오로직스 | 0.00 | 0.00 | 48.44 | 9 | 5 | -4 |

5종목 모두 Δ=-4 (매출 -2, 영업 -2). 부채비율은 결손이 아니므로 변화 없음. 백필 적용 시 진짜 데이터 기반으로 점수 재계산되면 일부는 회복, 일부(영업이익 음수)는 정확하게 반영됨.

### 전체 통계 (n=233)

| 지표 | 값 |
|---|---:|
| zero growth (rev_g=0 AND op_g=0) | 178 (76.4%) |
| zero debt_ratio | 27 (11.6%) |
| Δ < 0 (점수 하락 = 가산점 회수) | **185** |
| Δ = 0 (변화 없음) | 48 |
| **Δ > 0 (점수 상승, 회귀)** | **0** ← 회귀 없음 |
| 평균 Δ | **-3.66** |

**해석**:
- 결손 데이터에 부여되던 평균 +3.66점의 silent fail 가산점이 회수됨.
- 어떤 종목도 점수가 올라가지 않음 → 정상 종목 회귀 없음.
- 이 패치 단독으로는 모두 점수가 떨어지거나 그대로. 백필을 함께 적용하면 진짜 PL 값 기반으로 점수가 상승 또는 정확 음수 반영.
- 이 두 변화의 부호가 명확히 분리되므로(silent fail 회수=하락, 백필=상승) 머지 후 점수 변동 모니터링이 가능.

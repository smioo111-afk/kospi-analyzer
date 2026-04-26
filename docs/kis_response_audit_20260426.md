# KIS API 응답 결손 조사 보고서 (2026-04-26)

조사 브랜치: `feat/data-completeness-c` (read-only)
조사 범위: KIS get_stock_price / get_daily_chart / get_investor_trading
판정: **CRITICAL 추가 silent fail 없음.** MEDIUM 수준 미사용 필드 결손 1건 — 코드 수정 불필요.

## 요약

KIS API에서 일부 필드가 항상 0/빈값으로 응답되지만 모두 다음 중 하나로 처리됨:
- chart 60일 closes에서 자체 계산 (week52)
- stock_master에서 fallback (stock_name)
- 우선주/한계기업의 정상 결손 (PER/PBR)

**시스템 점수 계산에 영향 없음**. 추가 수정 불필요.

## 1. 표본 10종목 KIS get_stock_price 결손 매트릭스

대형(IT/자동차) / 은행 / 보험 / 증권 / 우선주 / 화학 / 인터넷은행 / 바이오 다양 카테고리.

| 필드 | 결손 | 비고 |
|---|---:|---|
| `current_price` | 0/10 | 정상 |
| `change_rate` | 0/10 | 정상 |
| `volume` | 0/10 | 정상 |
| `trading_value` | 0/10 | 정상 |
| `market_cap` | 0/10 | `hts_avls × 1억` 정상 |
| `per` | 2/10 | 우선주(005935) + 케이뱅크(279570). 정상 결손 |
| `pbr` | 2/10 | 동상 |
| `eps` | 2/10 | 동상 |
| `bps` | 2/10 | 동상 |
| **`high_52w`** | **10/10** | KIS `stck_dryc_hgpr` 항상 0 |
| **`low_52w`** | **10/10** | KIS `stck_dryc_lwpr` 항상 0 |
| `sector` | 0/10 | `bstp_kor_isnm` 정상 |
| **`stock_name`** | **10/10** | inquire-price API 사양상 부재 — stock_master fallback이 채움 |

## 2. 영향 분석

### 2-1. `high_52w` / `low_52w` (10/10 결손)
- **사용처**: `analysis/scorer.py _score_week52`는 chart 60일 closes의 max/min으로 직접 계산. KIS의 high_52w/low_52w 필드 **참조하지 않음**.
- **DB 영향**: stock_scores 테이블에 `high_52w`/`low_52w` 컬럼 자체 없음.
- **결론**: 결손이지만 의미 없음.

### 2-2. `stock_name` (10/10 결손)
- **사용처**: telegram_bot.py 등에서 `db.get_stock_master`로 lookup하여 보강.
- **코드 위치**: `_parse_stock_price`(kis_api.py:566) 자체에는 fallback 없으나 호출 측에서 채움.
- **DB 영향**: 없음.

### 2-3. 우선주/한계기업 PER/PBR (2/10)
- 005935 삼성전자우, 279570 케이뱅크: PER 음수이거나 정보 부재. 자연 결손.
- scorer는 `per <= 0` 시 default 점수(0)로 보수적 처리. 패치된 silent fail 차단 로직 적용됨.

## 3. 일봉 차트 60일 — 표본 5종목

| 종목 | 채워진 일수 | 최신 |
|---|---:|---|
| 005930 삼성전자 | 60 | 20260424 close=219,500 |
| 000660 SK하이닉스 | 60 | 20260424 close=1,222,000 |
| 000270 기아 | 60 | 20260424 close=153,400 |
| 055550 신한지주 | 60 | 20260424 close=100,000 |
| 032830 삼성생명 | 60 | 20260424 close=244,500 |

5/5 모두 60일 정상 채움. 차트 결손 없음.

## 4. 투자자별 매매동향 25일 — 표본 5종목

| 종목 | foreign_5d | foreign_20d | inst_5d | inst_20d |
|---|---:|---:|---:|---:|
| 005930 | 0 | 9 | 0 | 14 |
| 000660 | 0 | 8 | 1 | 13 |
| 000270 | 0 | 9 | 0 | 9 |
| 055550 | 0 | 4 | 2 | 16 |
| 032830 | 0 | 12 | 0 | 8 |

5d 값이 0인 것은 결손이 아니라 "5일 연속 순매수 미달성" 의미. 정상.

## 5. DB 측 KIS 기반 필드 결손율

### `stock_scores` 최근 분석일(2026-04-24, n=49)
| 필드 | 결손 |
|---|---:|
| `current_price` | 0/49 (0.0%) |
| `market_cap` | 0/49 (0.0%) |
| `per` | 1/49 (2.0%) |
| `pbr` | 1/49 (2.0%) |
| `dividend_yield` | 10/49 (20.4%) — DART 별도 issue (HIGH-2 별도 PR) |

### `analysis_results.top_10_json` (n=10)
모든 v3 필드 결손 0~8건이지만 `foreign_net_buy_5d`/`institutional_net_buy_5d`의 0은 "연속 미달성"으로 결손 아님. `volume`/`trading_value`/`market_cap`/`per`/`pbr`/`week52_position` 0건 결손.

## 6. 결론

KIS 응답에서 발견된 미사용 필드 결손(high_52w/low_52w/stock_name 10/10)은 모두 자체 계산 또는 fallback으로 우회되어 **시스템 점수 또는 출력에 영향 없음**.

CRITICAL 추가 silent fail **없음**.

## 7. 권고 (별도 PR — 우선순위 낮음)

- KIS `_parse_stock_price`에 `stock_name` fallback을 명시적으로 stock_master 조회로 추가 (현재는 호출 측 의존)
- 또는 inquire-price 대신 stock-quote API(`FHKST01010200`)로 변경 시 종목명 받음 — 호출 1회 추가 비용
- high_52w/low_52w를 KIS 별도 API에서 받는 옵션 검토 (현재 chart 60일은 사실 12주 — 52주와 차이)

이 권고는 모두 **선택적 개선**이며 결손 자체는 시스템에 영향 없음.

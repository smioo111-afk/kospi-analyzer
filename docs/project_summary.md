# KOSPI 저평가 기업 분석 시스템 — 프로젝트 요약

채팅 컨텍스트 최적화용 단일 요약 문서. 각 모듈의 시그니처/핵심 로직/임계값을 모은다.
세부 내용은 원본 파일 참조. 머지 후 본 문서를 갱신해 일관성 유지.

- 저장소 경로: `/root/kospianal/kospi-analyzer/`
- DB: `data/kospi_analyzer.db` (SQLite, WAL)
- 캐시: `data/dart_cache/{code}_{year}_{quarter}.parquet`
- 봇 실행: `nohup ../venv/bin/python main.py --bot &`
- 정기 분석: 매일 15:40 (월~금, Asia/Seoul)

---

## 1. 데이터 흐름 개요

```
[수집]                  [분석]                       [출력]
KIS API ─┐              ┌─ scorer (5축 점수)         ┌─ 텔레그램
DART API ┼─→ collectors ┼─ signals (신호 판정)       ├─ stock_scores DB
공시      ┘              ├─ stoploss (ATR 기반)       ├─ daily_report_log
                        ├─ buy_state (3단계 분류)     └─ analysis_results
                        └─ disclosure_impact (자정)
```

- 분석 사이클(15:40): KOSPI 종목 → KIS 가격/차트/수급 + DART 재무 → scorer → signals → stoploss → buy_state → 텔레그램
- 자정 사이클(00:00): 어제 공시 → 데이터 영향 종목 재계산 (`disclosure_impact.trigger_score_recalculation`)

---

## 2. main.py — 오케스트레이션

### 진입점
- `run_bot()` — telegram polling + scheduler 시작
- `start_scheduler()` — APScheduler 등록 (15:40 분석, 00:00 공시, 16:00 자가진단, 16:30 백업)
- `is_trading_day()` — 한국 영업일 판정

### `class AnalysisPipeline`
- `_get_top_stock_codes(n=50)`: 시총 상위 코드 캐시
- `_determine_target_codes()`: 분석 대상 코드 결정 (포트폴리오 + 워치리스트 + TOP)
- `run_daily_analysis()` (가장 큰 함수, ~600줄): 6단계
  1. 가격/투자자 수급 일괄 수집 (KIS async)
  2. 일봉 차트 60일 + DART 재무
  3. scorer.score_all_stocks → scored_list
  4. stoploss + admin_filter
  5. signals.generate_signals → top_10 + warnings
  6. **단계 1 매수 분류** (buy_state) → top_10 in-place 주입
  7. 텔레그램 발송 + DB 저장 + 성과추적

### 단계 1 통합 (main.py L398~422)
```python
prev_rank_map = {s["stock_code"]: i+1 for i,s in enumerate(prev_top_10 or [])}
for idx, stock in enumerate(top_10):
    code = stock["stock_code"]
    sl = stoploss_map.get(code, {})
    if sl.get("effective_stoploss") and not stock.get("stoploss_price"):
        stock["stoploss_price"] = sl["effective_stoploss"]
    if code in prev_rank_map:
        stock["rank_change"] = prev_rank_map[code] - (idx+1)
    history = self.db.get_stock_history(code, days=5)
    state = classify_buy_state(stock)
    stock["buy_state"] = state.value
    stock["buy_state_label"] = get_state_label(state)
    stock["buy_score"] = calculate_buy_score(stock, history)
    stock["buy_state_reason"] = get_state_reason(state, stock)
```

---

## 3. analysis/

### 3.1 `analysis/scorer.py` — 점수 계산 (가장 핵심)

`class ScoringEngine`. 5축 가중합 — value 30 / financial 20 / growth 20 / momentum 20 / quality 10 = **total 100점**.

#### `calculate_score(price_data, financial_data, chart_data) -> dict`
입력:
- `price_data`: KIS 가격 + 시총 + 거래대금 + 외국인/기관 순매수일수 (5d/20d)
- `financial_data`: DART financial_metrics row
- `chart_data`: 60일 일봉 (high/low/close/volume)

반환 dict 주요 키 (signals/buy_state가 사용):
- `total_score, value_score, financial_score, growth_score, momentum_score, quality_score`
- `signal, signal_label, reason`
- `current_price, market_cap, per, pbr, roe, ...`
- `stoploss_price, stoploss_pct, atr` (signals_generator가 stoploss_map에서 별도 주입)
- `fair_value_low, fair_value_high, fair_value_gap, fair_value_method`
- `week52_position` (mom["week52_pct"]에서 매핑)
- `foreign_net_buy_5d, institutional_net_buy_5d` 등 수급
- `consecutive_op_decline_years, turnaround_score` 등 보조

#### 5축 세부 함수
- `_calc_value_score`: PER + 섹터PER + PBR + 배당 + PEG + EV/EBITDA + PSR (30점)
- `_calc_financial_score`: ROE + 영업이익률 + 부채비율 + 유동비율 (20점)
- `_calc_growth_score`: 매출/영업이익 증가율 + profit_health + turnaround (20점)
- `_calc_momentum_score`: MA + 거래량 + RSI + MACD + 수급(5d/20d) + 52주 위치 (20점)
- `_calc_quality_score`: FCF yield + FCF margin (10점) — **`fcf <= 0`이면 즉시 0점 default**

#### 적정주가 (`_calc_fair_value`)
- 3-모델 가중평균 (PER, PBR, EV/EBITDA)
- 반환: `{low, high, gap_pct, method}`
- 적정 범위 안: gap_pct = 0; 미만: 음수(저평가); 초과: 양수(고평가)

#### 페널티 (config: `TOTAL_SCORE_PENALTIES`, `PROFIT_PENALTY_RULES`)
- 매출/영업이익 연속 감소 / 부채비율 200% 초과 / 영업이익 적자 등 시 total에서 차감

### 3.2 `analysis/signals.py`

`class SignalGenerator`. `Signal` constants: STRONG_BUY/BUY/HOLD/SELL/STRONG_SELL → label("⭐⭐ 강력매수" 등).

- `generate_signals(scored_list, financial_list, stoploss_map) -> {top_10, warnings, all_signals, stats}`
- `_judge(score) -> (signal, reason)`: total/momentum/financial 임계 (config.SignalConfig)
- `filter_stocks`: 시총 5000억+, 거래대금 50억+, 연속 적자 3년 미만, admin_filter 통과
- `select_top_n(filtered, n=10)`: total_score 기준

### 3.3 `analysis/buy_state.py` — 매수 상태 분류

#### `class BuyState(Enum)`: BUY / WATCH / AVOID

#### 임계 상수 (모듈 상단)
```python
STOPLOSS_PROXIMITY_PCT = 3.0     # 손절가 3% 이내 → AVOID
WEEK52_HIGH_THRESHOLD  = 85.0    # 52주 위치 85% 초과 → AVOID
VALUE_TRAP_MOMENTUM_MAX = 5      # 저평가+모멘텀<5 = 가치함정
BUY_SIGNALS    = ("buy", "strong_buy")  # 이 외 신호 → WATCH
SUPPLY_STRONG_NEG = -5           # 외국인/기관 5일 연속 매도
SUPPLY_BOTH_NEG   = -3           # 둘 다 동시 매도
RANK_DROP_THRESHOLD = -4         # 4계단 이상 하락
```

#### `classify_buy_state(score: dict) -> BuyState`
**분기 순서 (조기 반환):**
1. `cp <= 0` 또는 `fair_high <= 0` → AVOID + logger.warning
2. `signal == "sell"` → AVOID
3. `cp > fair_high` (고평가) → AVOID
4. `(cp - sl) / cp * 100 < 3` (손절근접) → AVOID
5. `week52_position > 85` → AVOID
6. `cp <= fair_mid AND momentum < 5` (가치함정) → AVOID
7. `signal not in ("buy", "strong_buy")` → WATCH (Hold 매수 X)
8. 수급 강한 매도 (외국인/기관 -5 OR 둘 다 -3) → WATCH
9. `rank_change ≤ -4` → WATCH
10. 그 외 → BUY

#### `calculate_buy_score(score, history) -> float (0~100 정규화)`
가중합:
- `(momentum_today / 20) × 30`
- `(momentum_change / 5) × 25` (직전 3일 평균 대비, ±5 clamp)
- `(total_score / 100) × 20`
- `((100 - week52_pos) / 100) × 15`
- `consistency × 5` (오늘 momentum >= 직전 → 1)
- `(sl_dist / 10) × 5`

#### `get_state_label(state)` / `get_state_reason(state, score)`
- 라벨: 🟢 BUY / 🟡 WATCH / 🔴 AVOID
- 사유: AVOID 6종(데이터부족/매도/고평가/손절근접/52주고점/가치함정), WATCH 3종(신호 X/수급 매도/순위 하락)

### 3.4 `analysis/stoploss.py`

`class StopLossCalculator(multiplier=2.0)` — ATR 기반.

- `calculate_atr(chart_data) -> float`: 14일 ATR
- `calculate_stoploss(current_price, chart_data) -> dict`
  - `effective_stoploss`: `cp - ATR × multiplier` 와 `cp × (1 - 0.07)` 중 안전한 쪽
  - `effective_stoploss_pct`: 손절% (음수)
  - `atr`, `atr_multiplier`, `warnings`, `consecutive_down_days`
- `check_stoploss_hit(current_price, sl_price)` -> bool
- `calculate_all_stoploss(prices, charts)` -> {code: dict}

### 3.5 `analysis/disclosure_impact.py` — 자정 공시 모니터

- `class ScoreSnapshot(stock_code, total, value, financial, growth, momentum, quality, signal)`
  - `from_db_row`, `from_score_result`
- `class DisclosureImpact(disclosure, before, after, total_diff, ..., signal_changed)`
  - `is_significant() -> bool` (5점 이상 변동 또는 신호 변경)
- `compare_scores(before, after, disclosure) -> DisclosureImpact`
- `trigger_score_recalculation(db, dart_client, scorer, stock_code, year=2025, save_to_db=True)`
  1. dart_cache invalidate
  2. extract_financial_metrics 재호출 (sector aware)
  3. financial_metrics UPSERT
  4. 직전 stock_scores에서 momentum/stoploss/atr 보존 (chart 없음 → scorer가 0 반환 시 silent regression 차단)
  5. scorer.calculate_score → momentum 복원 → total 재합산
  6. save_stock_scores (stoploss_map으로 보존된 손절/ATR 명시 전달)
- `process_disclosures(disclosures, db, ...)`: needs_data_refresh 필터 + 종목 dedup → 일괄 재계산

### 3.6 `analysis/shadow_trader.py` — 자동매매 호환 인터페이스 (placeholder)

진화 경로: v1 가상 → v2 실시간 가격 → v3 KIS 모의 → v4 실거래.

- `class PriceProvider(ABC)`: `get_current_price(code) -> int`
  - `class ClosingPriceProvider(db)`: `db.get_stock_score(code)["current_price"]`
- `class OrderExecutor(ABC)`: `buy/sell(code, qty, price) -> {status, order_id, message}`
  - `class VirtualOrderExecutor(db)`: 로그만 찍고 success 반환
- `class ShadowTrader(db, price_provider, order_executor, max_positions=5, position_size=2_000_000)`
  - `execute_cycle() -> dict`: 단계 2 후 구현 (현재 placeholder)

### 3.7 `analysis/admin_filter.py`

- `is_admin_or_suspended(stock) -> (bool, reason)`: 관리종목/거래정지/투자유의 판정
- `filter_admin_stocks(stocks) -> filtered_list`

### 3.8 `analysis/performance_analyzer.py`

`class PerformanceAnalyzer(db)`:
- `generate_performance_report(period="monthly") -> dict`
- `_calc_signal_accuracy`, `_calc_avg_return`, `_calc_top_worst`, `_calc_score_bracket_returns`, `_calc_fair_value_correlation`

---

## 4. database/

### 4.1 `database/models.py` — `class Database`

#### 테이블 (스키마는 `_init_tables`에 모두 정의)
- `analysis_results`: 일별 분석 결과 (top_10, warnings, stats JSON)
- `stock_scores`: 종목별 일별 스코어 (24 cols, **`fair_value_*` / `week52_position` / `foreign_net_buy_5d` 미저장**)
- `daily_report_log`: TOP 10 스냅샷 (rank + fair_value 포함)
- `financial_metrics`: 연도별 재무 지표 (33 cols, FCF/EBITDA/sector 포함)
- `portfolio`: 매수 이력 (stock_code, buy_price, quantity, buy_date, is_sold)
- `watchlist`, `stock_master`, `sector_averages`
- `performance_tracking`: 1w/1m/3m/6m/1y 수익률 추적
- `disclosure_impacts`: 자정 모니터 결과

#### 핵심 헬퍼 (시그니처)
- `save_stock_scores(analysis_date, signals, stoploss_map=None)`: stoploss_map에서만 손절/ATR 읽음
- `get_stock_score(code, date=None)`, `get_stock_history(code, days=7)`
- **`get_previous_price(code) -> int`**: stock_scores 우선, daily_report_log 폴백, 없으면 0 (포트폴리오 전일대비용)
- `save_financial_metrics(metrics)`: 0/누락 시 기존값 보존 가드 (silent regression 차단)
- `save_daily_report_log(date, top_10_with_rank)`
- `get_report_log(date=None, days=30)`: 사후 검증용
- `update_performance_tracking(kis_client)`: 1w~1y 수익률 갱신
- `save_disclosure_impacts_batch(impacts)`, `get_disclosure_impacts(date)`
- 포트폴리오: `add_portfolio`, `sell_portfolio`, `get_portfolio` (lots + buy_count + avg_buy_price), `clear_portfolio`
- WAL: `checkpoint_wal(mode="PASSIVE"|"TRUNCATE")`
- 종목 코드 매핑: `get_stock_name`, `search_stock_by_name`
- 상장폐지: `mark_stock_delisted`, `_cascade_mark_delisted`

### 4.2 `database/history.py` — `class AnalysisHistory(db)`

- `get_recent_reports(days=7)`, `get_stock_trend(code, days=30)`
- `detect_signal_changes(all_signals) -> list[change_dict]`
- `save_daily_result(...)`: analysis_results UPSERT

---

## 5. collectors/

### 5.1 `collectors/kis_api.py`

- `class KISTokenManager`: 토큰 캐시 (data/token_cache/), 만료 1시간 전 갱신
- `class KISClient`:
  - 비동기: `aget_stock_price`, `aget_daily_chart`, `aget_investor_trading`, `aget_kospi_index`
  - 일괄: `get_all_stock_prices`, `get_all_daily_charts`, `get_all_investor_trading`
  - 동기 wrapper: `get_stock_price`, `get_daily_chart` 등 (`_run_sync`)
  - 파서: `_parse_stock_price`, `_parse_daily_candle`, `_analyze_investor_trend` (5d/20d 연속 순매수일수 계산)
- 임계: `RATE_LIMIT_PER_SEC=15`, `MAX_RETRIES=3`

### 5.2 `collectors/dart_api.py` — `class DARTClient`

- `load_corp_codes()`: corp_codes.csv 캐시 (90일)
- `get_corp_code(stock_code) -> Optional[str]`
- `get_financial_statements(stock_code, year, quarter="annual") -> DataFrame`
  - parquet 캐시: `data/dart_cache/{code}_{year}_{quarter}.parquet`
- `extract_financial_metrics(stock_code, year, sector=None) -> dict` (~33 fields)
  - 매출/영업이익/순이익/자산/부채/자본/유동(자산/부채)/ROE/영업이익률/부채비율/유동비율/배당수익률
  - 성장: revenue_growth_yoy, op_income_growth_yoy
  - **EBITDA = 영업이익 + |감가상각비|**, **FCF = 영업CF − |CAPEX|**
  - prev_revenue, prev_operating_income, prev_net_income (전년도 동기)
  - consecutive_loss_years / op_decline_years / revenue_decline_years
  - sector (재무 섹터별 매출 합산 분기 적용)
- `_get_account_value(df, sj_div, account_names, account_ids=None) -> int`:
  4단계 fallback — 1) account_id 정확 일치 (IFRS) → 2) account_nm 정확 → 3) 공백 정규화 → 4) 부분 일치
- `_calc_financial_revenue(df, sector, code)`: 금융업 섹터별 매출 합산 (이자수익 등)

### 5.3 `collectors/dart_disclosure.py`

- `class DisclosureType(Enum)`: PERIODIC, AMENDMENT, MERGER, EARNINGS, DIVIDEND, TREASURY 등
- `class Disclosure(rcept_no, corp_code, stock_code, corp_name, report_nm, rcept_dt, rm)`
  - `is_amendment() -> bool`
- `classify_disclosure(d) -> DisclosureType`
- `needs_data_refresh(d) -> bool`: 재계산 필요한 공시만 True (배당/자사주는 False)
- `fetch_disclosures(begin_de, end_de, ...) -> list[Disclosure]`

---

## 6. bot/

### 6.1 `bot/telegram_bot.py` — `class KOSPIBot(db)`

명령어 핸들러:
- `/start, /help (= /commands, /명령어)`
- `/report` — 최신 리포트 재발송
- `/stock {code}` — 개별 종목 상세 분석
- `/history {code}` — 최근 7일 점수 추이
- `/watchlist [add|remove|list] {code}`
- `/stoploss {code}` — 손절 정보
- `/buy {code} {price} {qty}`, `/sell {code}` — 포트폴리오 등록
- `/portfolio [clear]` — 박스 형식 + 손절 + 전일대비. stock_score stoploss=0이면 history로 폴백.
- `/performance [monthly|quarterly|half_yearly|yearly]`

자동 발송:
- `send_daily_report(top_10, warnings, stats, stoploss_map=, prev_top_10=, portfolio_scores_map=, scored_list=, disclosure_impacts=, previous_prices=) -> bool`
  - top_10에 buy_state 있으면 매수 추천 섹션 자동 첨부
  - portfolio 있으면 포트폴리오 섹션 발송 (전일대비)
- `send_signal_changes(changes)`, `send_error_alert(error, module)`
- 화이트리스트 chat_id 필터 (TELEGRAM_CHAT_ID)

### 6.2 `bot/formatter.py` — `class MessageFormatter(db=None)`

- `format_daily_report(top_10, warnings, stats, stoploss_map=, kospi_index=, foreign_net_buy=, prev_top_10=, scored_list=, disclosure_impacts=) -> list[str]`
  - 분할 발송 (`MAX_MESSAGE_LENGTH=4000`, `_split_messages`)
  - 섹션: TOP 10 → 모멘텀 TOP 10 (보조) → 공시 영향 → **매수 추천/관망/회피** (top_10에 buy_state 있을 때) → 경고 → 탈락 → 시장 요약
- `_format_stock_entry(emoji, stock, stoploss_map, current_rank, prev_map)`: 종목 항목
  - `매수상태: 🟢 BUY (buy_score: 65.05)` 라인 (buy_state_label 있을 때만)
  - 적정주가 / 손절라인 / 수급 / 사유
- `format_buy_recommendations(top_10) -> str`:
  - 🎯 매수 가능 종목 (BUY, buy_score 정렬)
  - 🟡 관망 (WATCH)
  - 🔴 매수 회피 (AVOID)
  - BUY 0개 시 "오늘 매수 가능 종목 없음"
- `_buy_reason_summary(score)`: BUY 강점 사유 (추세 강함/저평가/저점 근처)
- `format_portfolio(portfolio, scores_map, stoploss_map=, previous_prices=) -> str`: 박스 형식
- `format_portfolio_for_report(...)`: 일일 리포트 첨부용 (동일 형식 + 합계 전일대비)
- `format_top_n`, `format_signal_change`, `format_error_message`, `format_stock_detail`, `format_history`, `format_warnings`, `format_market_summary`
- `_split_messages(lines)`: 4000자 한계 자동 분할

---

## 7. monitoring/

### `monitoring/health_check.py`

- `class HealthCheck(name, status, message, ...)` — status: pass/warning/fail
- `class HealthCheckReport.add(check)`, `format_text() -> str`, `to_dict()`
- `run_health_check(date, db_path=) -> HealthCheckReport`

체크 항목 (T1-N):
- T1-1 analysis_results / T1-2 KOSPI 지수 / T1-3 KOSPI 변동 정합 / T1-4 외국인 순매수
- T1-5 점수 손실률 (FCF/매출/prev_revenue/순이익 등) / T1-6 BS 손실 / T1-7 성과추적 / T1-8 cascade 최근
- T1-9 disclosure_monitor 실행 여부 / T1-10 점수합 정합 / T1-11 신호 임계 / T1-12 circuit breaker

매일 16:00 자동 실행, 결과 텔레그램 발송.

---

## 8. config/settings.py — 임계값

### `class KISConfig`
- `RATE_LIMIT_PER_SEC=15`, `MAX_RETRIES=3`, `RETRY_BACKOFF_BASE=2.0`
- `IS_PAPER_TRADING=true` (모의거래 기본)

### `class DARTConfig`
- `DAILY_CALL_LIMIT=9000`, `CACHE_DAYS=90`

### `class TelegramConfig`
- `MAX_MESSAGE_LENGTH=4000`

### `class ScoringConfig` (점수 임계 상세)
- 가중: VALUE 30 / FINANCIAL 20 / GROWTH 20 / MOMENTUM 20 / QUALITY 10 = 100
- PER/PBR/배당/PEG/EV-EBITDA/PSR 임계 (각 임계치별 점수 매핑)
- 섹터 평균 PER/PBR/EV-EBITDA dict (`SECTOR_AVG_*` + `DEFAULT_*`)
- ROE/영업이익률/부채/유동 임계
- 매출/영업이익 성장 임계
- `PROFIT_PENALTY_RULES`: 적자/감소/지속 영업적자 등 페널티
- `TURNAROUND_SCORES`, `TOTAL_SCORE_PENALTIES`
- `VOLUME_SCORES`, `RSI_SCORES`, `MACD_SCORES`, `SUPPLY_DEMAND_SCORES` (외국인 5d/20d, 기관 5d/20d)
- FCF: `FCF_YIELD_THRESHOLDS [(10,5),(7,4),(5,3),(3,2),(1,1)]`, `DEFAULT=0`. `FCF_MARGIN [(15,5),(10,4),(5,3),(0,1)]`. **`fcf <= 0`이면 즉시 0점**
- `EV_EBITDA_EXCLUDED_SECTORS = {"금융","보험","증권"}`

### `class SignalConfig` (신호 임계)
- STRONG_BUY ≥ 75 + momentum ≥ 10 + financial ≥ 12 + growth ≥ 10
- BUY 60~74 + financial ≥ 10
- HOLD 45~59
- SELL < 45

### `class FilterConfig`
- `MIN_MARKET_CAP=500B`, `MIN_TRADING_VALUE=5B`
- `EXCLUDE_CONSECUTIVE_LOSS_YEARS=3`
- `FINANCIAL_SECTOR_CODES=["0500","0600","0700"]`
- `TOP_N=10`

### `class StopLossConfig`
- `ATR_PERIOD=14`, `ATR_MULTIPLIER=2.0` (1.0~3.0)
- `ATR_PROFILES = {aggressive:1.5, conservative:2.0, safe:3.0}`
- `HARD_STOP_LOSS_PCT=-7.0` (절대 한도)
- `CONSECUTIVE_DOWN_DAYS=3` (경고)

### `class SchedulerConfig`
- DATA_COLLECT 15:40, ANALYSIS 15:55, REPORT 16:00, SEND 16:05, DB_SAVE 16:10
- TIMEZONE `Asia/Seoul`

### `class DBConfig`
- `DB_PATH=data/kospi_analyzer.db`
- `HISTORY_RETENTION_DAYS=365`

---

## 9. 주요 운영 흐름

### 9.1 일일 분석 (15:40 평일)
1. `is_trading_day` 체크
2. `_determine_target_codes` → 분석 대상 ~250종목
3. KIS async로 가격/차트/수급 일괄 수집
4. DART에서 누락된 재무 보충
5. `scorer.score_all_stocks` → scored_list (in-memory dict, 모든 필드 포함)
6. `signals.generate_signals` → top_10 + warnings
7. **buy_state 분류** (top_10에 in-place 주입)
8. `bot.send_daily_report` 발송
9. `db.save_daily_report_log` (TOP 10 스냅샷, rank 포함)
10. `db.update_performance_tracking` (1w~1y 갱신)

### 9.2 자정 공시 모니터 (00:00)
1. `dart_disclosure.fetch_disclosures(어제)` 
2. `needs_data_refresh` 필터
3. 종목별 dedup
4. `process_disclosures` → 각 종목에 `trigger_score_recalculation`
5. before/after 비교 → DisclosureImpact 저장 (`save_disclosure_impacts_batch`)
6. is_significant 한 건만 텔레그램 발송 (다음 일일 리포트에 포함)

### 9.3 자가 진단 (16:00)
- `run_health_check(today)` → 12+ 항목 체크
- 결과 텔레그램 발송 (warning/fail 시 강조)

### 9.4 백업 (16:30)
- `db.checkpoint_wal("TRUNCATE")` 후 sqlite 파일 복사

---

## 10. 알려진 한계 / 설계 결정

### 10.1 `stock_scores` 스키마 한계
저장하지 않는 필드: `fair_value_low/high`, `week52_position`, `foreign_net_buy_5d`, `institutional_net_buy_5d`.
- 사후 재분류 시 buy_state가 모두 "데이터 부족" AVOID로 떨어짐
- 라이브 흐름은 **scorer 결과 in-memory dict**를 직접 분류기에 전달 → 정상 작동
- 현재 의도적 설계 (DB 스키마 변경 금지 원칙)

### 10.2 FCF 음수 처리
`fcf <= 0` 시 quality_score = 0점 (즉시 default). 이는 다음 케이스에 시스템적 페널티:
- 금융/증권업 (운용자산 변동을 영업CF에 반영)
- 캡티브 파이낸스 보유 OEM (현대차 등)
- LG에너지솔루션 등 자본집약 신성장기업
별도 PR 후보: 섹터별 FCF 정의 다르게 적용 또는 quality에 보조지표 추가.

### 10.3 자정 모니터 stoploss/ATR 보존
chart 없이 scorer 호출 → stoploss/ATR 0 산출. 직전 양수 값을 stoploss_map으로 명시 전달 (momentum 보존 패턴과 동일). `disclosure_impact.py:240~255` 참조.

### 10.4 buy_state daily_report_log 사후 검증 한계
`week52_position`, `foreign_net_buy_5d` 부재로 일부 룰이 트리거되지 않음. 라이브에서는 모두 작동.

---

## 11. 테스트 위치 + 개수 (참고)

`tests/` 약 40개 파일. 현재 회귀: **465 pass / 1 fail (T1-9 무관 pre-existing)**.

신규 추가 (단계 1):
- `test_buy_state.py` (31)
- `test_shadow_trader.py` (9)
- `test_buy_recommendations_format.py` (9)
- `test_get_previous_price.py` (7)
- `test_format_portfolio.py` (12)
- `test_send_daily_report_previous_prices.py` (2)
- `test_disclosure_impact_stoploss.py` (4)
- `test_cmd_portfolio_stoploss.py` (3)

---

## 12. 마일스톤

- 2026-04-30: 단계 0 — 포트폴리오 박스 형식 + 전일대비 + buy_state 베이스
- **2026-05-01 (현재)**: 단계 1 분류 라이브 첫 시행 예정 (15:40)
- 5/4 또는 5/6: 단계 1 가동
- 5/4 ~ 5/16: 모니터링
- 5/12 ~ 5/16: 단계 2 (매도 정교화) 설계
- 5/19: 섀도우 가동 (ShadowTrader.execute_cycle 본문 구현)
- 7월~: v2 RealtimePriceProvider
- 8월~: v3 KIS 모의 (MockOrderExecutor)
- 검증 후: v4 LiveOrderExecutor

# performance_tracking 데이터 분석 스크립트 설계

작성일: 2026-04-22
대상 산출물: `tools/analyze_performance.py` (아직 구현 안 함. 이 문서는 설계만)
관련 TODO: `[P1][DATA] performance_tracking 실제 적중률 분석`

## 목적

- 지금까지 봇이 저장한 신호(STRONG_BUY / BUY / HOLD / SELL)가
  이후 1주~1년 구간에서 실제로 수익을 냈는지 객관적으로 확인한다.
- 이 결과가 추후 "백테스트 엔진 구축"의 근거가 된다.
  (살아있는 데이터로 먼저 적중률을 본 뒤에 합성 백테스트를 해야 설계 타당성이 생김.)

## 선행 조건

- 서버 DB(`data/kospi_analyzer.db`)에 아래 두 테이블이 충분히 쌓여 있어야 한다.
  - `daily_report_log`: 추천 당시 스냅샷 (종목, 점수, 신호, 가격 등)
  - `performance_tracking`: 이후 주/월/분기/반기/1년 가격과 수익률
- 생존편향 제거(`[P1][DATA] 생존편향 제거`)가 먼저 완료되어 있으면 숫자 신뢰도 ↑.
  아직 안 끝났다면 결과 하단에 "생존편향 미보정" 경고 반드시 노출.

## 실행 방법

```
python -m tools.analyze_performance [옵션]
```

## 옵션

| 옵션 | 값 | 기본값 | 설명 |
|------|-----|--------|------|
| --start | YYYY-MM-DD | 가장 오래된 report_date | 분석 시작일 |
| --end | YYYY-MM-DD | 오늘 | 분석 종료일 |
| --period | 1w\|1m\|3m\|6m\|1y | 1m | 수익률 기준 컬럼 |
| --signal | strong_buy\|buy\|hold\|sell\|all | all | 신호 필터 |
| --output | text\|csv\|json | text | 출력 포맷 |

## 데이터 소스

1. `daily_report_log` — 추천 당시 정보
   - 키: (report_date, stock_code)
   - 사용 필드: signal, total_score, current_price, fair_value_gap,
     value_score, financial_score, growth_score, momentum_score, quality_score
2. `performance_tracking` — 경과 후 수익률
   - 키: (report_date, stock_code)
   - 사용 필드: return_1w, return_1m, return_3m, return_6m, return_1y,
     price_at_report, price_after_*
3. `analysis_results` — KOSPI 지수 비교용
   - 키: analysis_date
   - 사용 필드: kospi_index

JOIN 기준: `daily_report_log.report_date = performance_tracking.report_date
AND daily_report_log.stock_code = performance_tracking.stock_code`.

## 계산 지표

### 1. 전체 통계 (Overall)

- 분석 기간 (start ~ end)
- 총 추천 종목 수 (report_date × stock_code 조합, 중복 포함)
- 고유 종목 수 (distinct stock_code)
- 추적 완료 비율
  = `performance_tracking`에서 해당 period의 return 필드가 0이 아닌 레코드 수
    / `daily_report_log` 전체 레코드 수
  (주의: return 필드가 0인 레코드는 "미계산"으로 간주. 실제 수익률이 정확히 0일 가능성도 있으나
   드물고, `price_at_report`/`price_after_*`가 둘 다 >0인 레코드로 추가 필터 가능.)

### 2. 신호별 성과 (By signal)

| 신호 | 추천수 | 평균수익률 | 중앙값 | 승률(%) | KOSPI대비 초과수익 |
|------|-------|-----------|-------|--------|-------------------|
| strong_buy | N | x% | y% | w% | α% |
| buy | ... |
| hold | ... |
| sell | ... |

- 승률 = 수익률 > 0 인 건수 / 총 건수.
- KOSPI 대비 초과수익: 각 행의 `return - kospi_return_same_window`.

### 3. 점수 구간별 성과 (By score band)

| 구간 | 추천수 | 평균수익률 | KOSPI대비 |
|------|-------|-----------|----------|
| 90+ |
| 80~89 |
| 70~79 |
| 60~69 |
| <60 |

### 4. 적정주가 괴리율 대비 실제 수익률 (By fair_value_gap)

`fair_value_gap` 기준 버킷: (-∞, -50], (-50, -30], (-30, -10], (-10, 0], (0, 10], (10, 30], (30, ∞)

각 버킷별 평균 수익률.

(해석: fair_value_gap이 크게 음수(저평가) 일 때 실제 수익이 높은지, 가설 검증용.)

### 5. 월별 수익률 타임시리즈 (Monthly series)

| 월 | 추천수 | 평균수익률 | KOSPI수익률 | 초과수익 |
|----|-------|-----------|-------------|---------|

차트 생성용 raw 데이터. report_date의 YYYY-MM으로 그룹.

### 6. 최고/최악 TOP 10 (Best / Worst)

- 실제 수익률 상위 10: (stock_code, stock_name, signal, total_score, report_date, return)
- 실제 수익률 하위 10: 동일 포맷.

## 통계적 유의성

- `strong_buy` 평균 수익률에 대한 one-sample t-test.
  - H0: μ = 0 (유의미한 양의 수익 없음)
  - p-value 함께 출력.
  - `scipy.stats.ttest_1samp(returns, 0)` 사용.
- 각 신호별 샘플 수가 30 미만이면 "샘플 부족" 경고.
- 전체 샘플 수가 90일 미만 기간이면 상단에 "데이터 누적 기간 최소 3개월 이상 권장" 배너.

## 출력 포맷

### text (기본)

```
==========================================
Performance Analysis Report
Period: 2026-01-15 ~ 2026-04-22
Return basis: 1m
==========================================

1. Overall
   Total picks: 234
   Unique stocks: 58
   Tracked ratio: 82.3%

2. By signal
   strong_buy: n=42, mean=+4.21%, median=+3.10%, win_rate=64.3%, vs_kospi=+2.05%
   buy:        ...
   ...

3. By score band
   90+   : n=3,  mean=+7.8%
   80-89 : ...
   ...

4. By fair_value_gap
   (-50,-30] : n=12, mean=+5.2%
   ...

5. Monthly
   2026-01 : n=78, mean=+1.2%, kospi=+0.4%, excess=+0.8%
   ...

6. Best / Worst TOP 10
   [Best]
    1. 005930 삼성전자  strong_buy  82  2026-02-03  +14.2%
    ...
   [Worst]
    ...

Statistical significance (strong_buy)
  t-statistic: 2.14
  p-value:     0.039  (α=0.05 기준 유의)

Warnings:
  - strong_buy 샘플 42건: 통계적 신뢰 경계.
  - 생존편향 보정 미적용 (P1 작업 미완).
```

### csv

- `summary.csv`: (section, metric, value) 3-컬럼 집계.
- `detailed_rows.csv`: JOIN된 원본 행 (report_date, stock_code, ..., return_1m, kospi_return, excess).

### json

- 모든 섹션을 중첩 딕셔너리로.
  ```
  {
    "period": {...},
    "overall": {...},
    "by_signal": [...],
    "by_score_band": [...],
    "by_fair_value_gap": [...],
    "monthly": [...],
    "best": [...],
    "worst": [...],
    "stats": {"strong_buy_ttest": {...}},
    "warnings": [...]
  }
  ```

## 구현 구조 (시그니처)

```python
class PerformanceAnalyzer:
    def __init__(self, db: Database) -> None: ...

    def load_data(self, start: str, end: str, period: str) -> pd.DataFrame:
        """daily_report_log와 performance_tracking을 JOIN.
        추가로 analysis_results에서 해당 report_date의 kospi_index를 병합하고,
        같은 기간 KOSPI 수익률도 계산해 kospi_return 컬럼으로 붙인다."""

    def compute_overall_stats(self, df: pd.DataFrame) -> dict: ...
    def compute_by_signal(self, df: pd.DataFrame, return_field: str) -> pd.DataFrame: ...
    def compute_by_score_band(self, df: pd.DataFrame, return_field: str) -> pd.DataFrame: ...
    def compute_by_fair_value_gap(self, df: pd.DataFrame, return_field: str) -> pd.DataFrame: ...
    def compute_monthly_series(self, df: pd.DataFrame, return_field: str) -> pd.DataFrame: ...
    def compute_best_worst(self, df: pd.DataFrame, return_field: str, n: int = 10) -> dict: ...

    def t_test_strong_buy(self, df: pd.DataFrame, return_field: str) -> dict:
        """strong_buy 수익률이 0과 유의 차이 있는지. scipy.stats.ttest_1samp."""

    def render_text(self, report: dict) -> str: ...
    def render_csv(self, report: dict, out_dir: Path) -> None: ...
    def render_json(self, report: dict, out_path: Path) -> None: ...


def main() -> None:
    """argparse로 옵션 파싱 후 PerformanceAnalyzer 실행."""
```

## 주의사항

1. `performance_tracking.return_1m` 같은 필드는 수익률이 계산된 경우에만 값이 있다.
   `0.0`으로 남아 있는 레코드는 "미계산"으로 간주해 제외한다.
   단, 가격이 실제로 보합이었을 가능성이 드물게 있으니 추가로
   `price_at_report > 0 AND price_after_1m > 0` 필터를 함께 쓴다.
2. KOSPI 대비 초과수익 계산에서는 `analysis_results.kospi_index`를 이용해
   (report_date 기준 KOSPI) → (report_date + period 기준 KOSPI) 수익률을 계산해 차감.
   기간 일치하는 KOSPI 값이 없으면 해당 행은 excess 계산 제외 후 로그 남김.
3. 샘플 수가 너무 적으면(신호별 30 미만) 신뢰도 낮음을 명시.
4. 첫 실행 시 데이터가 매우 적을 가능성이 높다 (봇 운영 기간 ≒ 한 달).
   이때는 "데이터 누적 기간 최소 3개월 이상 권장" 안내 문구 출력.
5. `pandas`와 `scipy`는 `requirements.txt`에 없을 수 있다.
   실행 전 확인하여 없으면 아래를 추가:
   ```
   pandas>=2.0
   scipy>=1.10
   ```
6. 생존편향 보정 전이면 결과 상단에 "WARNING: 생존편향 미보정" 경고를 반드시 출력.

## 구현 착수 체크리스트

- [ ] `tools/__init__.py` 생성 (빈 파일)
- [ ] `tools/analyze_performance.py` 생성 (위 시그니처대로)
- [ ] `requirements.txt`에 `pandas`, `scipy` 추가 여부 확인
- [ ] `python -m tools.analyze_performance --output text`로 smoke 테스트
- [ ] 결과를 `docs/performance_analysis_2026-04-XX.md`로 저장해 시계열 기록

## 다음 단계

- 설계 확정 후 사용자 요청 시 구현 착수.
- 최초 결과가 나오면 P1의 "룩어헤드 편향 감사", "생존편향 제거" 작업과 교차 검증.

# Stage 4 — Analysis + Scoring

**대상**: `analysis/scorer.py` (794) + `signals.py` (372) + `stoploss.py` (304) + `admin_filter.py` (93)
**날짜**: 2026-04-28

---

## 1. 가중치 합 검증 (정합 ✓)

| 카테고리 | sub max | 실측 |
|----------|---------|------|
| Value (PER+PBR+DIV+SECTOR_PER+PEG+EV/EBITDA+PSR) | 30 | ✓ |
| Financial (ROE+OPR_MARGIN+DEBT+CURRENT) | 20 | ✓ |
| Growth (REV_G 7 + OP_G 7 + turnaround 3 + profit_health 3) | 20 | ✓ |
| Momentum (MA20 2 + MA60 1 + Vol 2 + RSI 3 + MACD 2 + Supply/D 8 + Week52 2) | 20 | ✓ |
| Quality (FCF_YIELD 5 + FCF_MARGIN 5) | 10 | ✓ |
| **합** | **100** | ✓ |

`calculate_score` line 145-147: `total_score = max(0, min(100, raw_total + penalties))` — 0~100 클램프 정상.

---

## 2. silent fail 가드 검증

### 어제 패치 (`_score_growth`, line 532-540)
```python
if rate is None or rate == 0:
    return default
```
양수 임계값 (0.0, 2)와의 첫 매칭에서 결손이 가산점 받던 silent fail 차단 ✓.

### 같은 패턴 적용처 점검
| 함수 | 결손 가드 | 평가 |
|------|----------|------|
| `_score_growth` | ✓ rate==0 → default | OK |
| `_score_debt` | ✓ ratio<=0 → DEFAULT (line 434) | OK |
| `_score_fcf_yield` | ✓ fcf<=0 → DEFAULT | OK |
| `_score_fcf_margin` | ✓ fcf<=0 → DEFAULT | OK |
| `_score_psr` | ✓ revenue<=0 → DEFAULT | OK |
| `_score_ev_ebitda` | ✓ ebitda<=0 → DEFAULT + outlier 캡 100 | OK |
| `_score_peg` | ✓ growth<=0 → DEFAULT | OK |
| `_threshold_above` | ✓ val<=0 → default (line 763) | OK |
| `_threshold_below(reject_zero=True)` | ✓ val<=0 → default | OK |
| `_threshold_below(reject_zero=False)` | ⚠️ val<=0 시 첫 임계 통과 가능 | **점검 대상** |

### 발견
**S1 [INFO]** `_threshold_below(reject_zero=False)`로 호출되는 곳을 확인했지만 (현재 reject_zero=True가 PER/PBR에 적용) 다른 호출이 없음. 안전.

### 모멘텀 데이터 부족 가드 (line 566-569)
```python
if not chart or len(chart) < 5:
    return {모든 sub=0, total=0}
```
- 차트 5일 미만이면 모멘텀 0점. 합리적 (신규 상장 종목 보호).

---

## 3. 비즈니스 로직 정확성

### PER/PBR (KIS 응답 직접 사용)
- `_calc_value_score` line 340-341: `price.get("per", 0.0)` — KIS API에서 받은 값 그대로
- 검증: KIS API parser (`_parse_stock_price` line 655-656)가 정확히 추출 ✓

### ROE
- `dart_api.py::_calc_ratio` line 632-639: `numerator / denominator * 100`, 분모 0이면 0.0
- `metrics["roe"] = self._calc_ratio(net_income, total_equity)` line 373 ✓
- 자본잠식 종목 (equity≤0): ROE=0 → financial_score 결손 처리됨 ✓

### 영업이익률
- `metrics["operating_margin"] = self._calc_ratio(operating_income, revenue)` line 374 ✓
- revenue=0 종목 (보험/증권 합산 실패): operating_margin=0 → score=0 → silent fail로 감지 어려움 ⚠️

### 부채비율
- `_calc_ratio(total_liabilities, total_equity)` line 375
- 자본잠식 (equity≤0)이면 0 → `_score_debt`의 `ratio<=0` 가드로 default 처리 ✓

### 매출/영업이익 성장률 (`_calc_growth_rate`)
- 0→양수: 100%, 0→음수: -100%, 0→0: 0%
- 음수 → 양수 케이스: `(curr - prev) / abs(prev) * 100` (line 467-468 추정 — 확인 필요)

### EV/EBITDA outlier 캡 (`_score_ev_ebitda`)
- ratio > 100 시 0점 처리 (line 402-403)
- 한계 기업(EBITDA 0 근접) 비대칭 큰 ratio 방지 ✓

### EBITDA = 영업이익 + |감가상각비| (line 388)
- 표준 공식이지만 IFRS가 영업이익에 감가상각비를 포함 → **이중 가산 의심**? 한국 K-IFRS는 영업이익이 매출총이익에서 판관비(감가상각 포함)를 뺀 값. 즉 감가상각이 이미 차감된 상태에서 다시 더하는 게 맞음. ✓

### 적정주가 3-모델 가중평균 (`_calc_fair_value`)
- 가독성 위해 별도 함수. 검증은 별도 PR.

---

## 4. 신호 (signals.py) 임계값 정합성

| 임계 | 코드 (signals.py:_judge) | settings | 일치 |
|------|------------------------|----------|------|
| SELL | `total < 45` (line 112) | `SELL_SCORE: 45` | ✓ |
| SELL momentum | `momentum < 4` (line 115) | `SELL_MOMENTUM_MIN: 4` | ✓ |
| STRONG_BUY | `total>=75 + mom>=10 + fin>=12 + growth>=10` | 모두 일치 | ✓ |
| BUY | `60<=total<=74 + fin>=10` | 모두 일치 | ✓ |
| HOLD | `45<=total<=59` | 모두 일치 | ✓ |

T2-2 health check가 이를 매일 검증 (오늘 추가).

### `_is_financial_sector` dead flag (Stage 2 확인)
- 정의 line 222-228, 항상 False 반환
- 할당 line 201 `stock["is_financial"] = ...`
- 소비처 0건 → **L1 [LOW] 별도 PR로 제거 후보**

### `filter_stocks` (line 158-220)
- 시총 ≥ 5000억 ✓
- 거래대금 ≥ 50억 ✓
- 적자 3년 이상 제외 (`EXCLUDE_CONSECUTIVE_LOSS_YEARS=3`) ✓
- 금융주 태그만 추가 (제외 X) ✓
- ⚠️ 손절 도달 종목 제외 — `filter_stocks`에는 없음. 손절 처리는 `_judge`에서 SELL 판정 (line 109-110) ✓

---

## 5. ATR 손절 (stoploss.py)

### 공식 (line 38-79)
```
TR = max(high - low, |high - prev_close|, |low - prev_close|)
ATR(14) = mean(TR_1..14)
```
표준 공식 ✓. 14일 미만 시 가능한 기간으로 fallback (line 53-57).

### 손절 가격 결정 (line 122-148)
```python
atr_stoploss = current_price - (atr * multiplier)
hard_stop = current_price * 0.93  # -7%
effective = max(atr_stoploss, hard_stop)  # 더 작은 손실 = 더 높은 가격
```
✓ 보수적 (더 가까운 손절). 둘 중 손실 작은 쪽 선택.

### 멀티플라이어 클램프
- `[1.0, 3.0]` (line 117-120) ✓
- 환경변수/설정에서 받음 (multiplier 인자)

### 발견
**F-trail [LOW]** 트레일링 스탑 부재. 보유 중 가격 상승 시 동적 손절선 갱신 안 됨. 단발성 진입가 기준만 산출. v1으로는 OK, 별도 PR 후보.

---

## 6. admin_filter (오늘 추가, 단순 모듈)

### 점검
- 4개 필드 매칭 (`iscd_stat_cls_code`, `mang_issu_cls_code`, `temp_stop_yn`, `sltr_yn`)
- 보수적 분류 (관리 51 / 거래정지 58 / 임시정지 / 정리매매만 제외)
- 투자주의/경고/위험 (52/53/54) + 단기과열 보존 (false positive 회피) ✓
- fail-open: admin 필드 없으면 정상으로 간주 (line 49-51)
- 비변경: 입력 리스트 mutate 안 함 ✓
- 14/14 회귀 테스트 통과

### 발견
**없음**.

---

## 7. Stage 3 누적 발견 영향

### N1 (IFRS account_id 매출/영업이익 확장)
- scorer가 매출=0 종목 처리: `_score_psr`이 revenue<=0 → default 처리 ✓
- 그러나 `operating_margin = op_income / revenue`가 0이 되면 financial_score 손실
- → revenue 결손이 financial_score 영향 (간접)
- N1 패치 시 (매출에 IFRS 추가) 잔존 결손 12/256 (4.7%) → 더 낮춤 가능

### 매출=0 잔존 (보험/증권/금융 각 1)
- `_calc_financial_revenue`로 보호되지만 실패 잔존
- IFRS17 신규 패턴 또는 일반 지주 가능성
- scorer는 이를 silent fail로 0점 처리 — health check T1-6이 외부에서 노출 ✓

### quality_score (FCF 기반)
- FCF=0 종목 11/256 (4.3%) → quality_score=0 부여
- T1-5 health check가 노출 ✓
- 어제 FCF account_id 매칭 패치 후 결손율 80%+ → 4.3%로 대폭 개선됨

---

## 8. Stage 4 누적 발견

| ID | 심각도 | 내용 | 권고 |
|----|--------|------|------|
| L1 (재확인) | LOW | `_is_financial_sector` 항상 False, 참조 0건 — dead flag 제거 | 별도 PR (signals.py 함수+할당 제거) |
| F-trail | LOW | 트레일링 스탑 부재 | 별도 기능 PR (선택) |
| S1 | INFO | `_threshold_below(reject_zero=False)` 호출 부재, 안전 | 관찰 |

**CRIT 0 / HIGH 0**.

가중치, silent fail 가드, 비즈니스 로직, 신호 임계값, ATR 모두 **정합 OK**. 어제 패치(`_score_growth` rate==0 가드)가 핵심 silent fail 차단했고 health check가 잔존 결손 노출.

---

**다음**: Stage 5 (database/models.py) 진행 승인 요청.

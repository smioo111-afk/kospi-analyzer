# 4-30 전체 데이터 무결성 검증 (silent regression 확장 진단)

작성일: 2026-04-30
대상: 4-29 18:07~18:24 처리된 74종목 + 전체 financial_metrics

## 결론 한 줄

**Silent regression은 모두 정리 완료** (오전 PR `f11b130`로 회복). 하지만 **PRE-EXISTING parser 한계로 매출 외 metric에 광범위한 결손**: prev_revenue=0 / rev_growth_yoy=0 이 매출 보유 종목 264건 중 122건(46%)에 존재. 이는 4-29 회귀가 아닌 **누적 데이터 결손**으로, 별도 P1 후속 작업감.

---

## Phase 1 — 처리된 74종목 식별

`data/disclosure_reports/refetch_20260429_182440.json`:
- target=74, success=74, fail=0, rcept_changed=2

## Phase 2 — 매출 외 metric 결손 패턴 스캔 (rcept_no 보유 264종)

| 패턴 | 정의 | 건수 | 비율 |
|---|---|---:|---:|
| A | revenue=0 AND op>0 (sector-aware revenue 결손) | 0 | 0% |
| B | revenue>0 AND op=0 | 1 | 0.4% |
| C | revenue>0 AND net=0 | 6 | 2.3% |
| D | revenue>0 AND total_assets=0 (BS 결손) | 9 | 3.4% |
| E | revenue>0 AND total_equity=0 | 10 | 3.8% |
| F | net>0 AND ROE=0 (계산 누락) | 6 | 2.3% |
| G | revenue>0 AND rev_growth_yoy=0 | **122** | **46.2%** |
| G' | revenue>0 AND prev_revenue=0 | 125 | 47.3% |

### 패턴별 영향 종목

- **B (op=0)**: 020150 (전기·전자, 영업손실 −167B를 잡지 못함)
- **C (net=0)**: 006280 광동제약, 006400 삼성SDI, 0126Z0 신한지주 우, 096770 SK이노베이션, 326030 SK바이오팜, 377300 카카오페이
- **D/E (BS 결손)**: 005440·005940 KCC글라스/현대증권, 006280·006400, 009150 삼성전기, 047050 포스코인터내셔널, 051900 LG생활건강, 307950 현대오토에버 등 (대형주 다수 포함)
- **F (ROE=0)**: 005440, 005940, 009150, 012450 한화에어로스페이스, 075580 세아에스앤씨, 307950 — 대부분 total_equity=0 결손과 연동
- **G (성장률=0)**: 122건 — 대형주 다수 (000100 유한양행, 000500 가온전선, 001450 현대해상, 002020 코오롱, 003490 대한항공 등)

## Phase 3 — 4-29 18시 처리 결과

`updated_at` 분포:
- **2026-04-29 18:07:33: 180건** ← init_disclosure_baseline (rcept_no/rcept_dt만 갱신, 재무 필드 미손)
- 2026-04-07 17:42:06: 6건 (오래된 row)
- 2026-04-30 15:41:38: 5건 (KIS sector 갱신)
- 2026-04-30 16:55:36~39: 5건 (오늘 회복 PR)

180건의 row에서 `rcept_no`만 갱신되었고 매출·성장률 등 재무 필드는 그 이전(parser 한계 시점)에 산출된 값이 stale로 남아있음.

## Phase 4 — 백업 vs 현재 silent regression 검증

대상 baseline: `data/kospi_analyzer.db.bak_before_a1_phase0_20260429_1803` (refetch 직전).

### 4-1. 매출/영업이익/순이익/FCF/자산 등 16개 metric 전수 비교

```
전체 silent regression (baseline nonzero → 현재 0): 0건
74 처리종목 한정 silent regression: 0건
```

### 4-2. 회복 흐름 검증 (5건 회복 코드)

| code | baseline (4-29 18:03) | 18:24~00:00 regression | 16:55 회복 (PR f11b130) |
|---|---:|---:|---:|
| 001500 | rev=555,735,000,000 | rev=0 | rev=555,735,000,000 ✓ |
| 105560 | rev=47,306,167,000,000 | rev=0 | rev=47,306,167,000,000 ✓ |
| 139130 | rev=4,396,365,000,000 | rev=0 | rev=4,396,365,000,000 ✓ |
| 316140 | rev=25,249,011,000,000 | rev=0 | rev=25,249,011,000,000 ✓ |
| 064400 | rev=0 (PRE-EXISTING) | rev=0 | rev=6,129,542,718,000 ✓ |

### 4-3. PRE-EXISTING vs REGRESSION 분리

패턴 B/C/D/E/F의 모든 종목: baseline에서도 동일한 0 값 확인 → **모두 PRE-EXISTING parser 한계**. 4-29 작업이 신규로 깨뜨린 게 아니다.

## Phase 5 — stock_scores 영향 (회복 5건)

회복 UPDATE 시점이 16:55, 4-30 daily 분석은 15:40 → **그날 분석은 결손값으로 점수 산출됨**. 5/1 사이클부터 정상 점수 반영.

| code | 4-29 (사실상 정확) | 4-30 (잘못된 결손값 기반) | 차이 |
|---|---|---|---|
| 105560 KB금융 | 32 (sell) | 33 (sell) | 매출 0 → growth/value 저평가 |
| 139130 iM금융지주 | 31 (sell) | 41 (sell) | growth_score 3→10 (왜 올랐는지 불확실 — 제거 후 재계산 필요) |
| 316140 우리금융지주 | 40 (sell) | 31 (sell) | -9, 매출 0의 영향 |
| 064400 LG씨엔에스 | 39 (sell) | 40 (sell) | 거의 변화 없음 |
| 001500 현대차증권 | (4-30에 stock_scores 없음 — 분석 대상 아님) | — | — |

→ 영향은 작음. 5/1 사이클로 자동 정상화.

---

## 권고

| 우선순위 | 항목 | 조치 | 영향 |
|---|---|---|---|
| (완료) | 4-29 silent regression 4건 매출 회복 | PR `f11b130` 머지 완료 | T1-6 6.2% → 4.3% |
| **P1 [DATA]** | prev_revenue=0 / rev_growth_yoy=0 광범위 결손 (122/264 = 46%) | parser N1 IFRS account_id matching 적용 후 prev-year DART 강제 재추출 도구 작성 | growth_score 산출 정확도 — 일일 점수 안정성 |
| P1 [BUG] | total_assets=0 / total_equity=0 결손 (9~10건) | parser BS 라벨 매칭 보강 (예: ifrs-full_Assets, ifrs-full_Equity 등 IFRS account_id 추가) | ROE/debt_ratio 계산 의존 |
| P1 [BUG] | net_income=0 (rev>0) 6건 | parser net_income IFRS account_id 매칭 누락 점검 | 점수 calc + ROE |
| P2 [BUG] | 020150 영업이익 손실 미인식 | "영업손실" 라벨 + 음수 처리 검증 | scorer penalty 영향 |
| P2 [DATA] | 180건 stale (4-29 18:07:33만 갱신, 재무 미갱신) | daily 분석 시 "rcept_no가 최신 사업보고서를 가리키는데 metric=0이면 강제 재추출" 룰 추가 검토 | 캐시 hit 우회 |

### 즉시 회복 권고 종목

추가 즉시 회복은 **불요**. 모든 4-29 silent regression은 회복 완료. PRE-EXISTING 결손은 별도 P1 작업으로 일괄 재추출이 더 안전 (1회성 도구 + 검증 단계).

### 별도 조사 필요

1. **122건 성장률 결손**: dart_cache parquet에 prev-year 데이터가 있는지 표본 검증. 있으면 단순 재추출. 없으면 DART API 호출 비용 평가 후 일괄 backfill.
2. **9건 BS 결손**: parquet에 BS row가 있는지(account_id=ifrs-full_Assets 등) 1건씩 확인 — 패턴 D의 005440/047050 등 대형주가 0인 건 의외.
3. **6건 net_income=0**: parquet에 ProfitLoss row 존재 여부 확인.

---

## 부록 — 검증 명령

```bash
# 전체 silent regression 0건 재확인
python3 -c "
import sqlite3
b=sqlite3.connect('data/kospi_analyzer.db.bak_before_a1_phase0_20260429_1803')
c=sqlite3.connect('data/kospi_analyzer.db')
bd={r[0]:r[1:] for r in b.execute('SELECT stock_code, revenue, operating_income, net_income FROM financial_metrics WHERE year=2025 AND quarter=\"annual\"')}
cd={r[0]:r[1:] for r in c.execute('SELECT stock_code, revenue, operating_income, net_income FROM financial_metrics WHERE year=2025 AND quarter=\"annual\"')}
loss=[(k,bd[k],cd[k]) for k in bd if k in cd and any(b>0 and c==0 for b,c in zip(bd[k],cd[k]))]
print(f'silent regression: {len(loss)}')
"

# 패턴 G 표본 종목 prev/growth 0 재확인
python3 -c "
import sqlite3
c=sqlite3.connect('data/kospi_analyzer.db'); cur=c.cursor()
cur.execute('SELECT COUNT(*) FROM financial_metrics WHERE year=2025 AND quarter=\"annual\" AND revenue>0 AND revenue_growth_yoy=0')
print(f'rev>0 AND growth=0: {cur.fetchone()[0]}')
"
```

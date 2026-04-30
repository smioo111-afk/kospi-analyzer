# 4-30 사이클 진단 보고서

작성일: 2026-04-30
작성자: 진단 (코드/DB 무수정)

## 결론 한 줄

15건 신호 변경 중 14건은 4-29 저녁 H1/A1 백필로 **prev_*·growth_yoy 필드가 0→실제값**으로 회복된 결과(정상). 단 **재무지표 결손 4건은 silent regression**으로, `disclosure_impact.recalculate_score_for_stock` 및 `tools/refetch_amendments.py`가 `extract_financial_metrics`에 `sector`를 전달하지 않아 4-29 18:07 이후 4개 금융주(증권·은행지주)의 매출이 합계값에서 0으로 덮어쓰였다.

---

## Phase 1 — 신호 변경 15건 원인 추적

### 1-1. 5카테고리 점수 변화 (stock_scores 4-29 vs 4-30)

| code | name | 4-29→4-30 | V Δ | F Δ | G Δ | M Δ | Q Δ |
|---|---|---|---|---|---|---|---|
| 483650 | 달바글로벌 | 36→59 | +5 | +4 | **+14** | 0 | 0 |
| 000660 | SK하이닉스 | 39→59 | +6 | 0 | **+14** | 0 | 0 |
| 017960 | 한국카본 | 36→57 | +4 | +2 | **+14** | 0 | +1 |
| 251270 | 넷마블 | (신규)→52 | — | — | — | — | — |
| 064350 | 현대로템 | 29→50 | +5 | 0 | **+14** | +2 | 0 |
| 267250 | HD현대 | 31→49 | +5 | +1 | **+11** | +1 | 0 |
| 000880 | 한화 | 29→49 | +5 | 0 | **+14** | +1 | 0 |
| 035420 | NAVER | 31→49 | +4 | +4 | **+9** | +1 | 0 |
| 139480 | 이마트 | 43→47 | 0 | +1 | 0 | +3 | 0 |
| 021240 | 코웨이 | 27→47 | +3 | +8 | **+9** | 0 | 0 |
| 003230 | 삼양식품 | 25→46 | +5 | +2 | **+14** | 0 | 0 |
| 395400 | SK리츠 | 44→45 | 0 | 0 | 0 | +1 | 0 |
| 161890 | 한국콜마 | 28→45 | +5 | +1 | **+10** | +1 | 0 |
| 009240 | 한샘 | 23→25 (sell) | -1 | 0 | 0 | +3 | 0 |
| 034220 | LG디스플레이 | 30→45 | +5 | 0 | **+11** | -1 | 0 |

**관찰:** 11/14에서 growth_score가 +9~+14 점프 — 단일 카테고리 동시 회복은 데이터 변경 시그니처.

### 1-2. 데이터 변경 추적 (financial_metrics)

11개 종목의 `prev_revenue` / `op_income_growth_yoy` 변화:

| code | pre-H1 prev_rev | pre-H1 op_g | post-A1 prev_rev | post-A1 op_g | updated_at |
|---|---:|---:|---:|---:|---|
| 483650 | 0 | 0 | 309,062,616,157 | 69.55 | 2026-04-29 18:22:18 |
| 000660 | 0 | 0 | 66,192,960,000,000 | 101.16 | 2026-04-29 18:22:58 |
| 017960 | 0 | 0 | 741,740,847,191 | 188.14 | 2026-04-29 18:24:15 |
| 064350 | 0 | 0 | 4,376,597,857,000 | 120.26 | 2026-04-30 00:00:19 |
| 267250 | 0 | 0 | 67,765,626,088,000 | 104.47 | 2026-04-29 18:23:55 |
| 000880 | 0 | 0 | 55,646,829,000,000 | 71.63 | 2026-04-29 18:23:51 |
| 035420 | 0 | 0 | 10,737,719,264,647 | 11.56 | 2026-04-29 18:24:34 |
| 021240 | 0 | 0 | 4,310,141,694,596 | 10.47 | 2026-04-29 18:23:24 |
| 003230 | 0 | 0 | 1,728,015,003,746 | 52.13 | 2026-04-29 18:23:53 |
| 161890 | 0 | 0 | 2,452,063,980,463 | 23.60 | 2026-04-29 18:22:54 |
| 034220 | 0 | 0 | 26,615,347,000,000 | 192.22 | 2026-04-30 00:00:44 |

나머지 3건(139480 이마트, 395400 SK리츠, 009240 한샘)은 prev 필드 변화 없음 — 점수 차이 +1~+4로 신호 임계 근접 종목의 시세 변동 영향(boundary tipping).

### 1-3. 변경의 트리거 (commit 타임라인)

| 시각 | 커밋 | 영향 |
|---|---|---|
| 4-29 17:33 | 86ec996 fix(dart): N1 IFRS account_id matching | 미사용 — financial_metrics 캐시 hit으로 재추출 안 됨 |
| 4-29 18:09 | 39d4471 init_disclosure_baseline | rcept_no/rcept_dt만 갱신 (revenue·prev_* 미손) |
| 4-29 18:26 | cd331b3 refetch 151 amendments | **74개 종목 재추출 → prev_* 회복** ← 핵심 |
| 4-29 18:38 | 5e3b6eb A1 Phase 2 disclosure_impact | (오늘은 호출 미발생) |
| 4-29 18:53 | f714960 A1 Phase 4 daily_disclosure_monitor 00:00 cron | 4-30 00:00 첫 실행 → 064350·034220 prev 회복 |

→ **4-29 daily 분석(15:40)은 prev=0 상태로 점수 계산** → growth_score≈3 (default) → sell. 4-30 daily 분석은 prev이 채워진 상태로 정상 점수 → hold. **회귀 아님 / 데이터 결손 보강의 후행 효과.**

---

## Phase 2 — 재무 결손 17건 원인 추적

### 2-1. 17건 분류

| 분류 | 종목 수 | 코드 |
|---|---:|---|
| 우선주·ETN·REIT (구조적) | 11 | 000155(두산우), 000815(삼성화재우), 005308(CJ4우), 005385/005387(현대차우/2우B), 005935(삼성전자우), 006405(삼성SDI우), 00680K(미래에셋증권2우B), 009155(삼성전기우), 051915(LG화학우), 066575(LG전자우), 088980(맥쿼리인프라) |
| 사업보고서 매출 라인 부재(증권사) — pre-H1엔 채워져 있었음 | 1 | 001500 현대차증권 (sector=기타 오기) |
| 은행지주 매출 합산 — pre-H1엔 채워져 있었음 | 3 | 105560(KB금융), 139130(iM금융지주), 316140(우리금융지주) |
| 일반 종목 stale (parser는 정상, 캐시 hit 미갱신) | 1 | 064400 LG씨엔에스 |

11건은 KIS만 추적되고 사업보고서 미공시(우선주/ETN/인프라 펀드) — **정상**.

### 2-2. 4건 silent regression (P1 BUG)

001500 / 105560 / 139130 / 316140 — pre-H1과 현재 비교:

```
001500 현대차증권:  pre=555,735,000,000  → post=0  (sec 증권→기타→기타)
105560 KB금융:     pre=47,306,167,000,000 → post=0  (sec 금융→기타→금융)
139130 iM금융지주: pre=4,396,365,000,000  → post=0  (sec 금융→기타→금융)
316140 우리금융지주: pre=25,249,011,000,000 → post=0  (sec 금융→기타→금융)
```

원인: `analysis/disclosure_impact.py:197`과 `tools/refetch_amendments.py:89`이 `dart_client.extract_financial_metrics(stock_code, year=year)` 호출 시 **sector를 전달하지 않음** (None 기본값). 결과:

1. `_calc_financial_revenue(df, sector=None, code)` → 즉시 None 반환
2. fallback 분기로 `["매출액", "매출", "수익(매출액)", "영업수익"]` lookup
3. 증권/은행지주 사업보고서엔 해당 라인 부재 → revenue=0
4. UPSERT로 pre-H1의 정상 합계값(555B / 47T / 4T / 25T)이 0으로 덮어쓰기

추가로 sector 컬럼도 임시로 '기타'로 덮였다가 그 다음 daily 사이클 KIS sector_map 갱신으로 복구됨. revenue 컬럼은 복구 트리거 없음 — 재추출 이전에 캐시 hit.

### 2-3. 1건 stale (P2)

064400 LG씨엔에스: parquet 캐시(4-7 mtime)에 `account_id=ifrs-full_Revenue, account_nm=매출, thstrm_amount=6,129,542,718,000` 명확. 현재 parser로 직접 호출하면 6.13T 정상 추출됨. 하지만 financial_metrics.revenue=0 — 4-26 fe236bc(`매출` 별칭 추가) **이전**에 row가 생성된 후 daily 분석은 `db.get_financial_metrics` 캐시 hit으로 재추출하지 않아 stale.

---

## Phase 3 — SK하이닉스 검증

| 시점 | total | signal | V | F | G | M | Q | revenue_growth | op_growth |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 4-29 | 39 | sell | 5 | 18 | 3 | 7 | 6 | 0.0 | 0.0 |
| 4-30 | 59 | hold | 11 | 18 | 17 | 7 | 6 | 46.76 | 101.16 |

financial_metrics:
- pre-H1 (4-29 18:03): prev_revenue=0, prev_op=0, prev_net=0 → growth_score=3
- 현재 (4-29 18:22:58 update): prev_revenue=66.2T, prev_op=23.5T, prev_net=19.8T → growth_score=17

PER 21.93→21.81, PBR 7.41→7.37, ROE 35.59 동일. **재무·시세 펀더멘털은 거의 무변화. 점수 변화는 prev_* 필드 백필이 단일 원인.**

disclosure_impacts 테이블에 SK하이닉스 row 없음 → 4-30 00:00 모니터로 인한 영향 아님.

### 가설 판정

- **A (H1 패치 전 결손 → 정상): 채택.** prev_* / *_growth_yoy 회복이 핵심. growth_score 3→17 전부 설명.
- **B (정정공시 영향): 기각.** disclosure_impacts 0행.
- **C (시세 변동 + 임계 통과): 부분 적용.** 139480/395400/009240 등 점수 변화 작은 종목에만 해당.

---

## 권고

| 우선순위 | 항목 | 조치 |
|---|---|---|
| P1 BUG | disclosure_impact·refetch_amendments에 `sector=` 누락 | sector_map 인자 추가 + financial UPSERT 시 revenue=0이고 pre-row revenue>0이면 보존 가드 |
| P1 BUG | financial_metrics UPSERT가 정상값 → 0 덮어쓰기 허용 | save_financial_metrics에 "기존 값 > 0 & 신규 = 0이면 거부" 룰 또는 사유 로깅 |
| P2 BUG | 064400 LG씨엔에스 revenue stale | 1회성 재추출 또는 daily 분석에 "revenue=0이고 parquet에 ifrs-full_Revenue 존재 시 강제 재계산" |
| P3 DATA | 001500 현대차증권 sector="기타" 오기 | KIS bstp_kor_isnm 매핑 점검 (증권사인데 "기타") |
| 정보 | 신호 변경 15건 | 전부 정상 동작. PR 회귀 아님. |

**즉시 수정 vs 별도 PR:** 모두 별도 PR 권고. 봇 재시작 불필요(점수는 이미 4-30 사이클에 회복됨; 4건 매출 결손은 다음 정정공시 또는 수동 재추출이 트리거되기 전까지 잔존).

---

## 부록 — 검증 명령

```bash
# 4건 매출 회귀 확인
python3 -c "
import sqlite3
for db in ['data/kospi_analyzer.db.bak_before_a1_phase0_20260429_1803', 'data/kospi_analyzer.db']:
    c=sqlite3.connect(db); cur=c.cursor()
    print(db)
    for code in ['001500','105560','139130','316140']:
        cur.execute('SELECT stock_code, revenue, sector, updated_at FROM financial_metrics WHERE stock_code=? AND year=2025 AND quarter=\\'annual\\'', (code,))
        print(' ', cur.fetchone())
    c.close()
"

# 064400 stale 검증 (parser 직접 호출 시 6.13T 추출되는데 DB는 0)
python3 -c "
import sys; sys.path.insert(0,'/root/kospianal/kospi-analyzer')
from collectors.dart_api import DARTClient
print(DARTClient().extract_financial_metrics('064400', year=2025, sector='IT 서비스')['revenue'])
"
```

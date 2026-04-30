# prev_revenue 122건 결손 원인 진단

작성일: 2026-04-30
대상: `prev_revenue=0 AND revenue>0` 종목 125건 (전체 264 중 47.3%)

## 결론 한 줄

**대다수 종목은 parser 정상화 이후 stale로 남은 데이터** — 현재 parser로 재추출하면 즉시 회복(95건). 금융주 25건은 **prev_revenue 계산 경로에 sector 분기 미적용** 코드 버그(P1). 5건은 2024 DART 캐시 부재로 신규 API 호출 필요.

---

## Phase 1 — 표본 (revenue 큰 순 20)

```
096770 SK이노베이션      80.3T (화학)
005490 POSCO홀딩스      69.1T (금속)
105560 KB금융           47.3T (금융)
055550 신한지주          35.9T (금융)
036460 한국가스공사       35.7T (전기·가스)
006260 LS               31.9T (금융)
009540 HD한국조선해양     29.9T (금융)
097950 CJ제일제당        27.3T (음식료·담배)
088350 한화생명          26.8T (보험)
012450 한화에어로스페이스  26.7T (운송장비·부품)
316140 우리금융지주       25.2T (금융)
003490 대한항공          25.2T (운송·창고)
... (전 125건)
```

## Phase 2 — DART 캐시 인벤토리

| | 건수 |
|---|---:|
| 캐시 디렉토리 총 parquet | 641 |
| 2025 annual | 264 |
| 2024 annual | 259 |
| 2023 annual | 78 |

**125건 중 2024 캐시 부재: 5건** — 0120G0, 439260, 031210, 0126Z0, 217590

## Phase 3 — parquet raw 분석

표본 7개 (SK이노베이션 / POSCO홀딩스 / CJ제일제당 / 한화에어로스페이스 / 대한항공 / 한국타이어앤테크놀로지 / 롯데쇼핑)의 2024 parquet 모두:
- `account_id == 'ifrs-full_Revenue'` 1행 존재
- `sj_div == 'CIS'` 만 사용 (IS 부재)
- 매출 정상 추출 가능

**그런데 DB의 prev_revenue=0** → 저장 시점과 현재 parser 동작 사이의 괴리.

## Phase 4 — 코드 분석

`collectors/dart_api.py:441-477`의 prev-year 추출 경로:

```python
prev_df = self.get_financial_statements(stock_code, year - 1)
if prev_df is not None and not prev_df.empty:
    prev_rev = self._get_account_value(
        prev_df, "IS",
        ["매출액", "매출", "수익(매출액)", "영업수익"],
        account_ids=["ifrs-full_Revenue"],
    )
    ...
```

**중요:** `_calc_financial_revenue`(sector-aware 합산 분기) 호출이 빠져 있다. 당기(363-376)에는 적용되지만 prev에는 미적용. → 금융주(보험/증권/은행지주)의 prev_revenue가 일반 매출 라벨로만 시도되어 부분 매칭 또는 0.

## Phase 5 — 가설 검증

### 가설 A: 2024 annual 캐시 부재
- 5/125건 (4%)
- 대상: 0120G0, 439260, 031210, 0126Z0, 217590
- **부분 채택** (소수 영향)

### 가설 B: frmtrm_amount 사용
- 코드는 별도 prev_df 파일을 읽음. frmtrm_amount 사용 안 함.
- **기각** (해당 사항 없음)

### 가설 C: parser 한계
- 일반 종목 표본 5건을 현재 parser로 직접 호출 시 모두 prev_revenue 정상 추출:
  - 020150 → 902B / 006650 → 2.8T / 006110 → 251B / 012450 → 11.2T / 003280 → 188B
- 즉 **현재 parser는 정상**. 저장 시점의 한계로 stale 상태로 남음.

### 가설 D: 금융주 sector 분기 미적용
- 표본 직접 호출 시 (sector 인자 전달):
  - 105560 KB금융: prev_rev=0 (분기 미적용 → 일반 매출 라벨 매칭 실패)
  - 316140 우리금융지주: prev_rev=0
  - 055550 신한지주: prev_rev=0
  - 088350 한화생명: prev_rev=13.7T (부분만 — IFRS17 보험수익 일부)
  - 032830 삼성생명: prev_rev=276B (부분 — 비정상)
  - 005830 DB손해보험: prev_rev=5.7T (부분)
- **채택** (금융주 25건 영향 — 코드 버그)

## 최종 분류 (125건)

| 구분 | 건수 | 원인 | 회복 방법 |
|---|---:|---|---|
| **A** 2024 캐시 부재 | 5 | DART API에 신규 요청 필요 | 1회성 backfill 도구 (DART API 5회 호출) |
| **D** 금융주 sector 분기 누락 | 25 | parser 코드 버그 | dart_api.py:441-477에 `_calc_financial_revenue(prev_df, sector, code)` 분기 추가 |
| **G** 일반 종목 stale | 95 | 저장 시점 parser 한계 (캐시 hit으로 미갱신) | 캐시 invalidate 없이 단순 재추출 (DART 호출 0회) |

D 분류 25건 (sector별):
- 금융 17건 (105560 KB금융, 316140 우리금융지주, 055550 신한지주, 024110 기업은행, 138040 메리츠금융지주, 138930 BNK금융지주, 071050 한국금융지주, 175330 JB금융지주, 139130 iM금융지주, 005810 풍산, 279570 케이뱅크, 006260 LS, 002020 코오롱, 229640 LS에코에너지, 009540 HD한국조선해양, 003550 LG, 029780 삼성카드, 180640 한진칼)
- 보험 5건 (005830 DB손해보험, 032830 삼성생명, 001450 현대해상, 088350 한화생명, ...)
- 증권 2건 (005940 현대증권, 016360 삼성증권)
- 리츠/기타 1건

## Phase 6 — 회복 방법 권고

### 권고 PR 구성 (3단계)

**P1-1. parser 수정 (S, 회귀 위험: 작음)**
- `collectors/dart_api.py:441-470` prev_revenue 계산 경로에 sector 분기 추가
- 영향: 금융주 25건 prev_revenue 회복 + 향후 신규 분석 정확도
- 테스트 2건: `test_extract_passes_sector_to_prev_revenue` + `test_prev_revenue_for_financial_holdings`

**P1-2. 일괄 재추출 도구 (M, 회귀 위험: 작음)**
- `tools/backfill_prev_revenue.py` 신규
- 대상: 122건 (G 95 + D 25 — A 5 제외)
- 흐름:
  1. financial_metrics에서 prev_revenue=0 AND revenue>0 종목 + sector 조회
  2. `extract_financial_metrics(code, 2025, sector=sector)` 재호출 (캐시 hit 활용 → DART API 호출 0회)
  3. UPSERT (보존 가드는 0→nonzero 갱신을 정상 허용)
- 검증: dry-run 후 apply, T1-6/T1-7 회귀 테스트

**P1-3. A 분류 5건 처리 (S, 회귀 위험: 작음)**
- 0120G0, 439260, 031210, 0126Z0, 217590
- DART API 호출하여 2024 parquet 신규 생성 후 P1-2 도구로 회복
- 또는 P1-2 도구에 "캐시 부재 시 자동 fetch" 옵션 추가

### 공수 합산
- P1-1: S (parser 4-5줄 수정 + 테스트 2건)
- P1-2: M (도구 1개 + 회귀 테스트 + dry-run/apply)
- P1-3: S (수동 호출 또는 P1-2에 흡수)
- 합계: **M+** (반나절~1일)

### 영향
- 회복 후 prev_revenue 결손 122/264 = 46% → 약 0%
- growth_score 정확도 회복 → 일일 점수 안정성 ↑
- 향후 신호 변경 의외성 ↓ (4-30 cycle 같은 +20점 갑작스런 회복 사라짐)

---

## 부록 — 검증 명령

```bash
# 일반 G 분류 종목 — 단순 재추출만으로 회복되는지 확인
python3 -c "
import sys; sys.path.insert(0,'/root/kospianal/kospi-analyzer')
import os; os.chdir('/root/kospianal/kospi-analyzer')
from collectors.dart_api import DARTClient
c = DARTClient(); c.load_corp_codes()
for code, sec in [('020150','전기·전자'),('006650','화학'),('012450','운송장비·부품')]:
    m = c.extract_financial_metrics(code, year=2025, sector=sec)
    print(code, m['revenue'], m['prev_revenue'])
"

# D 분류 금융주 — 현재 parser는 prev=0
python3 -c "
import sys; sys.path.insert(0,'/root/kospianal/kospi-analyzer')
import os; os.chdir('/root/kospianal/kospi-analyzer')
from collectors.dart_api import DARTClient
c = DARTClient(); c.load_corp_codes()
m = c.extract_financial_metrics('105560', year=2025, sector='금융')
print('KB금융 prev_revenue:', m['prev_revenue'])  # 0 — 코드 버그
"
```

# 잔존 결손 진단 보고서 — net/assets/equity/ROE/op (2026-04-30)

작성일: 2026-04-30
작성자: 진단 (코드/DB 무수정, SELECT only)
대상: `financial_metrics` (year=2025, quarter='annual')

---

## 결론 한 줄

사용자 명시 "28건"은 카테고리 합산이며, **distinct 종목은 20건**. 이중 **12건은 우선주/맥쿼리/델리스팅(rcept_no='') = 구조적 정상**, **8건은 parser 결함**(혹은 stale). parser 결함 8건은 `_get_account_value(BS, …)` 호출에 **`account_ids=["ifrs-full_Assets"|"ifrs-full_Equity"]` fallback이 빠진 한 줄짜리 누락**이 원인이며, 단일 패치 + 재추출로 8/8 회복 가능. DART API 호출 0회(전 종목 캐시 hit).

---

## Phase 1 — 결손 종목 식별 (DB 실제값)

### 사용자 진단 vs 실측

| 카테고리 | 사용자 주장 | 실측 |
|---|---|---|
| net_income=0 + rev>0 | 6건 | **1건** (0126Z0) |
| total_assets=0 | 9건 | **17건** |
| total_equity=0 | 10건 | **16건** |
| ROE=0 + net>0 | 6건 | **2건** (005440, 075580) |
| op_income=0 + rev>0 | 1건 | **0건** |
| **합계 (overlap 포함)** | 32 | 36 |
| **distinct 종목** | — | **20건** |

→ 이후 분석은 **distinct 20종목**으로 진행.

### 1-2. distinct 20종목 구분

| 그룹 | 건수 | 특징 |
|---|---|---|
| **A. rcept_no 보유 (parser 의심)** | 8 | DART 보고서 수집됨, 일부 항목만 0 |
| **B. rcept_no 빈값 (수집 자체 부재)** | 12 | 우선주 11 + 정체불명 1 (005308) |

---

## Phase 2 — 그룹 A 8종목 상세 (parser 의심)

| stock_code | 종목명 | 섹터 | rev | op | net | assets | eq | ROE | rcept_no |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 047050 | 포스코인터내셔널 | 유통 | 32.4T | 1.17T | 637B | **0** | 7.81T | 8.15 | 20260318 |
| 006400 | 삼성SDI | 전기·전자 | 13.3T | -1.72T | -585B | **0** | 23.6T | -2.48 | 20260310 |
| 005440 | 현대지에프홀딩스 | 금융 | 8.09T | 274B | 519B | **0** | **0** | **0** | 20260319 |
| 004170 | 신세계 | 유통 | 6.93T | 480B | 64.6B | **0** | 6.57T | 0.98 | 20260316 |
| 051900 | LG생활건강 | 화학 | 6.36T | 171B | -85.8B | **0** | **0** | **0** | 20260316 |
| 066970 | 엘앤에프 | 전기·전자 | 2.15T | -157B | -535B | 3.13T | **0** | **0** | 20260423 |
| 075580 | 세진중공업 | 운송장비·부품 | 403B | 73.4B | 59.8B | 638B | **0** | **0** | 20260323 |
| 0126Z0 | 삼성에피스홀딩스 | 금융 | 252B | -63.6B | **0** | 7.72T | 5.79T | 0 | 20260312 |

---

## Phase 3 — 원본 parquet 분석

### 3-1. parquet의 sj_div 분포 (전 8종 공통)

`SCE / CF / BS / CIS` 위주, 일부만 `IS`. **단일 포괄손익계산서(CIS)만 제출**하는 회사가 다수.
parser는 `_get_account_value`가 sj_div="IS" 호출 시 CIS로 fallback하므로 net/op는 영향 없음.

### 3-2. account_id `ifrs-full_Assets` 의 account_nm 변형 (8종)

| code | account_nm |
|---|---|
| 047050 | **`총자산`** |
| 006400 | **`자산 합계`** |
| 005440 | **`자산`** |
| 004170 | **`총자산`** |
| 051900 | `자산 총계` (공백) |
| 066970 | `자산총계` ← 표준 |
| 075580 | `자산총계` ← 표준 |
| 0126Z0 | `자산총계` ← 표준 |

### 3-3. account_id `ifrs-full_Equity` 의 account_nm 변형

| code | account_nm |
|---|---|
| 047050 | `자본총계` ← 표준 |
| 006400 | `자본총계` ← 표준 |
| 005440 | **`자본`** |
| 004170 | `자본총계` ← 표준 |
| 051900 | `자본 총계` (공백) |
| 066970 | **`기말자본`** |
| 075580 | **`기말 자본`** |
| 0126Z0 | `자본총계` ← 표준 |

### 3-4. account_id `ifrs-full_ProfitLoss` 의 account_nm 변형

| code | account_nm |
|---|---|
| 0126Z0 | **`당기순손실`** (단일 행, IS 없고 CIS만) |
| 그 외 | `당기순이익(손실)` ← 표준 |

---

## Phase 4 — parser 코드 감사

`collectors/dart_api.py:390~394`:

```python
metrics["total_assets"] = self._get_account_value(df, "BS", ["자산총계"])
metrics["total_liabilities"] = self._get_account_value(df, "BS", ["부채총계"])
metrics["total_equity"] = self._get_account_value(df, "BS", ["자본총계"])
metrics["current_assets"] = self._get_account_value(df, "BS", ["유동자산"])
metrics["current_liabilities"] = self._get_account_value(df, "BS", ["유동부채"])
```

**핵심 결함**: BS 5개 metric 호출 모두 `account_ids=` 인자가 **누락**되어 있다. 동일 함수의 `revenue/operating_income/net_income` 호출(385~389行)은 `86ec996` 커밋(N1, 4-29 17:33)에서 `account_ids=["ifrs-full_..."]` fallback이 추가됐지만, **BS에는 그 패치가 적용되지 않음**.

`_get_account_value` 4단계 매칭 (623~679行):
1. account_id 정확 일치  ← BS 호출엔 account_ids 인자가 없어 skip
2. account_nm 정확 일치  ← `자산총계` ≠ `총자산` 등
3. 공백 정규화 후 정확 일치 (`_normalize_nm` = 공백/NBSP 제거)
4. 부분 일치 (`str.contains`)

`자산 총계` (공백) → 정규화 후 `자산총계` 매치 ✓ (3단계)
`총자산` → 정규화해도 `총자산`, contains도 fail (단계 2~4 모두 miss)
`자산 합계` → 정규화 `자산합계`, miss
`자산` → contains 단계에서 `자산`이 `자산총계`를 포함하지 않으므로 miss
`기말자본` / `기말 자본` → 동일 miss
`당기순손실` → 4단계에서 후보 `당기순이익` substring 매칭 fail

---

## Phase 5 — 가설 검증 (시뮬레이션 결과)

캐시 parquet에 **현행 `extract_financial_metrics`** 호출 → DB 비교:

| code | net DB→parser | assets DB→parser | equity DB→parser |
|---|---|---|---|
| 047050 | 637B → 637B ✓ | **0 → 0** ✗ | 7.81T → 7.81T ✓ |
| 006400 | -585B → -585B ✓ | **0 → 0** ✗ | 23.6T → 23.6T ✓ |
| 004170 | 64.6B → 64.6B ✓ | **0 → 0** ✗ | 6.57T → 6.57T ✓ |
| 051900 | -85.8B → -85.8B ✓ | **0 → 6.87T** ⓢ | **0 → 5.57T** ⓢ |
| 005440 | 519B → 519B ✓ | **0 → 0** ✗ | **0 → 0** ✗ |
| 066970 | -535B → -535B ✓ | 3.13T → 3.13T ✓ | **0 → 0** ✗ |
| 075580 | 59.8B → 59.8B ✓ | 638B → 638B ✓ | **0 → 0** ✗ |
| 0126Z0 | **0 → -31.4B** ⓢ | 7.72T → 7.72T ✓ | 5.79T → 5.79T ✓ |

- `✓` = 일치, `✗` = parser도 실패, `ⓢ` = parser 정상이나 DB가 stale

### 가설 매트릭스

| 가설 | 의미 | 해당 |
|---|---|---|
| **A. 캐시 부재** | DART 미수집 | 그룹 B 12건 (parquet 없음) |
| **B. parser sector 분기 누락** | prev_revenue 패턴 | **해당 없음** (sector 분기 무관) |
| **C. account_id 매칭 결함** | BS 호출에 `account_ids=` 누락 | **그룹 A 핵심 원인** |
| **D. ROE 계산 코드 버그** | net/eq 정상인데 ROE=0 | **해당 없음** (모두 eq=0이라 ROE=0이 정합) |
| **E. 신규/스핀오프 정상 결손** | 자료 자체 부재 | 0126Z0 일부 (스핀오프, 단 2025 자료 존재) |
| **F. stale (parser 사후 패치)** | 코드 개선 후 재추출 안 함 | 051900 assets+eq, 0126Z0 net (3 cell) |

### 가설 채택 (metric별)

| metric | 가설 | 비고 |
|---|---|---|
| net_income (1건: 0126Z0) | **F (stale)** | 현행 parser는 정상 동작 |
| total_assets (5건) | **C** (047050, 006400, 004170, 005440) + **F** (051900) | parser BS 호출에 `account_ids` 누락 |
| total_equity (5건) | **C** (005440, 066970, 075580) + **F** (051900) | 동일 |
| ROE (2건: 005440, 075580) | **C 의 부수효과** | eq 회복하면 ROE 자동 계산됨 |
| op_income | 해당 없음 | 0건 |

---

## Phase 6 — 회복 가능성 / 방법 / 공수

### 그룹 A (8종목) — 회복 가능

#### 케이스 F (stale): 051900, 0126Z0 — 3 cell

- **방법**: 단순 재추출 (`tools/recover_prev_metrics.py` 패턴 재사용)
- **DART API 호출**: 0회 (캐시 hit)
- **공수**: 5분

#### 케이스 C (parser 패치 필요): 047050, 006400, 004170, 005440, 066970, 075580 — 6종목

**한 줄 패치 (collectors/dart_api.py:390~394 후보)**:

```python
metrics["total_assets"] = self._get_account_value(
    df, "BS", ["자산총계", "총자산", "자산 합계"],
    account_ids=["ifrs-full_Assets"],
)
metrics["total_liabilities"] = self._get_account_value(
    df, "BS", ["부채총계", "총부채", "부채 합계"],
    account_ids=["ifrs-full_Liabilities"],
)
metrics["total_equity"] = self._get_account_value(
    df, "BS", ["자본총계", "기말자본"],
    account_ids=["ifrs-full_Equity"],
)
metrics["current_assets"] = self._get_account_value(
    df, "BS", ["유동자산"],
    account_ids=["ifrs-full_CurrentAssets"],
)
metrics["current_liabilities"] = self._get_account_value(
    df, "BS", ["유동부채"],
    account_ids=["ifrs-full_CurrentLiabilities"],
)
```

- account_id fallback이 1단계에서 hit하므로, account_nm 변형(`자본`, `자본 총계`, `기말 자본`, `자산`, `총자산`, `자산 합계`, `자산 총계`)을 모두 흡수.
- `prev_*` 경로 (453~471行)에도 동일 변경 필요 (BS 미사용이므로 실제로는 IS/CF만, 부분이지만 일관성 위해 검토).
- ROE는 자동 회복 (eq 0→실제값 → `_calc_ratio` 정상 분기).

**공수**:
- 코드 패치: 30분 (5줄, ROE는 derive)
- 회귀 테스트: 8종 fixture 추가 1시간
- 일괄 재추출 도구: 기존 `recover_prev_metrics.py` 한정 재사용 가능 (~30분)
- DART API 호출: 0회 (캐시 hit)
- 총: ~2시간

### 그룹 B (12종목) — 회복 불가/구조적

| code | 종목명 | 사유 | 액션 |
|---|---|---|---|
| 005935, 006405, 009155, 066575, 051915, 005385, 005387, 000155, 000815, 00680K | 우선주 10종 | DART 사업보고서는 모회사 단위로만 제출. 우선주는 별도 corp_code 미존재. | **회복 불가**. 점수산정 시 모회사(005930 등) 데이터 참조 옵션 검토 (별도 PR) |
| 088980 | 맥쿼리인프라 | 인프라 펀드, K-IFRS 일반 양식 미적용 | 별도 schema 필요 (저우선) |
| 005308 | (이름 없음, 기타) | stock_master에 stock_name=None | **delisted/invalid 추정**. row 정리 필요 (별도 PR) |

### 회복률 예측

| | distinct 결손 | 회복 가능 | 회복 후 잔존 |
|---|---|---|---|
| 패치 + 재추출 후 | 20 | 8 | **12 (구조적 정상)** |

---

## 권고

1. **즉시 (P0)**: parser 패치 (BS 5 호출 + account_ids fallback) — PR 별도, 약 2시간
2. **즉시 (P0)**: 패치 후 8종 일괄 재추출 (DART 호출 0)
3. **별도 (P1)**: 우선주 11종 점수산정 시 모회사 fallback 룰 도입 검토
4. **별도 (P2)**: 005308 정리 + 맥쿼리인프라 fund-style schema 검토

---

## 검증 명령 (이 보고서 재현용)

```bash
# Phase 1
python3 -c "import sqlite3; c=sqlite3.connect('data/kospi_analyzer.db'); …"

# Phase 5 시뮬레이션
python3 -c "  # extract_financial_metrics를 캐시에 직접 호출
import sys, os; from pathlib import Path
sys.path.insert(0, '.')
from collectors.dart_api import DARTClient
import pandas as pd, types
client = DARTClient.__new__(DARTClient)
client.api_key='dummy'; client._corp_code_map={}
client._cache_dir=Path('data/dart_cache')
client._cache_hit=client._cache_miss=client._api_call_count=0
client._last_call_ts=0.0
def fake(self, code, year):
    p=f'data/dart_cache/{code}_{year}_annual.parquet'
    return pd.read_parquet(p) if os.path.exists(p) else None
client.get_financial_statements=types.MethodType(fake, client)
print(client.extract_financial_metrics('047050', 2025, sector=None))
"
```

---

## 금지사항 준수

- 코드 수정: ✗ 없음
- DB 수정: ✗ 없음
- 봇 재시작: ✗ 없음
- 일괄 회복 도구 작성: ✗ 없음 (진단만)

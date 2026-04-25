# DART PL 결손 조사 보고서

조사일: 2026-04-25 (브랜치: `feat/dart-pl-loss-investigation`)
조사 대상: `financial_metrics` 테이블의 손익계산서(PL) 결손
범위: 코드 변경 없음. 원인 규명 + 권고만.

## 요약

- **결손 규모**: `financial_metrics(year=2025, quarter='annual')` 233건 중 **180건(77.3%) PL 전부 0**
  - 이 중 **163건(70%)은 BS는 정상이고 PL만 0** (023530 패턴)
  - 17건은 응답 자체가 비어 있음
  - 53건만 정상
- **원인**: 단일 파싱 버그 1건으로 확정됨.
  - `collectors/dart_api.py:454` `_get_account_value`가 `sj_div == "IS"`만 필터링.
  - DART 응답에서 K-IFRS 단일 포괄손익계산서만 제출하는 기업은 IS 행이 0개고 손익 계정이 모두 `sj_div="CIS"`(연결포괄손익계산서)에 들어 있음.
  - 코드는 CIS 행을 절대 보지 않으므로 매출/영업이익/당기순이익이 항상 0으로 추출됨.
  - DART 응답 자체는 정상이며, BS·CF는 별도 sj_div 코드(BS, CF)라서 영향 없음.
- **영향**:
  - TOP 10 점수 신뢰도 심각하게 흔들림. ROE/영업이익률/성장 점수가 결손 종목에서 모두 0이라, 가치·재무·성장·퀄리티 카테고리 점수 산출에 강한 편향.
  - 결손 종목들 다수가 대형주(SK하이닉스, 카카오, 삼성SDI, S-Oil, 롯데쇼핑 등)라 시장 대표성 큼.
- **권고 다음 작업**: `_get_account_value`의 IS 필터를 `["IS","CIS"]`로 확대. IS가 비어 있을 때 CIS로 fallback. 별도 브랜치/지침서.

---

## 1. 결손 패턴 (Phase 2)

### 결손 분류 (`year=2025, quarter='annual'`, total=233)

| 분류 | 건수 | 비율 |
|---|---:|---:|
| 전부 0 (응답 자체 없음) | 17 | 7.3% |
| **PL만 0, BS는 정상** (023530 패턴) | **163** | **70.0%** |
| 완전 정상 | 53 | 22.7% |

### 시총 규모별 결손율 (stock_scores 2026-04-24 join)

| 버킷 | total | 결손 | 결손율 |
|---|---:|---:|---:|
| 대형(10조+) | 19 | 6 | 31.6% |
| 중형(1조+) | 29 | 12 | 41.4% |
| 소형(1천억+) | 1 | 1 | 100.0% |

대형주에도 광범위. SK하이닉스, 삼성바이오로직스, 카카오, 삼성SDI 등 결손.

### 업종별 결손율 (상위)

| 업종 | total | 결손 | 결손율 |
|---|---:|---:|---:|
| 전기·전자 | 42 | 32 | 76.2% |
| 금융 | 32 | 27 | 84.4% |
| 화학 | 30 | 22 | 73.3% |
| 제약 | 13 | 12 | 92.3% |
| 금속 | 11 | 11 | 100.0% |
| 보험 | 7 | 7 | 100.0% |
| 증권 | 6 | 6 | 100.0% |
| 통신 | 3 | 0 | 0.0% |

업종 가리지 않음 (단 통신 3종목은 모두 정상).

### 우선주 vs 보통주

- 끝자리 '5' (우선주 중심): 6건 / 6건 (100%)
- 그 외: 174 / 227 (76.7%)

우선주는 100%지만 보통주도 광범위 결손.

### updated_at 시간대별

| 수집 시각 | total | 결손 | 결손율 |
|---|---:|---:|---:|
| 2026-04-07 16:04 | 3 | 2 | 66.7% |
| 2026-04-07 17:15 | 9 | 6 | 66.7% |
| 2026-04-07 17:42 | 192 | 148 | 77.1% |
| 2026-04-07 18:31 | 1 | 1 | 100.0% |
| 2026-04-13 15:48 | 15 | 13 | 86.7% |
| 2026-04-20 15:48 | 13 | 10 | 76.9% |

특정 시간대에 결손이 몰리지 않음. rate limit/quota 가설을 약화함.

### 023530 (롯데쇼핑) 단건 — DB

- `financial_metrics(2025 annual)`만 1행 존재. 2024 annual은 DB에 없음.
- BS 정상: total_assets=37.9조, total_liabilities=21.0조, total_equity=16.9조, debt_ratio=124.77, current_ratio=52.0, dividend_yield=4.63
- PL 전부 0: revenue=0, operating_income=0, net_income=0, ebitda=0, prev_*=0
- 파생 0: roe=0, operating_margin=0, revenue_growth_yoy=0, op_income_growth_yoy=0
- updated_at=2026-04-07 17:42:06

---

## 2. DART API 응답 검증 (Phase 3)

DART API 직접 호출 대신, 이미 받은 parquet 캐시(`data/dart_cache/`)를 raw로 분석.
캐시는 `_save_to_cache`(dart_api.py:585)에서 응답을 그대로 저장하므로 DART 원본 응답과 동일.

### 결손 종목 5개 — `sj_div` 분포 (2025 annual)

| 종목 | rows | sj_div 분포 |
|---|---:|---|
| 023530 롯데쇼핑 | 334 | SCE=171, CF=76, BS=51, **CIS=36, IS=0** |
| 000660 SK하이닉스 | 230 | SCE=112, BS=54, CF=37, **CIS=27, IS=0** |
| 035720 카카오 | 233 | SCE=91, BS=65, CF=41, **CIS=36, IS=0** |
| 011170 롯데케미칼 | 286 | SCE=133, CF=62, BS=52, **CIS=39, IS=0** |
| 207940 삼성바이오로직스 | 179 | SCE=64, BS=49, CF=36, **CIS=30, IS=0** |

**모두 `IS` 행이 0개. 손익 데이터는 `CIS` 행에 들어있음.**

### 정상 종목 3개

| 종목 | rows | sj_div 분포 |
|---|---:|---|
| 005930 삼성전자 | 229 | SCE=105, BS=52, CF=42, IS=17, CIS=13 |
| 004170 신세계 | 274 | SCE=144, BS=52, CF=47, IS=17, CIS=14 |
| 008770 호텔신라 | 236 | SCE=88, CF=76, BS=45, IS=18, CIS=9 |

**정상 종목은 IS와 CIS를 둘 다 제출.** 코드의 IS 필터가 우연히 동작한 케이스.

### 결손 종목 CIS 행 raw 내용 (023530 발췌)

```
sj_div=CIS:
  - 매출         | thstrm_amount=13,738,354,725,833  | account_id=ifrs-full_Revenue
  - 영업이익      | thstrm_amount=547,037,221,095     | account_id=dart_OperatingIncomeLoss
  - 당기순이익(손실)| thstrm_amount=73,555,557,525     | account_id=ifrs-full_ProfitLoss
  - 매출원가      | 7,075,160,203,967
  - 매출총이익    | 6,663,194,521,866
  - (외 포괄손익 항목들)
```

→ 매출액·영업이익·당기순이익 모두 정상값 존재. 데이터는 받았으나 코드가 못 봄.

### 005930(정상) IS와 CIS 동일 매출액 비교

- IS 매출액: 333,605,938,000,000
- CIS도 동일 계정 존재(이중 제출). 이 종목은 IS 우선 추출되어 정상 동작.

### 캐시 전체 (2025 annual 223건)

| 캐시 분류 | 건수 | 비율 |
|---|---:|---:|
| IS 행 있음 | 55 | 24.7% |
| **IS 행 없음 (CIS만)** | **168** | **75.3%** |

**캐시상 IS 결손율(75.3%)이 DB의 PL 결손율(77.3%)과 정확히 일치.** 원인은 이 한 가지 분기에서 모두 설명됨.

---

## 3. 캐시 vs DB (Phase 4)

- 캐시 parquet에는 결손 종목들의 손익 데이터가 **CIS 행에 정상 보존**되어 있음.
- DB(financial_metrics)는 0으로 저장. 즉 데이터 손실은 DART → 캐시 단계가 아니라 **캐시 → DB 변환 단계**(`extract_financial_metrics` 안의 `_get_account_value`)에서 발생.
- 캐시는 무결하므로 캐시를 다시 읽어서 재처리하면 보강 가능 (별도 작업).

---

## 4. 가설 검증 (Phase 5)

| 가설 | 결과 | 증거 |
|---|---|---|
| A. account_id가 종목별로 다름 (K-IFRS vs K-GAAP) | ✗ 반증 | 결손/정상 모두 `ifrs-full_Revenue`, `dart_OperatingIncomeLoss`, `ifrs-full_ProfitLoss` 동일. account_id는 일관됨. |
| B. 연결재무제표 vs 별도재무제표 차이 | ✗ 반증 | 모두 `fs_div=CFS`로 응답 받음(코드가 CFS 우선). status=000 정상 응답. |
| C. 2025 사업보고서 미공시 | ✗ 반증 | rcept_no, reprt_code='11011' 모두 응답에 존재. 응답 list가 200~334행으로 정상 채워짐. |
| D. API rate limit / 일일 quota | ✗ 반증 | 같은 수집 사이클 내(2026-04-07 17:42에 192건 한 번에)에 결손과 정상이 섞여 있음. 시각 분포에 패턴 없음. |
| E. 응답 파싱 시 빈 문자열/"-" 처리 | △ 부분 | `_parse_amount`에서 빈값 처리는 정상. 문제는 그 이전 단계 — 행 자체가 IS 필터에서 제외되어 `_parse_amount`까지 도달도 못함. |
| **F (신규). `sj_div='CIS'`(포괄손익계산서) 행을 IS로 분류하지 않음** | **✓ 확정** | 결손 5/5종목 모두 IS=0, 손익 데이터는 CIS 행. 코드 `df[df["sj_div"]=="IS"]`가 CIS 행을 무시. 캐시 결손율 75.3% ≒ DB 결손율 77.3%로 단일 원인으로 모두 설명됨. |

---

## 5. 결론

**원인 확정 — 단일 파싱 버그**:

`collectors/dart_api.py:438-466 _get_account_value`의 다음 한 줄:

```python
filtered = df[df["sj_div"] == sj_div]
```

이 분기에서 손익계산서 항목(`sj_div="IS"`)을 찾을 때, K-IFRS 하의 다수 기업이 단일 포괄손익계산서(`sj_div="CIS"`)만 제출한다는 사실을 반영하지 못함.

DART OpenAPI `fnlttSinglAcntAll` 응답에서 `sj_div`는 다음 의미:
- `BS` 재무상태표
- `IS` 손익계산서 (별도 제출 시)
- `CIS` 포괄손익계산서 (단일 제출 또는 IS와 별도 제출)
- `CF` 현금흐름표
- `SCE` 자본변동표

K-IFRS 기준으로 **CIS만 제출하는 기업이 다수(전체 223 중 168건, 75.3%)**. CIS의 앞부분에 매출/영업이익/당기순이익이 일반 IS와 동일한 형태로 들어 있고, 뒷부분에 기타포괄손익(OCI) 항목이 추가될 뿐이다. 즉 손익 정보는 CIS에서도 동일하게 추출 가능하다.

부수 영향:
- `revenue=0` → `revenue_growth_yoy=0`, `op_income_growth_yoy=0`
- `operating_income=0` → `operating_margin=0`, `ebitda=|depreciation|` 만 남음
- `net_income=0` → `roe=0`, `consecutive_loss_years` 판정 왜곡
- 스코어러의 가치(PEG/EV-EBITDA), 재무(ROE/영업이익률), 성장, 퀄리티 모두 영향

---

## 6. 권고 다음 작업

별도 브랜치(`fix/dart-cis-fallback` 등)에서 수행. 본 조사 결과를 근거로 한 구체 수정 방향:

1. **`_get_account_value` IS 필터 확대 + 우선순위**
   - `sj_div == "IS"` 이거나 IS가 비어 있을 때 `sj_div == "CIS"`로 fallback.
   - 정상 종목(IS와 CIS 동시 제출, 24.7%)에서 회귀 방지 위해 IS 우선.
   - CIS 행에는 OCI 항목이 섞여 있으므로 account_names 후보가 OCI와 충돌하지 않도록 점검.
     예: `당기순이익` ↔ `기타포괄손익`, `법인세효과` 등은 별 계정이라 혼동 없음.

2. **`extract_financial_metrics`에서 sj_div 인자 호출부 점검**
   - IS로 호출되는 곳: 매출, 영업이익, 당기순이익, 감가상각비(IS 우선)
   - CF, BS는 영향 없음. 단 감가상각비는 IS·CF 둘 다 있는 경우가 있어 기존 fallback 유지.

3. **재처리 (백필)**
   - 캐시 parquet은 무결하므로 코드 수정 후 캐시를 보존한 채 `extract_financial_metrics`를 재실행하면 결손 데이터 회복 가능.
   - 다만 `_load_from_cache`는 `CACHE_DAYS(=90)` 안의 캐시를 사용하므로 30일 가량 자연 적용. 명시적 재실행 스크립트가 권장.

4. **회귀 테스트**
   - 결손 5종목(023530, 000660, 035720, 011170, 207940) + 정상 3종목(005930, 004170, 008770)에 대해 매출/영업이익/당기순이익이 0이 아닌 정상값이 추출되는지 단위 테스트 추가.
   - parquet fixture를 `tests/fixtures/`에 두고 `_get_account_value`를 직접 검증.

5. **CIS fallback 단위 테스트 누락 주의**
   - 정상 종목은 IS·CIS 동시 보유라서 CIS-only 회귀를 못 잡음. 별도 fixture 필요.

6. **보고/확인 후 수정 범위 결정**
   - 본 보고서 검토 후 수정 브랜치 분기. 코드 변경 범위는 `dart_api.py:_get_account_value` 한 함수 + 테스트로 한정 가능 추정.

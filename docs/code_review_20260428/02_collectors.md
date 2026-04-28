# Stage 3 — Collectors (KIS + DART)

**대상**: `collectors/kis_api.py` (964) + `collectors/dart_api.py` (798)
**날짜**: 2026-04-28

---

## 1. A1 정밀 검증 (Stage 2 잠정 HIGH)

### 결론: 시나리오 A — **LOW로 강등**

`acheck_token` (kis_api.py:533) 본문:
```python
async def acheck_token(self) -> bool:
    try:
        await self._token_manager.get_token_async()
        return True
    except KISAPIError:
        return False
```

`get_token_async`는:
1. `_async_lock` 직렬화
2. 메모리 캐시 hit → 즉시 반환
3. 디스크 캐시 (`token_cache/kis_token.json`) 로드
4. 만료 시 `_issue_new_token()` (sync `requests.post`)

**aiohttp 세션 미사용** — `aiohttp.ClientSession`은 `_request_get` 경로에서만 필요. `acheck_token`은 `requests` (sync) 사용.

→ `async with self.kis:` 컨텍스트 밖 호출 안전.

### 모든 a-prefix 호출 점검 (main.py + tools/)
| 호출처 | 메서드 | 컨텍스트 필수 | 평가 |
|--------|--------|-------------|------|
| main.py:189 | `acheck_token` | ❌ 불필요 | ✓ |
| main.py:282 | `aget_kospi_index` | ✓ async with line 281 | ✓ |
| main.py:432 | `aget_kospi_stock_list` | ✓ _collect_data 컨텍스트 | ✓ |
| main.py:498 | `aget_all_stock_prices` | ✓ _collect_data 컨텍스트 | ✓ |
| main.py:322,327 | `aget_stock_price/aget_daily_chart` (gather) | ✓ async with line 320 | ✓ |
| tools/dry_run_async.py | 모두 `async with` 안 | ✓ | ✓ |
| tools/recover_performance_tracking.py | `async with` 안 | ✓ | ✓ |
| tools/benchmark_collect.py | `async with` 안 | ✓ | ✓ |

전수 안전. `_require_session` (kis_api.py:254) 가드가 컨텍스트 외 호출 시 명확한 RuntimeError 발생 → 회귀 보호.

---

## 2. KOSPI change_rate=0.0 (어제 P3 의심 → **HIGH 확정**)

### 라이브 재현 (4-28)
```
{'index': 6641.02, 'change': 25.99, 'change_rate': 0.0}
```

기대값: 25.99 / (6641.02 - 25.99) ≈ **0.39%**

### 원인 분석 — `aget_kospi_index` (kis_api.py:445-473)
```python
return {
    "index": self._safe_float(out.get("bstp_nmix_prpr", "0")),
    "change": self._safe_float(out.get("bstp_nmix_prdy_vrss", "0")),
    "change_rate": self._safe_float(out.get("prdy_ctrt", "0")),
}
```

- `bstp_nmix_prpr` (현재가) ✓ — 6641.02 정상
- `bstp_nmix_prdy_vrss` (전일대비) ✓ — 25.99 정상
- `prdy_ctrt` ✗ — KIS 인덱스 API 응답에 해당 키 부재 또는 빈 값. `_safe_float` fallback 0.0.

### 추정 정정
KIS 인덱스 API는 등락률 키로 **`bstp_nmix_prdy_ctrt`**를 사용 (개별 종목용 `prdy_ctrt`와 다름). `inquire-index-price` 엔드포인트의 정확한 응답 키 확인 필요.

### 영향
- **분석 영향**: 0건. `change_rate`는 텔레그램 리포트 표시 전용 (formatter), 스코어링·필터·신호 사용 안 함.
- **사용자 정보 신뢰**: 매일 0%로 표시되면 신뢰 저하.
- **health check 미감지**: T1-2는 index 값 범위만 검사, change_rate 미검증.

### 발견 ID
**N4 [HIGH]** KOSPI change_rate 0.0 silent fail. 한 줄 패치 가능 (`prdy_ctrt` → `bstp_nmix_prdy_ctrt`). **별도 PR 권고**.

---

## 3. DART `_get_account_value` 4단계 매칭 (kis_api.py:561-618)

```
1단계 → IFRS account_id 정확 일치 (옵션)
2단계 → account_nm 정확 일치
3단계 → 공백 정규화 후 정확 일치
4단계 → 부분 일치 (str.contains)
```

### 현황
| 호출 위치 | account_ids 사용 | 평가 |
|-----------|-----------------|------|
| 매출액 (line 357) | ❌ | 한글만 |
| 영업이익 (line 360) | ❌ | 한글만 |
| 당기순이익 (line 363) | ❌ | 한글만 |
| 자산/부채/자본총계 (line 366-368) | ❌ | 한글만 (단일) |
| 유동자산/유동부채 (line 369-370) | ❌ | 한글만 (단일) |
| 감가상각비 (line 379-385) | ❌ | 한글만 |
| 현금및현금성자산 (line 391) | ❌ | 한글만 |
| **operating_cf** (line 397) | ✓ `ifrs-full_CashFlowsFromUsedInOperatingActivities` | 어제 패치 |
| **capex** (line 402) | ✓ `ifrs-full_PurchaseOfPropertyPlantAndEquipment*` | 어제 패치 |
| 전년도 매출/영업이익 (line 420-422) | ❌ | 한글만 |
| 배당 (별도 경로) | n/a | - |

### 발견
**N1 [MED]** IFRS account_id 우선 매칭이 FCF에만 적용. 매출/영업이익/순이익/총계 항목은 한글 정확 매칭에 의존. 상장사 일부 (특히 IFRS17 보험사 신규 / 비IFRS / 이름 변형) 매칭 누락 가능.

영향 측정 (DB):
- `revenue=0` 12/256 (4.7%) 종목 — health check T1-6 통과 (5% 미만)
- 그러나 sector 분포 보면 일반 종목(전기·전자 3, 운송장비 2 등)에서도 결손 → **확장 패치 후보**

**별도 PR**: 매출/영업이익/순이익에 IFRS account_id 추가 — 회귀 위험 적고 결손율 개선.

---

## 4. 금융주 sector 분기 (`_calc_financial_revenue`, dart_api.py:253)

### KIS 라벨 vs DART 분기 매칭 검증 (DB 분포)
| KIS sector | 종목 수 | DART 분기 |
|-----------|--------|----------|
| 보험 | 8 | ✓ `sector == "보험"` |
| 증권 | 11 | ✓ `sector == "증권"` |
| 금융 | 36 | ✓ (단, BANK_HOLDING_CODES 11개로 좁힘) |
| 은행 | 0 | KIS는 "금융" 통합 |

→ **매칭 정합 OK**.

### `_BANK_HOLDING_CODES` 화이트리스트 (dart_api.py:36-48, 11개)
```
055550 신한지주, 105560 KB금융, 086790 하나금융, 316140 우리금융,
024110 기업은행, 279570 케이뱅크, 138040 메리츠금융, 139130 iM금융,
175330 JB금융, 071050 한국금융지주, 138930 BNK금융
```

### 발견
**N2 [INFO]** `_BANK_HOLDING_CODES` 하드코드. 신규 은행지주 상장 시 수동 갱신 필요. line 35에 TODO 명시. 운영 워크플로 명문화 권고:
- 분기 1회 KRX 공시에서 신규 금융지주 상장 점검
- 발견 시 코드 추가 + 회귀 테스트

별도 PR 후보 (낮은 우선순위).

### 매출=0 잔존 (sector 분기 적용 후에도)
| sector | 결손 |
|--------|------|
| 보험 | 1 |
| 증권 | 1 |
| 금융 | 1 |

→ IFRS17 신규 패턴 누락 또는 일반 지주(BANK_HOLDING_CODES 외)일 가능성. 추가 조사 권고 (별도 PR).

---

## 5. KIS Rate Limiter

### 구성 (`_default_rate_limit`, kis_api.py:161-177)
- env `KIS_RATE_LIMIT_PER_SEC` > `KISConfig.RATE_LIMIT_PER_SEC` > 하드 15
- `aiolimiter.AsyncLimiter` 토큰 버킷 — 진입(`__aenter__`)에서 지연 생성
- `_request_get` 매 호출마다 limiter 획득 + 재시도 + 지수 백오프 (`KISConfig.MAX_RETRIES`)

### 평가 ✓
- 정확하고 일관됨. 어제 발견된 모의투자 30건 한계는 `aget_kospi_stock_list`의 업종×가격대 분할로 우회 (line 484-...).

---

## 6. KIS 토큰 캐시 + 만료

### 흐름
1. 메모리 `_access_token` + `_token_expired_at` 검사 (`_is_token_valid`)
2. miss → `token_cache/kis_token.json` 디스크 로드
3. miss → `_issue_new_token()` (POST /oauth2/tokenP)
4. 만료 30분 전 (`TOKEN_REFRESH_BUFFER`) 사전 갱신

### 평가
**O3 [INFO]**: `_issue_new_token`이 sync `requests.post`. async lock 안에서 호출되지만 to_thread 격리 안 함 → 토큰 발급 시 약 1초간 이벤트 루프 블록. 하루 1~2회 발생이라 운영 영향 미미. 코멘트(line 96-97)에 명시되어 있음.

**O4 [INFO]**: 캐시 파일 권한 검사 없음. `token_cache/`가 group-readable일 가능성. 보안 영역 (Stage 9에서 검토).

---

## 7. 비즈니스 로직 정확성

### FCF (dart_api.py:411)
```python
if operating_cf != 0:
    metrics["free_cash_flow"] = operating_cf - capex
else:
    metrics["free_cash_flow"] = 0
```

- 어제 N4 토론한 NULL vs 0 구분 미반영 (P3). FCF=0이 "결손"인지 "실제 0"인지 구분 불가.
- 분석 영향: `analysis/scorer.py::_score_fcf_yield`에서 FCF=0이면 0점 부여 → **silent fail 가능** (실제 결손 종목이 0점으로 묻힘).
- 어제 health check T1-5에 FCF 결손율 검사 추가됨 — silent fail 노출됨.

### YoY 성장률 (`_calc_growth_rate`, line 458-470)
```python
if previous == 0:
    if current > 0: return 100.0
    elif current < 0: return -100.0
```
- 0→0 케이스: 0% 반환 (line 465 default). 정확.

### 비율 (`_calc_ratio`)
- 분모 0이면 0.0 반환. silent fail 가능 (예: ROE에서 자본=0 종목은 분석 의미 없지만 0점으로 통과). 영향은 score=0 → SELL 신호로 자동 분기되어 노출됨.

---

## 8. KIS 응답 파서 silent fail 점검

### `_safe_int` / `_safe_float` (line 806-832)
- 빈/`-`/예외 시 0 반환. silent fail 양산 가능 — health check T1-5/T1-6이 일부 노출.

### 알려진 silent fail (모두 health check가 잡음)
- KOSPI index=0 → T1-2 노출 ✓
- FCF=0 → T1-5 노출 ✓
- revenue=0 → T1-6 노출 ✓
- KOSPI change_rate=0 → **T 검사 항목 부재** ⚠️ (N4 별도 PR에 health check 보강 권고)

---

## 9. 테스트 갭 (Stage 8에서 정밀 검토)

| 영역 | 테스트 |
|------|--------|
| KIS async 패턴 | tests/test_kis_async.py 325줄 — 잘 커버 |
| KIS sync 호환 | tests/test_kis_sync_compat.py — 호환성 가드 |
| DART 매칭 | tests/test_dart_api.py 461줄 — 신규 패치 커버 |
| KOSPI index 파서 | **부재 추정** (Stage 8 검증) |
| 토큰 만료 자동 갱신 | **부재 추정** |
| Rate limiter 동시성 | tests/test_kis_async.py 일부 |

---

## 10. 누적 발견 (Stage 3)

| ID | 심각도 | 내용 | 권고 |
|----|--------|------|------|
| **N4** | **HIGH** | KOSPI `change_rate=0.0` silent fail (`prdy_ctrt` 키 오류 추정) | **즉시 별도 PR (한 줄 패치 가능)** |
| N1 | MED | IFRS account_id 매칭이 FCF에만 적용 — 매출/영업이익/순이익 확장 후보 | 별도 PR (점진적) |
| A1 (강등) | LOW | acheck_token 안전 확정 (시나리오 A) | 코멘트 보완만 |
| N2 | INFO | _BANK_HOLDING_CODES 운영 워크플로 명문화 | 별도 docs |
| O3 | INFO | 토큰 발급 sync request, 1초 블록 (하루 1-2회) | 영향 미미 |
| O4 | INFO | 토큰 캐시 파일 권한 점검 | Stage 9 |

---

## 11. CRIT 즉시 항목

**0건**. N4 (HIGH)는 silent fail이지만 분석/스코어링 영향 0이라 CRIT 아님. 단, 텔레그램 표시 신뢰 영향이라 **이번 주 별도 PR 권고**.

---

**다음**: Stage 4 (analysis/scoring) 진행 승인 요청.

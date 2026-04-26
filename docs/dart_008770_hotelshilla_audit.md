# 008770 호텔신라 백필 누락 단건 조사 (2026-04-26)

## 요약

호텔신라(008770)는 캐시에 모든 정상 라벨이 있고 패치 후 코드도 정상 추출하지만,
DB의 `operating_income=0` 상태가 유지되고 있다. 원인은 **백필 도구
(`tools/backfill_dart_pl.py`)의 WHERE 조건이 `revenue=0 AND op=0 AND net=0`
(완전 결손)이라 부분 결손인 호텔신라는 대상이 아니었기 때문**.

다음 분석 사이클(2026-04-27 15:40)의 정상 dart_api 수집에서 자연 갱신 예상.
즉시 수정 불필요.

## 1. 캐시 상태 (정상)

`data/dart_cache/008770_2025_annual.parquet` IS 18행 / CIS 9행:

| sj_div | account_nm | thstrm_amount |
|---|---|---:|
| IS | 매출액 | 4,068,306,491,409 |
| IS | **영업손익** | **13,512,516,028** |
| IS | 당기순이익(손실) | -172,848,550,007 |
| CIS | 당기순이익(손실) | -172,848,550,007 |
| IS | 영업외손익 | -243,688,232,385 |
| IS | 기타영업비용 | 1,510,940,797,712 |

## 2. 패치 후 코드 추출 (정상)

```
revenue   : 4,068,306,491,409  ← '매출액' 정확 매칭
op_income : 13,512,516,028     ← '영업손익' 정확 매칭
net_income: -172,848,550,007   ← '당기순이익(손실)' 정확/부분 매칭
```

## 3. DB 현재 상태 (부분 결손)

```sql
SELECT * FROM financial_metrics WHERE stock_code='008770' AND year=2025;
-- revenue=4,068,306,491,409  op=0  ni=-172,848,550,007  ebitda=50,741,448,215
-- updated_at=2026-04-07 17:42:06
```

- revenue/net_income 정상값
- operating_income=0 (부분 결손)
- ebitda는 |depreciation|만으로 산출 → 507억

## 4. 누락 원인

`tools/backfill_dart_pl.py:106-110`의 WHERE 조건:

```sql
SELECT * FROM financial_metrics
WHERE year=? AND quarter='annual'
  AND revenue=0 AND operating_income=0 AND net_income=0
```

세 PL 필드 **모두 0**인 종목만 백필 대상. 호텔신라는 revenue/net_income 정상값이 들어있어 대상에서 제외됐다.

같은 패턴이 추가로 있는지 측정:
- `revenue>0 AND operating_income=0 AND net_income!=0` 또는 `revenue>0 AND operating_income=0 AND net_income=0` 등 부분 결손은 backfill_dart_pl 미적용.

## 5. 추가 영향 범위

부분 결손 종목(op만 0) 조사:

```sql
SELECT COUNT(*) FROM financial_metrics
WHERE year=2025 AND quarter='annual'
  AND revenue>0 AND operating_income=0;
-- 측정 결과 (2026-04-26 dividend 백필 후): 3건
--   008770 호텔신라 (이건 글)
--   020150 롯데에너지머티리얼즈 (영업손실 라벨, 백필 도구가 op_income=0 조건이라 처리됨)
--   0126Z0 삼성에피스홀딩스 (동일)
```

C-3 패치 후 020150/0126Z0는 다음 사이클에 회복. 008770은 부분 결손이라 backfill 도구는 적용 안 되지만 정상 수집은 됨.

## 6. 권고

### 즉시 조치 (없음)
- 다음 분석 사이클(2026-04-27 15:40)에서 dart_api가 정상 호출 시 호텔신라 op 자연 갱신.
- 캐시는 90일 정책이라 4-7~7-7 사이 dart_api가 캐시 재사용 + extract_financial_metrics 패치 적용으로 op 정정.

### 후속 P3 (선택)
백필 도구 WHERE 조건 완화 — 부분 결손도 보강:
```sql
WHERE year=? AND quarter='annual'
  AND (revenue=0 OR operating_income=0 OR net_income=0)
```

단 정상 종목의 진짜 0(혼자 적자도 흑자도 아닌 케이스)을 잘못 덮어쓸 위험. 정확 일치 우선 시도라 안전하지만 별도 PR 필요. 효과는 작음(현재 1건만 영향).

## 7. 결론

코드 버그 아님. 백필 도구 보수적 WHERE 조건의 의도된 한계. 4-27 사이클에서 자연 회복 예상.

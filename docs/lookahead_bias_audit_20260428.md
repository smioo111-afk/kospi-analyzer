# 룩어헤드 편향 감사 (2026-04-28)

## 결론: 시나리오 A — 룩어헤드 없음

annual 재무 데이터에 한해 위반 **0건 / 245** (전수 검증).

---

## 1. 조사 배경

질문: 2026-04-27 시점에 분석 시스템이 사용한 2025년 annual 재무 데이터가
실제로 그 시점에 "이미 알 수 있던" 데이터인가?

만약 일부 종목의 공시일자(rcept_dt)가 2026-04-27 이후라면, 미래 정보를
사용한 룩어헤드 편향이 발생하여 백테스트 신뢰성이 훼손된다.

---

## 2. 데이터 소스

DART 캐시(parquet) 파일에 `rcept_no` 컬럼 존재 — 공시 접수번호.
첫 8자리가 공시일자 (`YYYYMMDD`).

**한계**: `data/kospi_analyzer.db::financial_metrics` 테이블에는
`rcept_dt` 또는 `rcept_no` 컬럼이 없음. 캐시 parquet에서만 추출 가능.

---

## 3. 표본 검증 (Phase 1)

| 종목코드 | 종목명 | 2025 annual rcept_dt | 4-27 대비 |
|----------|--------|---------------------|-----------|
| 005930 | 삼성전자 | 2026-03-10 | -48일 (정상) |
| 004800 | 효성 | 2026-03-12 | -46일 (정상) |
| 088350 | 한화생명 | 2026-03-31 | -27일 (정상) |
| 105560 | KB금융 | 2026-03-24 | -34일 (정상) |
| 005440 | 현대지에프홀딩스 | 2026-03-19 | -39일 (정상) |

표본 5종목 모두 4-27 이전 공시. 룩어헤드 없음.

---

## 4. 전수 검증 (Phase 2)

`data/dart_cache/*_2025_annual.parquet` 245개 파일 전수 조사.

### 공시일자 분포
| 월 | 건수 |
|----|------|
| 2026-02 | 1 |
| 2026-03 | 241 |
| 2026-04 | 3 |

### 가장 늦은 공시일자 (TOP 5)
| 종목 | rcept_dt | 4-27 대비 |
|------|----------|-----------|
| 483650 | 2026-04-07 | -20일 |
| 011790 | 2026-04-03 | -24일 |
| 062040 | 2026-04-03 | -24일 |
| 000880 | 2026-03-31 | -27일 |
| 005830 | 2026-03-31 | -27일 |

### 결과
- **4-27 이후 공시: 0건 / 245**
- 정정공시 (한 종목 내 rcept_no 2개+): 0건
- 가장 빠듯한 종목도 20일 여유

---

## 5. 분기/반기 데이터

분기 룩어헤드 검토는 불필요:
- DART collector 코드는 `REPORT_CODES`에 annual/half/q1/q3 정의 ([dart_api.py:60-63](../collectors/dart_api.py))
- 그러나 호출부 `main.py::_collect_data`는 `report_type` 기본값(annual)만 사용
- 캐시 디렉토리: `_annual.parquet`만 존재
- DB `financial_metrics.quarter` 분포: `annual` 256건만 (분기 데이터 없음)

분기 보고서(45일 마감)는 룩어헤드 위험이 더 크지만, 시스템이 사용하지
않으므로 현 시점 조사 대상 외.

---

## 6. 발견 사항 (별도 PR 권고)

### F-1. financial_metrics에 rcept_dt 컬럼 부재 (P3)
**현 상태**: 공시일자가 캐시 parquet에만 존재. DB 단계에서 분실.

**영향**:
- 미래 시점 백테스트(예: "2026-02-15에 가용했던 데이터로 분석") 시
  rcept_dt 비교 불가 → DB만으로 룩어헤드 검증 불가
- 정정공시 시점 추적 불가 (이번엔 0건이지만 향후 발생 가능)

**권고**:
- `financial_metrics`에 `rcept_dt TEXT DEFAULT ''` 컬럼 추가
- `save_financial_metrics_batch` 시 rcept_no 첫 8자리 저장
- 백테스트 시 `WHERE rcept_dt <= :as_of_date` 필터 적용

### F-2. annual만 사용하는 정책의 명문화 (P3)
**현 상태**: REPORT_CODES dict에 분기 코드가 정의되어 있어 향후 누군가
분기 데이터를 섞어 쓸 가능성이 있음. 그러면 그 시점에 룩어헤드 위험 등장.

**권고**: docs/CLAUDE.md 또는 dart_api.py 모듈 docstring에 명시:
> "현 정책: annual 보고서만 사용. 분기 데이터 도입 시 rcept_dt 기반
> 시점 필터링 의무화."

### F-3. 신규 IPO 종목 누락 (별도 이슈)
2025 annual 캐시 245개 vs 4-28 분석 대상은 더 많음. 2025 IPO 종목은
2025 annual이 아직 작성되지 않았거나 캐시되지 않았을 가능성.
DART collector의 fallback 로직 (전년도 데이터로 폴백) 별도 검토 필요.

---

## 7. 수정 권고

**즉시 조치 없음**. 현 운영(2026-04-27 분석)은 룩어헤드 없이 정상 작동.

별도 PR 후보:
1. (P3) `financial_metrics.rcept_dt` 컬럼 추가 + 백테스트 필터 (F-1)
2. (P3) annual-only 정책 명문화 (F-2)
3. (별도 조사) 신규 IPO 종목 누락 여부 (F-3)

---

## 8. 검증 재현

```bash
cd /root/kospianal/kospi-analyzer
../venv/bin/python -c "
import pandas as pd
from pathlib import Path
files = sorted(Path('data/dart_cache').glob('*_2025_annual.parquet'))
violations = []
for fp in files:
    df = pd.read_parquet(fp)
    rns = df['rcept_no'].dropna().unique()
    if not len(rns):
        continue
    latest = max(str(r)[:8] for r in rns)
    if latest > '20260427':
        violations.append((fp.stem.split('_')[0], latest))
print(f'위반: {len(violations)} / {len(files)}')
for v in violations: print(v)
"
```

기대 출력: `위반: 0 / 245`

---

**작성**: 2026-04-28 (Claude Opus 4.7, Task 4)
**감사 대상 데이터**: `data/dart_cache/*_2025_annual.parquet` (245개)
**분석 시점**: `2026-04-27`

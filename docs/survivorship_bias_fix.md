# 생존편향 제거 로직

작성일: 2026-04-22
대상 파일:
  - `database/models.py` (수정)
  - `tests/test_survivorship.py` (신규)
관련 TODO: `[P1][DATA] 생존편향 제거`

## 문제

기존 `update_performance_tracking`에서 KIS API 현재가 조회가 실패하면
`price_cache[code] = 0` → `continue`로 그냥 넘어갔다.

결과:
  - 상장폐지된 종목은 영영 추적되지 않는다.
  - 이후 성과 분석 시 "실패한 추천이 통계에서 사라지는" 효과
    (survivorship bias)가 발생한다.
  - 평균 수익률이 과대 계상된다.

## 해결

### 1. 스키마 확장

`performance_tracking` 테이블에 세 컬럼 추가.

| 컬럼 | 타입 | 기본 | 설명 |
|-----|-----|------|------|
| is_delisted | INTEGER | 0 | 상장폐지 판정 플래그 |
| delisted_detected_at | TEXT | '' | 판정 날짜 (YYYY-MM-DD) |
| consecutive_fetch_failures | INTEGER | 0 | 연속 조회 실패 횟수 |

신규 DB는 CREATE TABLE에 포함. 기존 DB는 `_init_tables()` 내의
ALTER TABLE 블록이 try/except로 감싸져 있어, 이미 컬럼이 있으면
조용히 지나간다. 별도 마이그레이션 스크립트 불필요.

### 2. 판정 알고리즘

`update_performance_tracking` 실행 시:

1. 시작 시점에 `performance_tracking` 전체 스캔 →
   (report_date, stock_code) 별 `is_delisted`, `consecutive_fetch_failures`
   캐시 로드.
2. 루프에서 stock_code가 이미 delisted이면 API 호출 없이 skip.
3. 조회 실패 시:
   - 해당 행의 `consecutive_fetch_failures + 1` 계산.
   - **3회 이상**이면:
     - 트리거 행의 카운터를 먼저 갱신 (stub insert 포함).
     - `_cascade_mark_delisted(code)` 호출 → 같은 stock_code의 모든 행을
       `is_delisted=1`, 각 return_* = -100.0, `signal_correct`는
       strong_buy/buy면 0, sell이면 1로 재계산.
     - ERROR 로그 출력.
   - 미만이면 stub 레코드 upsert로 카운터만 갱신 (WARNING 로그).
4. 조회 성공 시: 기존 로직대로 수익률 계산 + UPSERT.
   UPSERT의 `ON CONFLICT` 절에 `consecutive_fetch_failures = 0` 리셋 추가.

판정 기준의 "연속 3회"는 **서로 다른 실행 시점**에서의 연속 실패를
의미한다. 같은 실행 안에서 재시도 3회가 아니다.

### 3. 보조 유틸리티

`Database` 클래스에 세 메서드 추가.

```python
def mark_stock_delisted(self, stock_code: str) -> int: ...
def get_delisted_stocks(self) -> list[dict]: ...
def get_fetch_failure_candidates(self, threshold: int = 3) -> list[dict]: ...
```

- `mark_stock_delisted`: 사람이 확정한 상장폐지를 수동 표시.
- `get_delisted_stocks`: 현재 delisted로 표시된 종목 고유 리스트.
- `get_fetch_failure_candidates`: `consecutive_fetch_failures >= threshold`
  종목을 리뷰 용도로 조회.

### 4. 자동 판정의 한계

3회 실패 자동 판정은 오탐 가능성이 있다.
가능한 오탐 원인:
  - KIS API 일시 장애
  - 네트워크 문제
  - 종목코드 변경 (스핀오프, 합병)
  - API 인증 토큰 만료
  - 거래정지/관리종목 지정 (상장폐지 아닌 경우에도 현재가 조회 불안정)

따라서:
  - 자동 판정은 "리뷰 대기열"로 취급.
  - 운영자는 주기적으로 `get_fetch_failure_candidates` 확인.
  - KRX 상장폐지 공시와 대조 후 실제 상장폐지만
    `mark_stock_delisted`로 "확정"하는 워크플로가 권장된다.
  - 오탐 취소가 필요하면 아래 SQL을 직접 실행:
    ```sql
    UPDATE performance_tracking
       SET is_delisted = 0,
           delisted_detected_at = '',
           consecutive_fetch_failures = 0,
           return_1w = 0, return_1m = 0, return_3m = 0,
           return_6m = 0, return_1y = 0
     WHERE stock_code = '000000';
    ```

## 테스트

`tests/test_survivorship.py` (총 5 테스트, 21 assertions).

1. `test_consecutive_failures_increments` — 실행마다 카운터 +1
2. `test_three_consecutive_failures_marks_delisted` — is_delisted=1, return_*=-100
3. `test_success_resets_failure_count` — 성공 응답 시 카운터 0
4. `test_mark_stock_delisted_updates_all_records` — 모든 행 캐스케이드
5. `test_delisted_stock_is_skipped_in_next_update` — 다음 실행에서 조회 스킵

실행:
```
python tests/test_survivorship.py
```

in-memory SQLite (`:memory:`)로 돌아가므로 운영 DB에 영향 없음.

## 운영 DB 적용 절차

1. 백업:
   ```
   cp data/kospi_analyzer.db data/kospi_analyzer.db.bak_$(date +%Y%m%d)
   ```
2. 봇 중지.
3. 코드 반영 (이미 서버에 반영됨).
4. 봇 재시작 → `Database()` 생성자에서 `_init_tables()`가 호출되어
   ALTER TABLE 세 개가 자동 실행된다. 이미 컬럼 있으면 조용히 통과.
5. 첫 `update_performance_tracking` 실행부터 새 컬럼 사용.
6. 롤백 필요 시:
   - 봇 중지.
   - 백업 DB로 `cp data/kospi_analyzer.db.bak_YYYYMMDD data/kospi_analyzer.db`.
   - 코드는 새 컬럼을 사용하지만 `ON CONFLICT` 로직은 기존 컬럼만으로도
     동작한다. 단, 새 테이블 재생성 케이스에서 ALTER 성공이 전제되어 있어
     "코드는 신버전 + DB는 구버전"은 INSERT 컬럼 리스트 불일치로 실패한다.
     롤백 시 코드도 함께 되돌려야 안전.

## 봇 재시작 안내

DB 스키마가 변경되었으니 다음 명령으로 봇을 재시작해야 한다:
```
systemctl restart kospi-bot
# 또는 현재 사용 중인 재시작 명령
```

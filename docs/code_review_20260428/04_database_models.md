# Stage 5 — Database / Models

**대상**: `database/models.py` (1702 LoC) + `database/history.py`
**날짜**: 2026-04-28

---

## 1. 스키마 정의 (11 테이블)

| 테이블 | 용도 | 인덱스 | UNIQUE | 비고 |
|--------|------|--------|--------|------|
| `analysis_results` | 일별 결과 (top_10_json) | `idx_results_date` | - | top_10/warnings/stats JSON 직렬화 |
| `stock_scores` | 종목 스코어 (당일) | `idx_scores_date`, `idx_scores_code` | `(analysis_date, stock_code)` | **23 컬럼** |
| `watchlist` | 관심종목 | - | `stock_code` | atr_multiplier 컬럼 보유 |
| `portfolio` | 보유 종목 | `idx_portfolio_code`, `idx_portfolio_active` | - | is_sold 플래그 (논리 삭제) |
| `daily_report_log` | TOP10 스냅샷 | `idx_report_log_date` | `(report_date, stock_code)` | 23 컬럼 |
| `performance_tracking` | 추천 성과 추적 | `idx_perf_date`, `idx_perf_code` | `(report_date, stock_code)` | cascade 보호 (어제 추가) |
| `financial_metrics` | DART 재무 지표 | `idx_fin_code`, `idx_fin_year` | `(stock_code, year, quarter)` | sector 컬럼 (KIS 주입) |
| `stock_master` | 종목코드↔이름 | (PK) | `stock_code` PK | UPSERT |
| `sector_averages` | 업종 평균 PER/PBR | (PK) | `sector` PK | 월요일 동적 갱신 |

### 컬럼 정합성 검증
- `stock_scores`: 23 컬럼 (어제 growth_score, quality_score 추가) ✓
- `daily_report_log`: 23 컬럼 (TOP 10 스냅샷, fair_value 3종 + 5카테고리 + per/pbr/roe + 성장률) ✓
- INSERT 22-23 placeholder 모두 일치 ✓

### DEFAULT 값
- INTEGER → `DEFAULT 0`
- TEXT → `DEFAULT ''` 또는 `DEFAULT '[]'/'{}'` (JSON)
- TIMESTAMP → `DEFAULT (datetime('now', 'localtime'))`

→ NULL 허용 컬럼 0건 (모두 default 0/'').

---

## 2. 마이그레이션 (line 291-308)

```python
for ddl in (
    "ALTER TABLE performance_tracking ADD COLUMN is_delisted ...",
    "ALTER TABLE performance_tracking ADD COLUMN delisted_detected_at ...",
    "ALTER TABLE performance_tracking ADD COLUMN consecutive_fetch_failures ...",
    "ALTER TABLE stock_scores ADD COLUMN growth_score ...",
    "ALTER TABLE stock_scores ADD COLUMN quality_score ...",
):
    try: conn.execute(ddl)
    except sqlite3.OperationalError: pass
```

### 평가
- **idempotent**: 컬럼 존재 시 OperationalError catch ✓
- **순서 의존성 없음**: 각 ALTER가 독립적 ✓
- **실패 silent**: catch만 하고 로그 X. 다른 종류 OperationalError(예: 테이블 미존재)도 묻힘 ⚠️

### 발견
**D-MIG [LOW]**: 마이그레이션 버전 관리 시스템 부재. 5개 ALTER가 hardcoded. 향후 컬럼 추가/이름 변경/타입 변경 시 추적 어려움. Alembic 도입은 과한 비용 — 가벼운 `schema_version` 테이블 또는 마이그레이션 스크립트 별도 디렉토리 권고.

**D-MIG2 [INFO]**: ALTER 실패 시 silent. `sqlite3.OperationalError`를 더 좁게 catch (e.g. `if "duplicate column name" in str(e):`)하거나 디버그 로그 추가 권고.

---

## 3. CRUD 함수 검증

### `save_stock_scores` (line 408-477) — 어제 컬럼 추가
- 23 컬럼 INSERT OR REPLACE
- 모든 placeholder `?` parameterized → **SQL 인젝션 안전 ✓**
- 종목별 try/except (한 종목 실패가 전체 사이클 막지 않음) ✓
- commit 1회 (배치) ✓

### `save_analysis_result` (line 316-355)
- `top_10_json/warnings_json/stats_json`: `json.dumps(..., ensure_ascii=False, default=str)` — 한글/날짜 안전 ✓

### `save_daily_report_log` (line 1112-1175)
- 23 컬럼 INSERT OR REPLACE, parameterized ✓
- TOP 10 스냅샷 (rank 포함). 어제 백필로 4-27/4-28 보강.
- merge 순서 결함 (이전 발견)은 **bot/formatter.py** 영역 — Stage 6에서 정밀 검토.

### `update_performance_tracking` (line 1215, 큰 메소드)
- cascade circuit-breaker (오늘 health check T2-3에서 노출)
- to_thread 격리 안전 (line 60-61 docstring) ✓

### SQL 인젝션 가능성 전수 점검
```bash
grep -nE "execute\(f\"|execute\(\".*%.*\"|executescript\(f\"" database/models.py
```
**유일 발견**:
- line 83: `execute(f"PRAGMA wal_checkpoint({mode})")` — f-string 삽입.
- 호출처: `checkpoint_wal("PASSIVE")` / `checkpoint_wal("TRUNCATE")` (코드 검색). 외부 입력 안 들어옴 → 실용적 안전.

### 발견
**S2 [INFO]**: `checkpoint_wal(mode)`에 화이트리스트 검증 추가 권고 (`mode in {"PASSIVE", "TRUNCATE", "FULL", "RESTART"}`). 향후 사용자 입력 접근 시 보호.

---

## 4. 트랜잭션 처리

### 패턴
- 모든 mutating 함수 끝에 `conn.commit()` (17건)
- 명시적 BEGIN 없음 — sqlite3 implicit transaction 사용
- 명시적 ROLLBACK 0건. 예외 시 commit 안 함 → 자동 롤백 ✓

### WAL 모드
- `PRAGMA journal_mode=WAL` (line 68) ✓
- `PRAGMA wal_autocheckpoint=1000` (line 73) — 1000 page (~4MB) 자동 체크포인트
- 사이클 종료 시 `checkpoint_wal("PASSIVE")` 추가 (main.py:404)
- 백업 직전 `checkpoint_wal("TRUNCATE")` (tools/backup_db.py)

### 발견
**T1 [INFO]**: implicit transaction 패턴은 sqlite3에서는 일반적이지만, `BEGIN IMMEDIATE`로 명시하면 동시성 충돌 빠른 감지 가능. 현재 단일 writer라 영향 없음.

---

## 5. 동시성 안전

### 동시 접근 점
- 분석 사이클 (15:40, ~3분, writer)
- health check (16:00, ~수초, **reader only**)
- auto backup (16:30, ~수초, sqlite3 backup API → reader)
- 텔레그램 봇 polling (지속, reader/writer, /portfolio 등)
- update_performance_tracking (사이클 내 to_thread, writer)

### 보호
- `check_same_thread=False` + WAL → reader 동시 가능
- writer는 직렬화 (sqlite3 자체 락)
- `_get_conn` lazy + 단일 인스턴스 (Database 객체당 1 connection)

### 발견
**C1 [INFO]**: `Database` 클래스는 인스턴스당 단일 connection 공유. 멀티 스레드/코루틴이 동일 인스턴스를 쓰면 그 connection을 공유 → sqlite3.connect의 `check_same_thread=False`로 허용되지만 statement-level lock 필요. 현재는 사이클당 직렬 실행이라 안전. 향후 동시 분석 도입 시 connection pool 필요.

---

## 6. 미사용 메소드 / 데드 코드

### 전수 grep 결과
| 메소드 | 외부 호출 | 상태 |
|--------|----------|------|
| **`update_portfolio_stock_names_from_master`** | **0건** | **DEAD** |
| `cleanup_old_data` | 1 | 사용 (스케줄러 X, 도구) |
| `clear_portfolio` | 1 | 사용 |
| `mark_stock_delisted` | 7 | 사용 |
| `get_delisted_stocks` | 2 | 사용 |
| `get_fetch_failure_candidates` | 2 | 사용 |
| `_cascade_mark_delisted` | 2 | 사용 |
| `get_op_income` | 3 | 사용 |
| `get_performance_data` | 1 | 사용 |

### 발견
**D-1 [LOW]**: `update_portfolio_stock_names_from_master` — 0 외부 호출. 31줄 메소드. 별도 PR로 제거 권고.

(이전 D-4 발견의 5개 미호출 메소드 중 4개는 그동안 사용처 추가됨. 잔존 1개만 남음.)

---

## 7. 인덱스 분석

### 현황 (10건)
- 시간 차원 (`*_date`) 5건 + 종목 차원 (`*_code`) 4건 + 활성 플래그 1건
- 모든 SELECT 쿼리가 적절한 인덱스 활용

### 평가 ✓
- 누락 의심: `daily_report_log.stock_code` (조회 패턴 있음). 그러나 `report_date` 인덱스로 1차 좁힘 + `stock_code` UNIQUE의 자동 인덱스로 보완 → 영향 미미.
- 과다 의심: 11 테이블 × 평균 1 인덱스 = 양호한 비율, 쓰기 부담 없음.

---

## 8. 데이터 무결성

### NULL vs 0 (P3 항목)
- 모든 컬럼이 `DEFAULT 0` 또는 `DEFAULT ''` — 명시적 NULL 미사용
- FCF NULL/0 구분 미반영 (P3 별도 PR)
- 영향: scorer가 0을 결손으로 가드 처리 → 실용적 안전

### FOREIGN KEY
- `PRAGMA foreign_keys=ON` 활성화 (line 69)
- 그러나 **FOREIGN KEY 정의 0건** (전수 grep)

### 발견
**D-FK [INFO]**: 관계 무결성 미적용 (예: `stock_scores.stock_code → stock_master.stock_code` 등). 도입 시 cascade delete/update 정책 결정 필요 — 회귀 위험 큼. 운영 안정 후 별도 PR 검토.

### sentinel 값
- `total_score=0` → SELL 신호로 자동 분기 (signals.py)
- `kospi_index=0` → T1-2 health check 노출 ✓
- 모든 결손 sentinel이 외부에서 감지됨

---

## 9. top_10_json 처리

### 직렬화 (`save_analysis_result` line 346-348)
```python
json.dumps(top_10, ensure_ascii=False, default=str),
```
- 한글 ✓ (ensure_ascii=False)
- datetime → str (default=str) ✓
- 키 순서 보존 (Python 3.7+ dict 보장) ✓

### 역직렬화 (`_parse_result_row` line 392-407)
- `json.loads(row["top_10_json"])` ✓
- 키 일관성: stock_code/stock_name/total_score 등 — 모든 경로에서 일관

### merge 순서 결함 (이전 발견)
- 영역: bot/formatter.py의 portfolio 종목 표시
- DB 측 영향: stock_scores INSERT가 portfolio 종목까지 포함하지 않을 수 있음 (signals만 저장)
- → **Stage 6에서 정밀 검토**

---

## 10. close 처리 + 리소스 정리

### `close()` (line 1669-1673)
```python
if self._conn:
    self._conn.close()
    self._conn = None
```
✓ 단순/안전

### main.py에서 호출
- `AnalysisPipeline.cleanup()` (line 634) → `db.close()` ✓
- `scheduled_performance_report` finally → `db.close()` ✓
- `scheduled_health_check` finally → `db.close()` ✓ (오늘 추가)

### 발견 없음.

---

## 11. 백업 일관성 (Phase 0 backup_db.py)

- WAL TRUNCATE (line 73-77) → -wal/-shm 비움
- sqlite3 backup API (line 79-86) → 다른 writer 있어도 일관 스냅샷
- 같은 날 호출 시 덮어쓰기 (`{ts}_kospi_analyzer.db`)

평가 ✓.

---

## 12. Stage 5 누적 발견

| ID | 심각도 | 내용 | 권고 |
|----|--------|------|------|
| D-1 | LOW | `update_portfolio_stock_names_from_master` 데드 메소드 | 별도 PR로 제거 |
| D-MIG | LOW | 마이그레이션 버전 관리 부재, 5건 ALTER 하드코드 | 별도 PR (가벼운 schema_version 테이블) |
| D-MIG2 | INFO | ALTER 실패 silent (어떤 OperationalError든 묻힘) | 좁은 catch 권고 |
| S2 | INFO | `checkpoint_wal(mode)` 화이트리스트 검증 권고 | 선택 |
| T1 | INFO | implicit transaction → 향후 BEGIN IMMEDIATE 도입 검토 | 선택 |
| C1 | INFO | 단일 connection 공유 (현재 안전, 동시 분석 도입 시 pool 필요) | 관찰 |
| D-FK | INFO | FOREIGN KEY 정의 0건 (foreign_keys=ON에도 무관계) | 운영 안정 후 검토 |

**CRIT 0 / HIGH 0 / MED 0**.

데이터베이스 계층은 견고. 어제 컬럼 추가, 마이그레이션 idempotency, 트랜잭션, 동시성, JSON 직렬화 모두 정합. silent fail 영역(NULL/0 구분)은 P3로 분리됐고 health check가 외부 노출 보장.

---

**다음**: Stage 6 (bot + formatter) 진행 승인 요청. **merge 순서 결함**(이전 P2)을 formatter에서 정밀 검토.

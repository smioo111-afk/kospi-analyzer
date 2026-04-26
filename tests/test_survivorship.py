"""
KOSPI Analyzer - 생존편향 제거 로직 테스트

update_performance_tracking의 실패 누적 / 자동 상장폐지 / 수동 표시 /
실패 카운터 리셋 / 상장폐지 종목 스킵을 :memory: SQLite로 검증한다.

실행: python tests/test_survivorship.py
"""

import logging
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.WARNING)

from database.models import Database


# ================================================================
# 테스트 유틸
# ================================================================
_pass = 0
_fail = 0


def _check(cond: bool, msg: str) -> None:
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS: {msg}")
    else:
        _fail += 1
        print(f"  FAIL: {msg}")


def _section(name: str) -> None:
    print(f"\n{'='*60}\n  {name}\n{'='*60}")


# ================================================================
# 공통 픽스처
# ================================================================
def _make_db() -> Database:
    """각 테스트마다 in-memory DB를 새로 만든다."""
    return Database(db_path=":memory:")


def _seed_report(
    db: Database,
    report_date: str,
    code: str,
    price: int,
    signal: str = "strong_buy",
    score: int = 80,
) -> None:
    """daily_report_log에 한 건 삽입."""
    conn = db._get_conn()
    conn.execute(
        """INSERT INTO daily_report_log
           (report_date, stock_code, stock_name, rank, total_score,
            signal, signal_label, current_price)
           VALUES (?, ?, ?, 1, ?, ?, ?, ?)""",
        (report_date, code, f"NAME_{code}", score, signal, signal, price),
    )
    conn.commit()


class FakeKIS:
    """시나리오별 현재가 응답을 프로그래밍 가능하게 한 가짜 KIS client.

    prices: {stock_code: [response_sequence]}
      각 호출마다 pop(0). 값이 정수면 성공 응답 반환, "raise"면 예외 발생.
    exception_cls: "raise" 토큰이 던질 예외 클래스 (기본 ConnectionError).
      cascade 안전장치 테스트에서 RuntimeError 등 스킵 대상을 주입할 때 사용.

    기본이 ConnectionError인 이유: 운영 환경에서 실제 KIS API 오류는
    HTTP/네트워크 류이고 RuntimeError는 환경 문제. 안전장치가 RuntimeError를
    cascade skip 대상으로 두기 때문에, "실제 상장폐지 시나리오"는
    non-RuntimeError 계열이어야 재현된다.
    """

    def __init__(
        self,
        prices: dict,
        exception_cls: type[Exception] = ConnectionError,
    ) -> None:
        self._prices = {k: list(v) for k, v in prices.items()}
        self._exception_cls = exception_cls
        self.calls: list[str] = []

    def get_stock_price(self, code: str) -> dict:
        self.calls.append(code)
        q = self._prices.get(code, [])
        if not q:
            raise self._exception_cls(f"no more responses for {code}")
        val = q.pop(0)
        if val == "raise":
            raise self._exception_cls("fake network error")
        return {"current_price": val}


def _report_date_weeks_ago(weeks: int) -> str:
    d = datetime.now() - timedelta(days=weeks * 7)
    return d.strftime("%Y-%m-%d")


# ================================================================
# 1. 실패 카운터 증가
# ================================================================
def test_consecutive_failures_increments() -> None:
    _section("1. consecutive_fetch_failures가 실행마다 증가한다")
    db = _make_db()
    report_date = _report_date_weeks_ago(3)
    _seed_report(db, report_date, "999999", 10000)

    # 3번 호출하되, 첫 2번만 검증 (3번째면 자동 상장폐지 판정 trigger)
    fake = FakeKIS({"999999": ["raise", "raise"]})

    db.update_performance_tracking(fake)
    db.update_performance_tracking(fake)

    conn = db._get_conn()
    row = conn.execute(
        """SELECT consecutive_fetch_failures, is_delisted
           FROM performance_tracking
           WHERE stock_code = '999999'""",
    ).fetchone()
    _check(row is not None, "stub 레코드가 생성되었다")
    _check(row["consecutive_fetch_failures"] == 2,
           f"2회 누적 실패 (actual={row['consecutive_fetch_failures']})")
    _check(row["is_delisted"] == 0, "아직 상장폐지 아님")


# ================================================================
# 2. 3회 연속 실패 → 자동 상장폐지
# ================================================================
def test_three_consecutive_failures_marks_delisted() -> None:
    _section("2. 3회 연속 실패 시 is_delisted=1, return_*=-100")
    db = _make_db()
    report_date = _report_date_weeks_ago(5)
    _seed_report(db, report_date, "888888", 5000, signal="strong_buy")

    fake = FakeKIS({"888888": ["raise", "raise", "raise"]})
    for _ in range(3):
        db.update_performance_tracking(fake)

    conn = db._get_conn()
    row = conn.execute(
        """SELECT is_delisted, delisted_detected_at,
                  return_1w, return_1m, return_3m, return_6m, return_1y,
                  signal_correct
           FROM performance_tracking
           WHERE stock_code = '888888'""",
    ).fetchone()
    _check(row["is_delisted"] == 1, "is_delisted=1로 전환")
    _check(row["delisted_detected_at"] != "", "delisted_detected_at 기록됨")
    _check(row["return_1w"] == -100.0, "return_1w=-100")
    _check(row["return_1m"] == -100.0, "return_1m=-100")
    _check(row["return_1y"] == -100.0, "return_1y=-100")
    _check(row["signal_correct"] == 0,
           "strong_buy 신호는 상장폐지로 오답 처리")


# ================================================================
# 3. 성공 시 실패 카운터 리셋
# ================================================================
def test_success_resets_failure_count() -> None:
    _section("3. 성공 응답이 오면 consecutive_fetch_failures=0")
    db = _make_db()
    report_date = _report_date_weeks_ago(2)
    _seed_report(db, report_date, "777777", 10000)

    # 실패 2회 → 이후 성공
    fake = FakeKIS({"777777": ["raise", "raise", 11000]})
    db.update_performance_tracking(fake)
    db.update_performance_tracking(fake)

    conn = db._get_conn()
    mid = conn.execute(
        """SELECT consecutive_fetch_failures, is_delisted
           FROM performance_tracking
           WHERE stock_code = '777777'""",
    ).fetchone()
    _check(mid["consecutive_fetch_failures"] == 2, "중간: 2회 누적")
    _check(mid["is_delisted"] == 0, "중간: 상장폐지 아님")

    db.update_performance_tracking(fake)
    after = conn.execute(
        """SELECT consecutive_fetch_failures, is_delisted,
                  price_after_1w, return_1w
           FROM performance_tracking
           WHERE stock_code = '777777'""",
    ).fetchone()
    _check(after["consecutive_fetch_failures"] == 0,
           "성공 후: 카운터 리셋")
    _check(after["is_delisted"] == 0, "상장폐지 아님")
    _check(after["price_after_1w"] == 11000, "price_after_1w 기록됨")
    _check(abs(after["return_1w"] - 10.0) < 0.01,
           f"수익률 +10% (actual={after['return_1w']})")


# ================================================================
# 4. mark_stock_delisted 수동 호출
# ================================================================
def test_mark_stock_delisted_updates_all_records() -> None:
    _section("4. mark_stock_delisted는 같은 stock_code 모든 행 갱신")
    db = _make_db()
    r1 = _report_date_weeks_ago(10)
    r2 = _report_date_weeks_ago(20)
    _seed_report(db, r1, "666666", 8000, signal="buy")
    _seed_report(db, r2, "666666", 9000, signal="strong_buy")

    # 최소 한 번의 update_performance_tracking 실행으로
    # performance_tracking 레코드 2건 생성 (성공 응답).
    fake = FakeKIS({"666666": [7500, 7500]})  # 가격 한 번만 조회됨 (캐시)
    db.update_performance_tracking(fake)

    affected = db.mark_stock_delisted("666666")
    _check(affected == 2, f"2개 행 영향 (actual={affected})")

    conn = db._get_conn()
    rows = conn.execute(
        """SELECT report_date, is_delisted, return_1w
           FROM performance_tracking
           WHERE stock_code = '666666'""",
    ).fetchall()
    _check(all(r["is_delisted"] == 1 for r in rows), "모든 행 is_delisted=1")
    _check(all(r["return_1w"] == -100.0 for r in rows),
           "모든 행 return_1w=-100")


# ================================================================
# 5. 상장폐지 종목은 다음 실행에서 스킵
# ================================================================
def test_delisted_stock_is_skipped_in_next_update() -> None:
    _section("5. is_delisted=1 종목은 다음 실행에서 조회 자체 스킵")
    db = _make_db()
    r1 = _report_date_weeks_ago(4)
    _seed_report(db, r1, "555555", 12000)

    # 먼저 자동 상장폐지 판정 유도.
    fake1 = FakeKIS({"555555": ["raise", "raise", "raise"]})
    for _ in range(3):
        db.update_performance_tracking(fake1)

    # 이후 실행 → FakeKIS를 새로 주되, 호출이 안 되어야 한다.
    fake2 = FakeKIS({"555555": [13000]})  # 있어도 호출 안 됨
    db.update_performance_tracking(fake2)
    _check(fake2.calls == [],
           f"상장폐지 종목은 조회 시도 안 됨 (실제 호출: {fake2.calls})")

    candidates = db.get_fetch_failure_candidates(threshold=3)
    _check(any(c["stock_code"] == "555555" for c in candidates),
           "get_fetch_failure_candidates에 노출")

    delisted = db.get_delisted_stocks()
    _check(any(d["stock_code"] == "555555" for d in delisted),
           "get_delisted_stocks에 노출")


# ================================================================
# 6. cascade 안전장치: RuntimeError 예외는 cascade 금지
# ================================================================
def test_cascade_skipped_on_runtime_error() -> None:
    """2026-04-24 회귀 사건 재현 방지 테스트.

    async 리팩터링 후 _run_sync가 RuntimeError를 던지는 상황을
    재현하고, cascade 안전장치가 이를 감지해 상장폐지 판정을
    하지 않는지 검증한다.
    """
    db = _make_db()
    report_date = _report_date_weeks_ago(5)
    _seed_report(db, report_date, "111111", 10000, signal="strong_buy")

    fake = FakeKIS(
        {"111111": ["raise", "raise", "raise"]},
        exception_cls=RuntimeError,
    )
    for _ in range(3):
        db.update_performance_tracking(fake)

    conn = db._get_conn()
    row = conn.execute(
        """SELECT is_delisted, return_1m, consecutive_fetch_failures
           FROM performance_tracking
           WHERE stock_code = '111111'"""
    ).fetchone()
    assert row is not None, "stub 레코드가 생성되어야"
    assert row["is_delisted"] == 0, (
        f"RuntimeError는 cascade 금지 (actual is_delisted={row['is_delisted']})"
    )
    assert row["return_1m"] != -100.0, (
        "cascade 금지 상태에서 return은 오염되지 않음"
    )
    assert row["consecutive_fetch_failures"] >= 3, (
        "카운터는 그대로 누적 (수동 조사용으로 유지)"
    )


# ================================================================
# 7. cascade 안전장치: 대형주는 화이트리스트
# ================================================================
def test_cascade_skipped_on_large_cap() -> None:
    """시총 ≥ 500B KRW 종목은 실제 API 오류라도 cascade 금지."""
    db = _make_db()
    report_date = _report_date_weeks_ago(5)
    code = "005930"  # 삼성전자 연상
    _seed_report(db, report_date, code, 70000)

    # stock_scores에 market_cap 주입 (삼성전자급 시총: 500조)
    conn = db._get_conn()
    conn.execute(
        """INSERT INTO stock_scores
           (analysis_date, stock_code, stock_name, market_cap)
           VALUES (?, ?, ?, ?)""",
        (report_date, code, "샘플대형주", 500_000_000_000_000),
    )
    conn.commit()

    fake = FakeKIS(
        {code: ["raise", "raise", "raise"]},
        exception_cls=ConnectionError,  # 진짜 API 오류
    )
    for _ in range(3):
        db.update_performance_tracking(fake)

    row = conn.execute(
        """SELECT is_delisted, return_1m, consecutive_fetch_failures
           FROM performance_tracking
           WHERE stock_code = ?""",
        (code,),
    ).fetchone()
    assert row is not None
    assert row["is_delisted"] == 0, (
        "대형주는 ConnectionError라도 cascade 금지 "
        f"(actual is_delisted={row['is_delisted']})"
    )
    assert row["return_1m"] != -100.0


# ================================================================
# 8. cascade 안전장치: 소형주 + 진짜 API 에러는 여전히 cascade
# ================================================================
def test_cascade_still_fires_for_small_cap_real_error() -> None:
    """안전장치가 과도하게 보수적이지 않은지 확인.

    스킵 리스트 예외가 아니고 시총도 작으면 기존 cascade 동작 유지.
    """
    db = _make_db()
    report_date = _report_date_weeks_ago(5)
    code = "222222"
    _seed_report(db, report_date, code, 3000)

    conn = db._get_conn()
    conn.execute(
        """INSERT INTO stock_scores
           (analysis_date, stock_code, stock_name, market_cap)
           VALUES (?, ?, ?, ?)""",
        (report_date, code, "소형주", 100_000_000_000),  # 1000억
    )
    conn.commit()

    fake = FakeKIS(
        {code: ["raise", "raise", "raise"]},
        exception_cls=ConnectionError,
    )
    for _ in range(3):
        db.update_performance_tracking(fake)

    row = conn.execute(
        """SELECT is_delisted, return_1m
           FROM performance_tracking
           WHERE stock_code = ?""",
        (code,),
    ).fetchone()
    assert row["is_delisted"] == 1, "소형주 + 실제 API 에러는 정상 cascade"
    assert row["return_1m"] == -100.0


# ================================================================
# E-2: cascade 사이클 단위 circuit-breaker
# ================================================================
def test_cascade_circuit_breaker_caps_per_cycle() -> None:
    """한 사이클에 cascade가 임계치(5)를 넘으면 그 이상은 차단."""
    from database.models import _CASCADE_PER_CYCLE_LIMIT
    db = _make_db()
    report_date = _report_date_weeks_ago(5)
    # 7개 소형주 — 모두 ConnectionError로 cascade 후보
    codes = [f"7770{i:02d}" for i in range(7)]
    conn = db._get_conn()
    for code in codes:
        _seed_report(db, report_date, code, 1000)
        conn.execute(
            """INSERT INTO stock_scores
               (analysis_date, stock_code, stock_name, market_cap)
               VALUES (?, ?, ?, ?)""",
            (report_date, code, f"소형주{code}", 50_000_000_000),  # 500억
        )
    conn.commit()

    fake = FakeKIS(
        {c: ["raise", "raise", "raise"] for c in codes},
        exception_cls=ConnectionError,
    )
    for _ in range(3):
        db.update_performance_tracking(fake)

    # is_delisted=1로 마킹된 종목 수 = 임계치 이하여야
    rows = conn.execute(
        """SELECT stock_code, is_delisted FROM performance_tracking
           WHERE report_date = ?""",
        (report_date,),
    ).fetchall()
    delisted = sum(1 for r in rows if r["is_delisted"])
    assert delisted <= _CASCADE_PER_CYCLE_LIMIT, (
        f"circuit-breaker 미동작: {delisted}건 cascade "
        f"(한도 {_CASCADE_PER_CYCLE_LIMIT})"
    )
    # 적어도 한도까지는 발화해야
    assert delisted >= _CASCADE_PER_CYCLE_LIMIT, (
        f"cascade가 한도까지 동작 안 함: {delisted}건"
    )


# ================================================================
# 메인
# ================================================================
def main() -> int:
    test_consecutive_failures_increments()
    test_three_consecutive_failures_marks_delisted()
    test_success_resets_failure_count()
    test_mark_stock_delisted_updates_all_records()
    test_delisted_stock_is_skipped_in_next_update()
    test_cascade_skipped_on_runtime_error()
    test_cascade_skipped_on_large_cap()
    test_cascade_still_fires_for_small_cap_real_error()
    test_cascade_circuit_breaker_caps_per_cycle()

    print(f"\n{'='*60}")
    print(f"결과: PASS={_pass} FAIL={_fail}")
    print(f"{'='*60}")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

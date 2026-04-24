"""tools/recover_performance_tracking.py 단위 테스트.

오염된 performance_tracking 레코드를 KIS 생존 프로브로 판정해
복구 대상/상폐 유지로 분리하는 로직을 검증한다.

실행: pytest tests/test_recover_performance.py -v

핵심 시나리오:
  1) dry-run은 DB를 건드리지 않는다.
  2) apply는 "확정 생존" 종목의 모든 is_delisted=1 행을 미계산 상태로 되돌린다.
  3) "확정 불가" 종목(API 실패 + 소형주)은 그대로 상폐 유지.
  4) API 실패라도 시총 >= 500B KRW인 대형주는 large-cap override로 복구.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

import pytest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from database.models import Database
from tools import recover_performance_tracking as tool


# =========================================================
# 픽스처: FakeKISClient
# =========================================================
class FakeKISClient:
    """KISClient async-context-manager 인터페이스를 흉내낸다.

    responses: {stock_code: value}
      - int > 0  → aget_stock_price가 {"current_price": int} 반환
      - 0        → 조회는 성공하되 current_price=0 (= "확정 불가")
      - "raise"  → ConnectionError 발생 (= API 오류, 확정 불가)
    """

    def __init__(self, responses: dict) -> None:
        self._responses = responses
        self.calls: list[str] = []

    async def __aenter__(self) -> "FakeKISClient":
        return self

    async def __aexit__(self, *a) -> None:
        pass

    async def aget_stock_price(self, code: str) -> dict:
        self.calls.append(code)
        v = self._responses.get(code, "raise")
        if v == "raise":
            raise ConnectionError(f"mock: {code} unreachable")
        return {"current_price": int(v)}


def _install_fake_kis(monkeypatch, responses: dict) -> FakeKISClient:
    fake = FakeKISClient(responses)

    def _factory() -> FakeKISClient:
        return fake

    monkeypatch.setattr(tool, "KISClient", _factory)
    return fake


# =========================================================
# 픽스처: 시드 DB
# =========================================================
def _seed_delisted_stock(
    db: Database,
    code: str,
    name: str,
    market_cap: int = 0,
    rows: int = 3,
) -> None:
    """is_delisted=1 오염 행 N개 + 옵션으로 stock_scores 시총 주입."""
    conn = db._get_conn()
    for i in range(rows):
        conn.execute(
            """INSERT INTO performance_tracking
               (report_date, stock_code, stock_name,
                signal_at_report, score_at_report, price_at_report,
                return_1w, return_1m, return_3m, return_6m, return_1y,
                is_delisted, delisted_detected_at,
                consecutive_fetch_failures, last_updated,
                signal_correct)
               VALUES (?, ?, ?, 'strong_buy', 80, 10000,
                       -100, -100, -100, -100, -100,
                       1, '2026-04-24', 3, '2026-04-24', 0)""",
            (f"2026-01-{i+1:02d}", code, name),
        )
    if market_cap > 0:
        conn.execute(
            """INSERT INTO stock_scores
               (analysis_date, stock_code, stock_name, market_cap)
               VALUES (?, ?, ?, ?)""",
            ("2026-04-24", code, name, market_cap),
        )
    conn.commit()


def _count_delisted(db: Database, code: Optional[str] = None) -> int:
    conn = db._get_conn()
    if code is not None:
        r = conn.execute(
            """SELECT COUNT(*) AS n FROM performance_tracking
               WHERE is_delisted = 1 AND stock_code = ?""",
            (code,),
        ).fetchone()
    else:
        r = conn.execute(
            """SELECT COUNT(*) AS n FROM performance_tracking
               WHERE is_delisted = 1""",
        ).fetchone()
    return r["n"]


@pytest.fixture
def db() -> Database:
    return Database(db_path=":memory:")


# =========================================================
# 1) dry-run은 DB를 건드리지 않는다
# =========================================================
def test_dry_run_no_changes(db, monkeypatch, capsys):
    _seed_delisted_stock(db, "005930", "삼성전자", market_cap=0, rows=5)
    _seed_delisted_stock(db, "999999", "좀비", market_cap=0, rows=2)

    # 005930는 생존 응답, 999999는 API 실패 → 하지만 dry-run이라 DB 변경 X
    _install_fake_kis(
        monkeypatch, {"005930": 70000, "999999": "raise"},
    )
    monkeypatch.setattr(tool, "Database", lambda db_path=None: db)

    import asyncio
    rc = asyncio.run(tool._amain(apply=False, db_path=None))
    assert rc == 0

    assert _count_delisted(db, "005930") == 5, (
        "dry-run은 005930 레코드를 복구하지 않아야"
    )
    assert _count_delisted(db, "999999") == 2
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "005930" in out


# =========================================================
# 2) apply는 확정 생존 종목을 미계산 상태로 되돌린다
# =========================================================
def test_apply_restores_confirmed_alive_stocks(db, monkeypatch):
    _seed_delisted_stock(db, "005930", "삼성전자", rows=4)

    _install_fake_kis(monkeypatch, {"005930": 70000})
    monkeypatch.setattr(tool, "Database", lambda db_path=None: db)

    import asyncio
    rc = asyncio.run(tool._amain(apply=True, db_path=None))
    assert rc == 0

    conn = db._get_conn()
    rows = conn.execute(
        """SELECT is_delisted, delisted_detected_at,
                  consecutive_fetch_failures,
                  return_1w, return_1m, return_3m, return_6m, return_1y,
                  price_after_1w, signal_correct, last_updated
           FROM performance_tracking WHERE stock_code = '005930'""",
    ).fetchall()
    assert len(rows) == 4
    for r in rows:
        assert r["is_delisted"] == 0, "복구 후 is_delisted=0"
        assert r["delisted_detected_at"] == ""
        assert r["consecutive_fetch_failures"] == 0
        # return_*는 미계산 상태(=0)로 리셋
        for k in ("return_1w", "return_1m", "return_3m",
                  "return_6m", "return_1y"):
            assert r[k] == 0.0, f"{k} reset to 0"
        assert r["price_after_1w"] == 0
        assert r["signal_correct"] == 0
        assert r["last_updated"] == ""


# =========================================================
# 3) 확정 불가 (API 실패 + 소형주)는 상폐 유지
# =========================================================
def test_apply_keeps_truly_delisted_stocks(db, monkeypatch):
    # 소형주 + API 조회 실패 → "확정 불가", 복구 안 됨
    _seed_delisted_stock(
        db, "000001", "작은회사",
        market_cap=100_000_000_000,  # 1000억, 임계값 미달
        rows=3,
    )

    _install_fake_kis(monkeypatch, {"000001": "raise"})
    monkeypatch.setattr(tool, "Database", lambda db_path=None: db)

    import asyncio
    rc = asyncio.run(tool._amain(apply=True, db_path=None))
    assert rc == 0

    assert _count_delisted(db, "000001") == 3, (
        "확정 불가 종목은 상폐 유지"
    )
    conn = db._get_conn()
    row = conn.execute(
        """SELECT return_1w FROM performance_tracking
           WHERE stock_code = '000001' LIMIT 1""",
    ).fetchone()
    assert row["return_1w"] == -100.0


# =========================================================
# 4) 대형주는 API 실패라도 large-cap override로 복구
# =========================================================
def test_large_cap_always_restored(db, monkeypatch):
    # 시총 500조(삼성전자급). API는 실패하지만 대형주 화이트리스트로 복구.
    _seed_delisted_stock(
        db, "005930", "삼성전자",
        market_cap=500_000_000_000_000,
        rows=5,
    )

    _install_fake_kis(monkeypatch, {"005930": "raise"})
    monkeypatch.setattr(tool, "Database", lambda db_path=None: db)

    import asyncio
    rc = asyncio.run(tool._amain(apply=True, db_path=None))
    assert rc == 0

    assert _count_delisted(db, "005930") == 0, (
        "대형주는 API 실패라도 복구 대상"
    )


# =========================================================
# 5) 확정 불가 + 확정 생존 혼합: 선별적으로 복구
# =========================================================
def test_mixed_batch_handles_each_stock_independently(db, monkeypatch):
    _seed_delisted_stock(db, "005930", "삼성전자", rows=3)
    _seed_delisted_stock(
        db, "000001", "좀비소형주",
        market_cap=50_000_000_000, rows=2,
    )

    _install_fake_kis(monkeypatch, {
        "005930": 70000,
        "000001": "raise",
    })
    monkeypatch.setattr(tool, "Database", lambda db_path=None: db)

    import asyncio
    rc = asyncio.run(tool._amain(apply=True, db_path=None))
    assert rc == 0

    assert _count_delisted(db, "005930") == 0, "생존 확정 종목 복구"
    assert _count_delisted(db, "000001") == 2, "확정 불가 종목은 유지"


# =========================================================
# 6) restore_stock은 다른 종목을 건드리지 않는다
# =========================================================
def test_restore_stock_scoped_to_code(db):
    _seed_delisted_stock(db, "AAA", "a", rows=2)
    _seed_delisted_stock(db, "BBB", "b", rows=3)

    affected = tool.restore_stock(db, "AAA")
    db._get_conn().commit()

    assert affected == 2
    assert _count_delisted(db, "AAA") == 0
    assert _count_delisted(db, "BBB") == 3

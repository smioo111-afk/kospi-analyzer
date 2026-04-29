"""A1 Phase 4: disclosure_impacts 테이블 + CRUD 회귀 테스트."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.disclosure_impact import (  # noqa: E402
    DisclosureImpact,
    ScoreSnapshot,
)
from collectors.dart_disclosure import Disclosure  # noqa: E402
from database.models import (  # noqa: E402
    Database,
    _disclosure_impact_from_json,
    _disclosure_impact_to_dict,
)


def _disc(stock_code="004800", report_nm="[기재정정]사업보고서") -> Disclosure:
    return Disclosure(
        rcept_no="20260429001",
        corp_code="00111111",
        stock_code=stock_code,
        corp_name="효성",
        report_nm=report_nm,
        rcept_dt="20260429",
        rm="정",
    )


def _impact(code="004800", before_total=55, after_total=62,
            signal_changed=False) -> DisclosureImpact:
    before = ScoreSnapshot(
        stock_code=code, stock_name="효성",
        total_score=before_total, value_score=15, financial_score=10,
        growth_score=10, momentum_score=12, quality_score=8,
        signal="hold",
    )
    after = ScoreSnapshot(
        stock_code=code, stock_name="효성",
        total_score=after_total, value_score=18, financial_score=12,
        growth_score=12, momentum_score=12, quality_score=8,
        signal="buy" if signal_changed else "hold",
    )
    return DisclosureImpact(
        disclosure=_disc(code),
        stock_code=code,
        before=before, after=after,
        total_diff=after_total - before_total,
        value_diff=3, financial_diff=2, growth_diff=2,
        momentum_diff=0, quality_diff=0,
        signal_changed=signal_changed,
    )


# ----------------------------------------------------------------------
# 스키마
# ----------------------------------------------------------------------
def test_table_created_on_init(tmp_path):
    db = Database(db_path=str(tmp_path / "fresh.db"))
    try:
        cols = {
            r["name"] for r in db._get_conn().execute(
                "PRAGMA table_info(disclosure_impacts)"
            ).fetchall()
        }
    finally:
        db.close()
    expected = {
        "id", "analysis_date", "stock_code", "rcept_no",
        "report_nm", "rcept_dt",
        "before_total", "after_total",
        "before_signal", "after_signal",
        "impact_json", "created_at",
    }
    assert expected.issubset(cols)


def test_indexes_created(tmp_path):
    db = Database(db_path=str(tmp_path / "idx.db"))
    try:
        rows = db._get_conn().execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='disclosure_impacts'"
        ).fetchall()
    finally:
        db.close()
    names = {r["name"] for r in rows}
    assert "idx_disclosure_impacts_date" in names
    assert "idx_disclosure_impacts_stock" in names


def test_migration_idempotent(tmp_path):
    """같은 DB 두 번 init해도 OperationalError 없이 통과."""
    p = tmp_path / "idem.db"
    Database(db_path=str(p)).close()
    Database(db_path=str(p)).close()  # 두 번째 init도 안전
    db = Database(db_path=str(p))
    try:
        rows = db._get_conn().execute(
            "PRAGMA table_info(disclosure_impacts)"
        ).fetchall()
        assert len(rows) > 0
    finally:
        db.close()


# ----------------------------------------------------------------------
# CRUD
# ----------------------------------------------------------------------
def test_save_single_impact(tmp_path):
    db = Database(db_path=str(tmp_path / "single.db"))
    try:
        n = db.save_disclosure_impact("2026-04-29", _impact())
        assert n == 1
        rows = db._get_conn().execute(
            "SELECT * FROM disclosure_impacts WHERE analysis_date='2026-04-29'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["stock_code"] == "004800"
        assert rows[0]["rcept_no"] == "20260429001"
        assert rows[0]["before_total"] == 55
        assert rows[0]["after_total"] == 62
    finally:
        db.close()


def test_save_batch_impacts(tmp_path):
    db = Database(db_path=str(tmp_path / "batch.db"))
    try:
        imps = [
            _impact("004800", 55, 62),
            _impact("005930", 70, 72),
            _impact("000270", 60, 60),
        ]
        n = db.save_disclosure_impacts_batch("2026-04-29", imps)
        assert n == 3
        cnt = db._get_conn().execute(
            "SELECT COUNT(*) c FROM disclosure_impacts "
            "WHERE analysis_date='2026-04-29'"
        ).fetchone()["c"]
        assert cnt == 3
    finally:
        db.close()


def test_save_batch_empty_returns_zero(tmp_path):
    db = Database(db_path=str(tmp_path / "empty.db"))
    try:
        n = db.save_disclosure_impacts_batch("2026-04-29", [])
        assert n == 0
    finally:
        db.close()


def test_get_impacts_by_date_returns_objects(tmp_path):
    db = Database(db_path=str(tmp_path / "by_date.db"))
    try:
        db.save_disclosure_impacts_batch("2026-04-29", [
            _impact("004800", 55, 62),
            _impact("005930", 70, 72),
        ])
        # 다른 날짜 행도 추가 (필터 검증용)
        db.save_disclosure_impact("2026-04-28", _impact("000270", 60, 65))

        out = db.get_disclosure_impacts("2026-04-29")
        assert len(out) == 2
        codes = {imp.stock_code for imp in out}
        assert codes == {"004800", "005930"}
    finally:
        db.close()


def test_get_impacts_empty_returns_empty_list(tmp_path):
    db = Database(db_path=str(tmp_path / "noop.db"))
    try:
        assert db.get_disclosure_impacts("2026-04-29") == []
    finally:
        db.close()


def test_get_impacts_for_stock_with_limit(tmp_path):
    db = Database(db_path=str(tmp_path / "stock.db"))
    try:
        # 같은 종목 5건 (날짜 다름)
        for i in range(5):
            db.save_disclosure_impact(
                f"2026-04-{20 + i:02d}", _impact("004800", 50 + i, 55),
            )
        # 다른 종목 1건
        db.save_disclosure_impact("2026-04-29", _impact("005930", 70, 72))

        out = db.get_disclosure_impacts_for_stock("004800", limit=3)
        assert len(out) == 3
        assert all(imp.stock_code == "004800" for imp in out)
    finally:
        db.close()


# ----------------------------------------------------------------------
# 직렬화 round-trip
# ----------------------------------------------------------------------
def test_serialization_roundtrip_preserves_all_fields():
    imp = _impact("004800", 55, 62, signal_changed=True)
    d = _disclosure_impact_to_dict(imp)
    import json
    s = json.dumps(d, ensure_ascii=False)
    restored = _disclosure_impact_from_json(s)

    assert restored.stock_code == imp.stock_code
    assert restored.disclosure.rcept_no == imp.disclosure.rcept_no
    assert restored.disclosure.report_nm == imp.disclosure.report_nm
    assert restored.before.total_score == imp.before.total_score
    assert restored.after.total_score == imp.after.total_score
    assert restored.total_diff == imp.total_diff
    assert restored.signal_changed is True


def test_get_impacts_ignores_invalid_json_rows(tmp_path):
    """impact_json이 손상된 행은 건너뛰지 않고 raise (현 정책 명시)."""
    db = Database(db_path=str(tmp_path / "bad.db"))
    try:
        # 정상 1건 + 손상 1건
        db.save_disclosure_impact("2026-04-29", _impact())
        conn = db._get_conn()
        conn.execute(
            "INSERT INTO disclosure_impacts "
            "(analysis_date, stock_code, rcept_no, impact_json) "
            "VALUES (?, ?, ?, ?)",
            ("2026-04-29", "BAD", "X", "not-valid-json"),
        )
        conn.commit()
        # 손상 행에서 json.loads 실패 → ValueError raise
        with pytest.raises(Exception):
            db.get_disclosure_impacts("2026-04-29")
    finally:
        db.close()


# ----------------------------------------------------------------------
# 인덱스 동작 검증 (큰 부하는 아니지만 스캔 회피 의도 확인)
# ----------------------------------------------------------------------
def test_query_by_date_uses_index(tmp_path):
    db = Database(db_path=str(tmp_path / "explain.db"))
    try:
        plan = db._get_conn().execute(
            "EXPLAIN QUERY PLAN SELECT * FROM disclosure_impacts "
            "WHERE analysis_date = ?", ("2026-04-29",),
        ).fetchall()
        plan_str = " ".join(str(r["detail"]) for r in plan)
        assert "idx_disclosure_impacts_date" in plan_str
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

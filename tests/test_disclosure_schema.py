"""A1 Phase 0: financial_metrics rcept_no/rcept_dt 마이그레이션 회귀 테스트."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.dart_api import DARTClient  # noqa: E402
from database.models import Database  # noqa: E402


def test_rcept_columns_exist_on_fresh_db(tmp_path):
    """신규 DB에 rcept_no/rcept_dt 컬럼이 생성된다."""
    db = Database(db_path=str(tmp_path / "fresh.db"))
    try:
        cols = {
            r["name"] for r in db._get_conn().execute(
                "PRAGMA table_info(financial_metrics)"
            ).fetchall()
        }
    finally:
        db.close()
    assert "rcept_no" in cols
    assert "rcept_dt" in cols


def test_rcept_columns_default_empty(tmp_path):
    """rcept_no/rcept_dt 미주입 시 빈 문자열로 저장된다."""
    db = Database(db_path=str(tmp_path / "default.db"))
    try:
        db.save_financial_metrics({
            "stock_code": "005930",
            "year": 2025,
            "quarter": "annual",
            "revenue": 100,
        })
        row = db._get_conn().execute(
            "SELECT rcept_no, rcept_dt FROM financial_metrics "
            "WHERE stock_code='005930'"
        ).fetchone()
    finally:
        db.close()
    assert row["rcept_no"] == ""
    assert row["rcept_dt"] == ""


def test_save_with_rcept_data(tmp_path):
    """rcept_no/rcept_dt를 명시 주입하면 그대로 저장된다."""
    db = Database(db_path=str(tmp_path / "with_rcept.db"))
    try:
        db.save_financial_metrics({
            "stock_code": "004800",
            "year": 2025,
            "quarter": "annual",
            "rcept_no": "20260312001236",
            "rcept_dt": "20260312",
        })
        row = db._get_conn().execute(
            "SELECT rcept_no, rcept_dt FROM financial_metrics "
            "WHERE stock_code='004800'"
        ).fetchone()
    finally:
        db.close()
    assert row["rcept_no"] == "20260312001236"
    assert row["rcept_dt"] == "20260312"


def test_migration_idempotent(tmp_path):
    """기존 DB에 컬럼 없는 상태로 두 번 init해도 OperationalError 안남."""
    path = tmp_path / "legacy.db"
    # 1차: 컬럼 있는 신규 스키마
    db1 = Database(db_path=str(path))
    db1.close()
    # 2차: 같은 DB 재오픈 — ALTER가 다시 실행되어도 silent pass
    db2 = Database(db_path=str(path))
    try:
        cols = {
            r["name"] for r in db2._get_conn().execute(
                "PRAGMA table_info(financial_metrics)"
            ).fetchall()
        }
    finally:
        db2.close()
    assert "rcept_no" in cols
    assert "rcept_dt" in cols


def test_migration_adds_columns_to_pre_a1_db(tmp_path):
    """A1 이전에 만들어진 DB(rcept 컬럼 없음)도 init 시 자동 보강."""
    path = tmp_path / "preA1.db"
    # rcept 컬럼이 없는 구버전 스키마 수동 생성
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE financial_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            year INTEGER NOT NULL,
            quarter TEXT DEFAULT 'annual',
            revenue INTEGER DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )"""
    )
    conn.commit()
    conn.close()

    # Database 초기화 — 마이그레이션 ALTER가 실행되어 컬럼이 추가돼야
    db = Database(db_path=str(path))
    try:
        cols = {
            r["name"] for r in db._get_conn().execute(
                "PRAGMA table_info(financial_metrics)"
            ).fetchall()
        }
    finally:
        db.close()
    assert "rcept_no" in cols
    assert "rcept_dt" in cols


# ----------------------------------------------------------------------
# DART → DB 매핑
# ----------------------------------------------------------------------
def test_extract_financial_metrics_attaches_rcept_no(tmp_path, monkeypatch):
    """DART parquet의 rcept_no가 metrics dict에 들어간다."""
    df = pd.DataFrame([
        {
            "sj_div": "IS",
            "account_nm": "매출액",
            "thstrm_amount": "1000000",
            "rcept_no": "20260312001236",
            "bsns_year": 2025,
        }
    ])
    client = DARTClient()
    monkeypatch.setattr(
        client, "get_financial_statements",
        lambda code, year: df if year == 2025 else None,
    )
    metrics = client.extract_financial_metrics("004800", year=2025)
    assert metrics.get("rcept_no") == "20260312001236"
    assert metrics.get("rcept_dt") == "20260312"


def test_extract_financial_metrics_no_rcept_column(tmp_path, monkeypatch):
    """rcept_no 컬럼 자체가 없는 (구) parquet에서도 안전하게 동작."""
    df = pd.DataFrame([
        {"sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "1000000"},
    ])
    client = DARTClient()
    monkeypatch.setattr(
        client, "get_financial_statements",
        lambda code, year: df if year == 2025 else None,
    )
    metrics = client.extract_financial_metrics("004800", year=2025)
    # rcept_no는 없거나 빈 값
    assert metrics.get("rcept_no", "") == ""
    assert metrics.get("rcept_dt", "") == ""


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

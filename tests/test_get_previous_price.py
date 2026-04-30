"""Tests for Database.get_previous_price (포트폴리오 전일 대비 표시용 헬퍼)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models import Database  # noqa: E402


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "prev_price.db"
    database = Database(db_path=str(db_path))
    yield database
    database.close()


def _insert_score(db: Database, code: str, date: str, price: int) -> None:
    conn = db._get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO stock_scores
           (analysis_date, stock_code, current_price)
           VALUES (?, ?, ?)""",
        (date, code, price),
    )
    conn.commit()


def _insert_report_log(db: Database, code: str, date: str, price: int) -> None:
    conn = db._get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO daily_report_log
           (report_date, stock_code, current_price)
           VALUES (?, ?, ?)""",
        (date, code, price),
    )
    conn.commit()


def test_get_previous_price_returns_yesterday_from_stock_scores(db):
    _insert_score(db, "005930", "2026-04-29", 70000)
    _insert_score(db, "005930", "2026-04-30", 71000)
    # 오늘(2026-05-01) 보다 작은 날짜 중 가장 최근의 가격을 반환해야 함
    assert db.get_previous_price("005930") == 71000


def test_get_previous_price_handles_weekend_gap(db):
    # 금요일까지만 데이터 있음 (월요일 호출 가정)
    _insert_score(db, "005930", "2026-04-24", 65000)  # 금요일
    _insert_score(db, "005930", "2026-04-23", 64000)
    # 가장 최근 데이터를 반환
    assert db.get_previous_price("005930") == 65000


def test_get_previous_price_returns_zero_if_missing(db):
    assert db.get_previous_price("999999") == 0


def test_get_previous_price_skips_today_data(db):
    # 오늘자 데이터가 있어도 무시 (전일 비교용이므로)
    _insert_score(db, "005930", "2026-05-01", 72000)  # 오늘
    _insert_score(db, "005930", "2026-04-30", 71000)
    assert db.get_previous_price("005930") == 71000


def test_get_previous_price_falls_back_to_report_log(db):
    # stock_scores에 데이터 없을 때 daily_report_log에서 조회
    _insert_report_log(db, "005930", "2026-04-30", 71500)
    assert db.get_previous_price("005930") == 71500


def test_get_previous_price_prefers_stock_scores_over_report_log(db):
    # 두 테이블 모두 있으면 stock_scores 우선
    _insert_score(db, "005930", "2026-04-30", 71000)
    _insert_report_log(db, "005930", "2026-04-30", 99999)
    assert db.get_previous_price("005930") == 71000


def test_get_previous_price_skips_zero_prices(db):
    # current_price=0 행은 무시하고 그 이전 유효한 가격을 반환
    _insert_score(db, "005930", "2026-04-30", 0)
    _insert_score(db, "005930", "2026-04-29", 70000)
    assert db.get_previous_price("005930") == 70000

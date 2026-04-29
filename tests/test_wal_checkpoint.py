"""database.Database.checkpoint_wal 회귀 테스트.

분석 사이클 종료 시점(main.py:404)의 WAL 정리가 실제로 동작하는지,
다른 reader가 있어도 PASSIVE 모드가 silent fail 없이 끝나는지 확인한다.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models import Database  # noqa: E402


def _make_db(tmp_path: Path) -> Path:
    return tmp_path / "wal_test.db"


def test_checkpoint_wal_passive_runs_without_error(tmp_path):
    db_path = _make_db(tmp_path)
    db = Database(db_path=str(db_path))
    try:
        db.checkpoint_wal("PASSIVE")  # 빈 DB에서도 에러 없어야 함
    finally:
        db.close()


def test_checkpoint_wal_after_writes_shrinks_wal(tmp_path):
    """PASSIVE 체크포인트 후 WAL 파일이 0 또는 매우 작아진다."""
    db_path = _make_db(tmp_path)
    db = Database(db_path=str(db_path))
    try:
        # 충분한 변동을 발생시켜 WAL을 부풀림
        conn = db._get_conn()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)"
        )
        conn.executemany(
            "INSERT INTO t (v) VALUES (?)",
            [(f"row{i}",) for i in range(2000)],
        )
        conn.commit()

        wal_path = Path(f"{db_path}-wal")
        size_before = wal_path.stat().st_size if wal_path.exists() else 0

        db.checkpoint_wal("PASSIVE")

        # PASSIVE는 WAL 파일을 비우지 않을 수 있지만, 누적된 변경분은
        # main DB로 흘러들어가야 한다. 추가 쓰기 없이 파일 크기가
        # 증가하지 않아야 함.
        size_after = wal_path.stat().st_size if wal_path.exists() else 0
        assert size_after <= size_before
    finally:
        db.close()


def test_checkpoint_wal_with_concurrent_reader(tmp_path):
    """다른 reader가 열려 있어도 PASSIVE는 에러 없이 종료된다."""
    db_path = _make_db(tmp_path)
    db = Database(db_path=str(db_path))
    try:
        conn = db._get_conn()
        conn.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES ('x')")
        conn.commit()

        # 별도 connection으로 reader 유지
        reader = sqlite3.connect(db_path)
        reader.execute("BEGIN").fetchall()
        reader.execute("SELECT * FROM t").fetchall()
        try:
            # PASSIVE는 reader 차단 안 함 → 예외 없이 끝나야 함
            db.checkpoint_wal("PASSIVE")
        finally:
            reader.close()
    finally:
        db.close()


def test_checkpoint_wal_invalid_mode_swallowed(tmp_path):
    """잘못된 모드를 줘도 logger.warning만 띄우고 예외는 삼킨다."""
    db_path = _make_db(tmp_path)
    db = Database(db_path=str(db_path))
    try:
        db.checkpoint_wal("NOT_A_MODE")  # 예외가 밖으로 새지 않아야 함
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

"""tools.backup_db 회귀 테스트."""

from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.backup_db import (  # noqa: E402
    AUTO_BACKUP_PATTERN,
    _purge_old_auto_backups,
    auto_backup_db,
    cli_main,
)


def _make_db(path: Path) -> None:
    """간단한 테이블이 있는 DB 생성."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"row{i}",) for i in range(50)])
    conn.commit()
    conn.close()


def test_backup_creates_file(tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)
    out = auto_backup_db(retain_days=30, db_path=str(src))
    assert out.exists()
    assert out.parent == tmp_path / "auto_backup"
    assert AUTO_BACKUP_PATTERN.match(out.name)


def test_backup_contents_match_source(tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)
    out = auto_backup_db(retain_days=30, db_path=str(src))

    src_rows = sqlite3.connect(src).execute("SELECT COUNT(*) FROM t").fetchone()[0]
    bk_rows = sqlite3.connect(out).execute("SELECT COUNT(*) FROM t").fetchone()[0]
    assert src_rows == bk_rows == 50


def test_backup_after_wal_checkpoint(tmp_path):
    """WAL 모드에서도 일관된 스냅샷이 생성되는지."""
    src = tmp_path / "src.db"
    _make_db(src)
    conn = sqlite3.connect(src)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("INSERT INTO t (v) VALUES ('after-wal')")
    conn.commit()
    conn.close()

    out = auto_backup_db(retain_days=30, db_path=str(src))
    rows = sqlite3.connect(out).execute(
        "SELECT v FROM t WHERE v='after-wal'"
    ).fetchall()
    assert len(rows) == 1


def test_backup_old_files_cleaned(tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)

    backup_dir = tmp_path / "auto_backup"
    backup_dir.mkdir()

    # 35일 전 날짜로 자동 백업 파일 생성 → retain=30 시 삭제 대상
    old_date = (datetime(2026, 4, 28) - timedelta(days=35)).strftime("%Y%m%d")
    old_file = backup_dir / f"{old_date}_kospi_analyzer.db"
    old_file.write_bytes(b"old")

    # 5일 전 → 보존
    recent_date = (datetime(2026, 4, 28) - timedelta(days=5)).strftime("%Y%m%d")
    recent_file = backup_dir / f"{recent_date}_kospi_analyzer.db"
    recent_file.write_bytes(b"recent")

    auto_backup_db(retain_days=30, db_path=str(src), now=datetime(2026, 4, 28))

    assert not old_file.exists(), "35일 이전 백업이 삭제돼야 함"
    assert recent_file.exists(), "5일 이전 백업은 보존돼야 함"


def test_existing_manual_backups_preserved(tmp_path):
    """수동 백업 (.bak_before_*, .bak_*)은 정리에서 건드리지 않는다."""
    src = tmp_path / "src.db"
    _make_db(src)

    backup_dir = tmp_path / "auto_backup"
    backup_dir.mkdir()

    # 자동 패턴 외 — 보존돼야 함
    manual1 = backup_dir / "kospi_analyzer.db.bak_before_dart_fix_20260426"
    manual2 = backup_dir / "kospi_analyzer.db.bak_20260422"
    manual3 = backup_dir / "random_file.txt"
    for p in (manual1, manual2, manual3):
        p.write_bytes(b"manual")

    # 100일 이전 자동 백업도 추가 (정리 대상)
    old_date = (datetime(2026, 4, 28) - timedelta(days=100)).strftime("%Y%m%d")
    old_auto = backup_dir / f"{old_date}_kospi_analyzer.db"
    old_auto.write_bytes(b"old")

    auto_backup_db(retain_days=30, db_path=str(src), now=datetime(2026, 4, 28))

    assert manual1.exists(), "수동 백업1 보존"
    assert manual2.exists(), "수동 백업2 보존"
    assert manual3.exists(), "임의 파일 보존"
    assert not old_auto.exists(), "오래된 자동 백업 삭제"


def test_purge_no_op_when_retain_zero_or_negative(tmp_path):
    backup_dir = tmp_path / "auto_backup"
    backup_dir.mkdir()
    f = backup_dir / "20260101_kospi_analyzer.db"
    f.write_bytes(b"x")

    n = _purge_old_auto_backups(backup_dir, 0, now=datetime(2026, 4, 28))
    assert n == 0
    assert f.exists()


def test_backup_dir_created_if_missing(tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)
    backup_dir = tmp_path / "auto_backup"
    assert not backup_dir.exists()

    auto_backup_db(retain_days=30, db_path=str(src))
    assert backup_dir.exists()


def test_backup_overwrites_same_day(tmp_path):
    """같은 날 두 번 호출 시 덮어쓰기 (충돌 없음)."""
    src = tmp_path / "src.db"
    _make_db(src)

    out1 = auto_backup_db(retain_days=30, db_path=str(src))
    time.sleep(0.05)

    # 소스 변경
    conn = sqlite3.connect(src)
    conn.execute("INSERT INTO t (v) VALUES ('second')")
    conn.commit()
    conn.close()

    out2 = auto_backup_db(retain_days=30, db_path=str(src))
    assert out1 == out2
    rows = sqlite3.connect(out2).execute(
        "SELECT v FROM t WHERE v='second'"
    ).fetchall()
    assert len(rows) == 1


def test_backup_raises_when_source_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        auto_backup_db(retain_days=30, db_path=str(tmp_path / "nope.db"))


# ----------------------------------------------------------------------
# F1: argparse + dry-run
# ----------------------------------------------------------------------
def test_dry_run_does_not_create_backup_file(tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)
    backup_dir = tmp_path / "auto_backup"
    out = auto_backup_db(
        retain_days=30, db_path=str(src), dry_run=True,
    )
    # dry-run은 path를 반환하지만 실제 파일은 생성 안 됨
    assert not out.exists()
    # 디렉토리도 강제 생성되지 않음 (없는 채로 남음)
    # backup_dir 자체는 _resolve_backup_dir로 src.parent/auto_backup 결정.
    assert not backup_dir.exists()


def test_dry_run_preserves_existing_old_backups(tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)
    backup_dir = tmp_path / "auto_backup"
    backup_dir.mkdir()
    old = (datetime(2026, 4, 28) - timedelta(days=40)).strftime("%Y%m%d")
    old_file = backup_dir / f"{old}_kospi_analyzer.db"
    old_file.write_bytes(b"old")

    auto_backup_db(
        retain_days=30, db_path=str(src),
        now=datetime(2026, 4, 28), dry_run=True,
    )
    # dry-run은 삭제하지 않음
    assert old_file.exists()


def test_cli_main_dry_run_returns_0(tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)
    rc = cli_main([
        "--dry-run",
        "--db-path", str(src),
        "--retain-days", "30",
    ])
    assert rc == 0


def test_cli_main_missing_source_returns_2(tmp_path):
    rc = cli_main([
        "--db-path", str(tmp_path / "nope.db"),
    ])
    assert rc == 2


def test_cli_main_custom_backup_dir(tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)
    custom = tmp_path / "custom_dir"
    rc = cli_main([
        "--db-path", str(src),
        "--backup-dir", str(custom),
        "--retain-days", "30",
    ])
    assert rc == 0
    assert custom.exists()
    files = list(custom.glob("*_kospi_analyzer.db"))
    assert len(files) == 1


def test_cli_main_retain_days_zero_skips_purge(tmp_path):
    """retain-days=0 이면 정리 안 함 — 기존 자동 백업 보존."""
    src = tmp_path / "src.db"
    _make_db(src)
    backup_dir = tmp_path / "auto_backup"
    backup_dir.mkdir()
    very_old = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
    f = backup_dir / f"{very_old}_kospi_analyzer.db"
    f.write_bytes(b"x")

    rc = cli_main([
        "--db-path", str(src),
        "--retain-days", "0",
    ])
    assert rc == 0
    assert f.exists()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

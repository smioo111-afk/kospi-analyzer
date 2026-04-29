"""자동 DB 백업 (스케줄러용).

매일 16:30 KST에 실행되어 data/kospi_analyzer.db를
data/auto_backup/YYYYMMDD_kospi_analyzer.db로 복사한다.

  1. WAL TRUNCATE 체크포인트로 -wal/-shm 정리
  2. sqlite3 백업 API로 일관된 스냅샷 생성 (file copy 대신)
  3. retain_days 이전의 자동 백업 자동 삭제

기존 .bak_before_* 등 수동 백업은 패턴 매칭으로 보존된다.
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config.settings import DBConfig

logger = logging.getLogger(__name__)


# 자동 백업 파일명 형식: YYYYMMDD_kospi_analyzer.db
# 수동 백업(.bak_before_*, .bak_*)과 명확히 구분되어 정리 시 충돌 없음.
AUTO_BACKUP_DIR = "auto_backup"
AUTO_BACKUP_PATTERN = re.compile(
    r"^(\d{8})_kospi_analyzer\.db$"
)


def _resolve_db_path(db_path: Optional[str] = None) -> Path:
    return Path(db_path or DBConfig.DB_PATH)


def _resolve_backup_dir(db_path: Path) -> Path:
    return db_path.parent / AUTO_BACKUP_DIR


def auto_backup_db(
    retain_days: int = 30,
    db_path: Optional[str] = None,
    now: Optional[datetime] = None,
    backup_dir: Optional[Path] = None,
    dry_run: bool = False,
) -> Path:
    """자동 백업을 수행한다.

    Args:
        retain_days: 이 일수 이전의 자동 백업은 삭제. 기본 30일.
        db_path: 소스 DB 경로 (기본: DBConfig.DB_PATH).
        now: 현재 시각 주입 (테스트용).
        backup_dir: 백업 디렉토리 명시. None이면 db_path.parent/auto_backup.
        dry_run: True면 실제 파일 생성/삭제 없이 계획만 로깅하고 예정 경로 반환.

    Returns:
        Path: 생성된(또는 예정된) 백업 파일 경로.
    """
    src = _resolve_db_path(db_path)
    if not src.exists():
        raise FileNotFoundError(f"백업 대상 DB가 없음: {src}")

    target_dir = (
        Path(backup_dir) if backup_dir is not None else _resolve_backup_dir(src)
    )
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)

    ts = (now or datetime.now()).strftime("%Y%m%d")
    dest = target_dir / f"{ts}_kospi_analyzer.db"

    if dry_run:
        size_mb = src.stat().st_size / (1024 * 1024)
        logger.info(
            "[dry-run] 백업 예정: %s → %s (소스 %.2f MB)",
            src, dest, size_mb,
        )
        # retain 정리도 dry-run으로 미리보기.
        n = _count_old_auto_backups(
            target_dir, retain_days, now=(now or datetime.now()),
        )
        logger.info(
            "[dry-run] 정리 예정: %d건 (retain_days=%d, dir=%s)",
            n, retain_days, target_dir,
        )
        return dest

    # 1) TRUNCATE 체크포인트로 -wal 비움 (백업 일관성 확보).
    #    실패해도 복구 가능: backup API가 dirty page도 함께 복사한다.
    try:
        with sqlite3.connect(src) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as e:
        logger.warning("백업 직전 WAL TRUNCATE 실패 (%s) - 백업은 계속 진행", e)

    # 2) sqlite3 backup API: 다른 writer가 있어도 일관된 스냅샷 생성.
    src_conn = sqlite3.connect(src)
    dst_conn = sqlite3.connect(dest)
    try:
        with dst_conn:
            src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    size_mb = dest.stat().st_size / (1024 * 1024)
    logger.info("자동 백업 생성: %s (%.2f MB)", dest, size_mb)

    # 3) retain_days 초과 자동 백업 정리. 패턴 매칭으로 수동 백업은 건드리지 않음.
    removed = _purge_old_auto_backups(
        target_dir, retain_days, now=(now or datetime.now())
    )
    if removed:
        logger.info("자동 백업 정리: %d건 삭제 (retain_days=%d)", removed, retain_days)

    return dest


def _count_old_auto_backups(
    backup_dir: Path, retain_days: int, now: datetime
) -> int:
    """삭제 대상 자동 백업 개수만 세어 반환 (dry-run 미리보기용)."""
    if retain_days <= 0 or not backup_dir.exists():
        return 0
    cutoff = (now - timedelta(days=retain_days)).date()
    count = 0
    for entry in backup_dir.iterdir():
        if not entry.is_file():
            continue
        m = AUTO_BACKUP_PATTERN.match(entry.name)
        if not m:
            continue
        try:
            file_date = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            count += 1
    return count


def _purge_old_auto_backups(
    backup_dir: Path, retain_days: int, now: datetime
) -> int:
    """retain_days보다 오래된 자동 백업 파일을 삭제한다.

    파일명 YYYYMMDD 부분을 파싱한다. 패턴이 안 맞으면 무시 (수동 백업 보호).
    """
    if retain_days <= 0:
        return 0

    cutoff = now - timedelta(days=retain_days)
    cutoff_date = cutoff.date()
    removed = 0

    for entry in backup_dir.iterdir():
        if not entry.is_file():
            continue
        m = AUTO_BACKUP_PATTERN.match(entry.name)
        if not m:
            # 자동 백업 패턴 외 파일은 보존 (.bak_before_* 등)
            continue
        try:
            file_date = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if file_date < cutoff_date:
            try:
                entry.unlink()
                removed += 1
                logger.debug("자동 백업 삭제: %s", entry)
            except OSError as e:
                logger.warning("자동 백업 삭제 실패 (%s): %s", entry, e)

    return removed


# ----------------------------------------------------------------------
# 스케줄러 진입점
# ----------------------------------------------------------------------
def scheduled_auto_backup() -> None:
    """APScheduler에서 호출되는 진입점."""
    try:
        path = auto_backup_db()
        logger.info("scheduled_auto_backup 완료: %s", path)
    except Exception as e:
        logger.error("scheduled_auto_backup 실패: %s", e, exc_info=True)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="backup_db",
        description=(
            "KOSPI Analyzer DB 자동 백업 도구. "
            "data/kospi_analyzer.db를 data/auto_backup/YYYYMMDD_*.db로 복사."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 파일 생성/삭제 없이 계획만 로깅",
    )
    p.add_argument(
        "--retain-days",
        type=int,
        default=30,
        help="N일보다 오래된 자동 백업은 삭제 (기본 30, 0=정리 안함)",
    )
    p.add_argument(
        "--backup-dir",
        type=str,
        default=None,
        help="백업 디렉토리 (기본: 소스 DB 위치 옆 auto_backup/)",
    )
    p.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="소스 DB 경로 (기본: DBConfig.DB_PATH)",
    )
    return p


def cli_main(argv: Optional[list[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        auto_backup_db(
            retain_days=args.retain_days,
            db_path=args.db_path,
            backup_dir=Path(args.backup_dir) if args.backup_dir else None,
            dry_run=args.dry_run,
        )
        return 0
    except FileNotFoundError as e:
        logger.error("%s", e)
        return 2
    except Exception as e:
        logger.error("백업 실패: %s", e, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(cli_main())

"""A1 Phase 0.5: financial_metrics rcept_no/rcept_dt 초기 기준점 설정.

기존 dart_cache parquet에서 rcept_no를 추출해 financial_metrics의 빈
컬럼을 채운다. DART API 신규 호출 없음 (캐시 100% 활용).

사용법:
    # 미리보기 (DB 미수정)
    python tools/init_disclosure_baseline.py --dry-run

    # 실제 적용
    python tools/init_disclosure_baseline.py --apply

    # 특정 연도/한도
    python tools/init_disclosure_baseline.py --apply --year 2025 --limit 50
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import DBConfig  # noqa: E402

logger = logging.getLogger(__name__)

CACHE_DIR = ROOT / "data" / "dart_cache"


def _extract_rcept_from_parquet(path: Path) -> tuple[str, str]:
    """parquet에서 rcept_no/rcept_dt 추출. 실패 시 빈 문자열."""
    try:
        df = pd.read_parquet(path, columns=["rcept_no"])
    except Exception as e:
        logger.debug("parquet 읽기 실패 %s: %s", path, e)
        return "", ""
    if df.empty or "rcept_no" not in df.columns:
        return "", ""
    try:
        rcept_no = str(df["rcept_no"].iloc[0]).strip()
    except (IndexError, KeyError):
        return "", ""
    if not rcept_no or rcept_no.lower() in ("none", "nan"):
        return "", ""
    rcept_dt = rcept_no[:8] if len(rcept_no) >= 8 and rcept_no[:8].isdigit() else ""
    return rcept_no, rcept_dt


def init_baseline(
    db_path: str,
    cache_dir: Path,
    year: Optional[int] = None,
    quarter: str = "annual",
    apply: bool = False,
    limit: Optional[int] = None,
) -> dict:
    """financial_metrics의 빈 rcept_no를 dart_cache에서 채운다.

    Args:
        db_path: SQLite 경로.
        cache_dir: dart_cache 디렉토리.
        year: 대상 연도. None이면 모든 연도 (rcept_no='' 인 모든 행).
        quarter: 'annual' 등.
        apply: True면 UPDATE 실행, False면 dry-run.
        limit: 처리 종목 수 제한 (테스트용).

    Returns:
        dict: 통계 (target/updated/skipped_no_cache/skipped_no_rcept).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        params: list = []
        sql = (
            "SELECT stock_code, year, quarter, rcept_no FROM financial_metrics "
            "WHERE (rcept_no IS NULL OR rcept_no = '')"
        )
        if year is not None:
            sql += " AND year = ?"
            params.append(year)
        if quarter:
            sql += " AND quarter = ?"
            params.append(quarter)
        sql += " ORDER BY stock_code, year"
        rows = conn.execute(sql, params).fetchall()
        if limit is not None:
            rows = rows[:limit]

        target = len(rows)
        updated = 0
        skipped_no_cache = 0
        skipped_no_rcept = 0
        examples: list[str] = []

        for r in rows:
            code = r["stock_code"]
            yr = r["year"]
            qt = r["quarter"]
            # 캐시 파일명 규약: {code}_{year}_{quarter}.parquet
            cache_path = cache_dir / f"{code}_{yr}_{qt}.parquet"
            if not cache_path.exists():
                skipped_no_cache += 1
                continue
            rcept_no, rcept_dt = _extract_rcept_from_parquet(cache_path)
            if not rcept_no:
                skipped_no_rcept += 1
                continue
            if apply:
                conn.execute(
                    "UPDATE financial_metrics "
                    "SET rcept_no = ?, rcept_dt = ?, "
                    "    updated_at = ? "
                    "WHERE stock_code = ? AND year = ? AND quarter = ?",
                    (
                        rcept_no, rcept_dt,
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        code, yr, qt,
                    ),
                )
            updated += 1
            if len(examples) < 5:
                examples.append(f"{code} {yr}{qt}: {rcept_no} ({rcept_dt})")

        if apply:
            conn.commit()
    finally:
        conn.close()

    return {
        "target": target,
        "updated": updated,
        "skipped_no_cache": skipped_no_cache,
        "skipped_no_rcept": skipped_no_rcept,
        "examples": examples,
        "applied": apply,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="init_disclosure_baseline",
        description=(
            "financial_metrics의 빈 rcept_no/rcept_dt를 dart_cache parquet "
            "에서 추출해 채운다. DART API 호출 없음."
        ),
    )
    p.add_argument(
        "--apply", action="store_true",
        help="실제 UPDATE (기본은 dry-run)",
    )
    p.add_argument(
        "--year", type=int, default=None,
        help="특정 연도만 처리 (기본: 모든 연도의 빈 행)",
    )
    p.add_argument(
        "--quarter", default="annual",
        help="quarter 필터 (기본 'annual')",
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="처리 종목 수 제한 (테스트용)",
    )
    p.add_argument(
        "--db-path", default=None,
        help="SQLite 경로 (기본: DBConfig.DB_PATH)",
    )
    p.add_argument(
        "--cache-dir", default=None,
        help="dart_cache 디렉토리 (기본: data/dart_cache)",
    )
    return p


def cli_main(argv: Optional[list[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    db_path = args.db_path or DBConfig.DB_PATH
    cache_dir = Path(args.cache_dir) if args.cache_dir else CACHE_DIR

    if not Path(db_path).exists():
        logger.error("DB가 없음: %s", db_path)
        return 2
    if not cache_dir.exists():
        logger.error("캐시 디렉토리가 없음: %s", cache_dir)
        return 2

    logger.info(
        "init_disclosure_baseline 시작 (apply=%s, year=%s, quarter=%s, limit=%s)",
        args.apply, args.year, args.quarter, args.limit,
    )
    stats = init_baseline(
        db_path=db_path,
        cache_dir=cache_dir,
        year=args.year,
        quarter=args.quarter,
        apply=args.apply,
        limit=args.limit,
    )

    mode = "APPLY" if args.apply else "DRY-RUN"
    logger.info(
        "[%s] 결과: target=%d updated=%d skipped(no_cache)=%d skipped(no_rcept)=%d",
        mode, stats["target"], stats["updated"],
        stats["skipped_no_cache"], stats["skipped_no_rcept"],
    )
    if stats["examples"]:
        logger.info("예시:")
        for line in stats["examples"]:
            logger.info("  %s", line)
    if not args.apply and stats["updated"] > 0:
        logger.info("실제 적용하려면 --apply 추가하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())

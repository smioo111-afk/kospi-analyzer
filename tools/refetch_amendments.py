"""A1 Phase 0.7: 정정공시 영향 종목 financial_metrics 일괄 재수집.

backfill_recent_disclosures.py가 생성한 보고서 JSON에서 정정공시
종목을 추출하고, 해당 종목의 dart_cache parquet을 invalidate한 뒤
DART에서 다시 받아 financial_metrics를 UPDATE한다.

사용법:
    python tools/refetch_amendments.py \\
        --report-file data/disclosure_reports/backfill_*.json \\
        --dry-run

    python tools/refetch_amendments.py \\
        --report-file data/disclosure_reports/backfill_*.json \\
        --apply
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.dart_api import DARTClient  # noqa: E402
from config.settings import DBConfig  # noqa: E402
from database.models import Database  # noqa: E402

logger = logging.getLogger(__name__)

CACHE_DIR = ROOT / "data" / "dart_cache"


def extract_amendment_codes(report_file: Path) -> list[str]:
    """보고서 JSON에서 정정공시 종목코드 unique 리스트를 추출."""
    data = json.loads(report_file.read_text(encoding="utf-8"))
    # backfill 보고서는 amendment_examples만 truncate (10개) 형태로 저장.
    # 상위 도구가 향후 amendments 전체를 저장하도록 확장될 수 있으므로
    # 두 키를 모두 본다. 우선 amendment_examples를 사용.
    amendments = data.get("amendments_full") or data.get("amendment_examples", [])
    codes = []
    seen: set[str] = set()
    for it in amendments:
        code = str(it.get("stock_code") or "").strip()
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def invalidate_cache(stock_code: str, year: int,
                     report_type: str = "annual",
                     cache_dir: Path = CACHE_DIR) -> bool:
    """해당 (code, year, type) 캐시를 삭제. 없으면 False."""
    path = cache_dir / f"{stock_code}_{year}_{report_type}.parquet"
    if path.exists():
        path.unlink()
        return True
    return False


def refetch_one(
    client: DARTClient, db: Database, stock_code: str, year: int,
    cache_dir: Path = CACHE_DIR,
) -> dict:
    """단일 종목 재수집.

    Returns:
        dict: {stock_code, status, old_rcept, new_rcept, error}
    """
    # 기존 rcept_no 조회 (변화 추적용)
    conn = db._get_conn()
    row = conn.execute(
        "SELECT rcept_no FROM financial_metrics "
        "WHERE stock_code=? AND year=? AND quarter='annual'",
        (stock_code, year),
    ).fetchone()
    old_rcept = row["rcept_no"] if row else ""

    invalidated = invalidate_cache(stock_code, year, cache_dir=cache_dir)
    try:
        metrics = client.extract_financial_metrics(stock_code, year=year)
    except Exception as e:
        return {
            "stock_code": stock_code,
            "status": "error",
            "old_rcept": old_rcept,
            "new_rcept": "",
            "cache_invalidated": invalidated,
            "error": str(e),
        }

    new_rcept = str(metrics.get("rcept_no", ""))
    new_dt = str(metrics.get("rcept_dt", ""))

    if not new_rcept:
        return {
            "stock_code": stock_code,
            "status": "no_rcept",
            "old_rcept": old_rcept,
            "new_rcept": "",
            "cache_invalidated": invalidated,
            "error": "재수집 결과 rcept_no 없음 (DART 미응답 또는 캐시 미생성)",
        }

    db.save_financial_metrics(metrics)
    return {
        "stock_code": stock_code,
        "status": "ok",
        "old_rcept": old_rcept,
        "new_rcept": new_rcept,
        "new_rcept_dt": new_dt,
        "cache_invalidated": invalidated,
        "rcept_changed": old_rcept != new_rcept,
    }


def refetch_amendments(
    report_file: Path,
    year: int,
    apply: bool,
    db_path: str,
    cache_dir: Path = CACHE_DIR,
    limit: Optional[int] = None,
    client: Optional[DARTClient] = None,
    db: Optional[Database] = None,
    sleep_between: float = 0.0,
) -> dict:
    """정정공시 보고서 기반 일괄 재수집.

    apply=False(dry-run)는 캐시 invalidate / DART 호출 / DB UPDATE 모두
    수행하지 않고, 처리 예정 종목 수만 보고한다.
    """
    codes = extract_amendment_codes(report_file)
    if limit is not None:
        codes = codes[:limit]
    target = len(codes)

    if not apply:
        return {
            "applied": False,
            "report_file": str(report_file),
            "year": year,
            "target": target,
            "codes_preview": codes[:10],
            "expected_api_calls": target,  # 캐시 invalidate 후 1종목당 1회
        }

    client = client or DARTClient()
    db = db or Database(db_path=db_path)
    results: list[dict] = []
    success = 0
    fail = 0
    rcept_changed = 0
    for i, code in enumerate(codes, 1):
        try:
            r = refetch_one(client, db, code, year, cache_dir=cache_dir)
        except Exception as e:
            r = {
                "stock_code": code, "status": "exception",
                "error": str(e),
            }
        results.append(r)
        if r.get("status") == "ok":
            success += 1
            if r.get("rcept_changed"):
                rcept_changed += 1
        else:
            fail += 1
        if i % 25 == 0:
            logger.info(
                "진행 %d/%d (성공 %d, 실패 %d, rcept 변경 %d)",
                i, target, success, fail, rcept_changed,
            )
        if sleep_between > 0:
            time.sleep(sleep_between)

    return {
        "applied": True,
        "report_file": str(report_file),
        "year": year,
        "target": target,
        "success": success,
        "fail": fail,
        "rcept_changed": rcept_changed,
        "results": results,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="refetch_amendments",
        description=(
            "정정공시 보고서에 등장한 종목의 dart_cache를 무효화하고 "
            "DART에서 재수집해 financial_metrics를 갱신한다."
        ),
    )
    p.add_argument("--report-file", required=True,
                   help="backfill 보고서 JSON 경로")
    p.add_argument("--year", type=int, default=2025,
                   help="대상 사업연도 (기본 2025)")
    p.add_argument("--apply", action="store_true",
                   help="실제 캐시 무효화 + DB UPDATE (기본은 dry-run)")
    p.add_argument("--limit", type=int, default=None,
                   help="처리 종목 수 제한 (테스트용)")
    p.add_argument("--db-path", default=None,
                   help="SQLite 경로 (기본: DBConfig.DB_PATH)")
    p.add_argument("--cache-dir", default=None,
                   help="dart_cache 디렉토리 (기본: data/dart_cache)")
    return p


def cli_main(argv: Optional[list[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    report_path = Path(args.report_file)
    if not report_path.exists():
        logger.error("보고서 파일 없음: %s", report_path)
        return 2
    db_path = args.db_path or DBConfig.DB_PATH
    if not Path(db_path).exists():
        logger.error("DB 없음: %s", db_path)
        return 2
    cache_dir = Path(args.cache_dir) if args.cache_dir else CACHE_DIR

    logger.info(
        "refetch_amendments 시작 (apply=%s, year=%d, limit=%s)",
        args.apply, args.year, args.limit,
    )
    try:
        stats = refetch_amendments(
            report_file=report_path,
            year=args.year,
            apply=args.apply,
            db_path=db_path,
            cache_dir=cache_dir,
            limit=args.limit,
        )
    except Exception as e:
        logger.error("재수집 실패: %s", e, exc_info=True)
        return 1

    if not stats["applied"]:
        logger.info(
            "[DRY-RUN] target=%d expected_api_calls=%d codes_preview=%s",
            stats["target"], stats["expected_api_calls"],
            stats["codes_preview"],
        )
        logger.info("실제 적용하려면 --apply 추가하세요.")
        return 0

    logger.info(
        "[APPLY] target=%d success=%d fail=%d rcept_changed=%d",
        stats["target"], stats["success"], stats["fail"],
        stats["rcept_changed"],
    )
    # 보고서 저장
    report_dir = ROOT / "data" / "disclosure_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    out = report_dir / (
        f"refetch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    out.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("재수집 보고서 저장: %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())

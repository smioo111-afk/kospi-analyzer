"""A1 Phase 0.5: 최근 N일 공시 백필.

분석 종목(financial_metrics에 있는 종목)에 대해 최근 N일 공시를 조회하고,
정정공시(rm에 '정' 표기) + 정기공시 신규 발견을 분류해 보고한다.

본 단계에서는 보고만 수행한다. 실제 재수집은 Phase 1+에서 처리.
이 도구는 공백 기간(예: 4-26~4-29)을 안전히 따라잡기 위한 백필용.

사용법:
    python tools/backfill_recent_disclosures.py --days 30 --dry-run
    python tools/backfill_recent_disclosures.py --days 30 --apply
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.dart_api import DARTClient  # noqa: E402
from config.settings import DBConfig  # noqa: E402

logger = logging.getLogger(__name__)


# 정기공시 보고서명 키워드 (사업/반기/분기보고서)
_PERIODIC_KEYWORDS = ("사업보고서", "반기보고서", "분기보고서")


def is_amendment(item: dict) -> bool:
    """정정공시 여부 — DART는 report_nm 앞에 '[기재정정]' 등을 붙이거나
    rm 컬럼에 '정' 코드가 들어간다.
    """
    nm = str(item.get("report_nm", ""))
    rm = str(item.get("rm", ""))
    if "정정" in nm:
        return True
    # rm 코드: 유 = 유가증권, 코 = 코스닥, 정 = 정정 등
    return "정" in rm


def is_periodic(item: dict) -> bool:
    nm = str(item.get("report_nm", ""))
    return any(k in nm for k in _PERIODIC_KEYWORDS)


def get_analyzed_stock_codes(db_path: str) -> set[str]:
    """financial_metrics에 있는 모든 종목코드."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT stock_code FROM financial_metrics"
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def backfill_recent(
    db_path: str,
    days: int,
    apply: bool = False,
    client: Optional[DARTClient] = None,
    today: Optional[datetime] = None,
) -> dict:
    """최근 N일 KOSPI 공시를 조회하고 분석 종목 한정 분류 결과를 반환.

    apply=True여도 본 도구는 DB를 수정하지 않는다 (보고만 수행).
    실제 재수집은 후속 Phase에서 별도 도구가 담당한다. apply 플래그는
    "보고서 파일을 디스크에 남길지" 여부에만 영향.
    """
    today = today or datetime.now()
    end = today.date()
    start = end - timedelta(days=days)
    bgn_de = start.strftime("%Y%m%d")
    end_de = end.strftime("%Y%m%d")

    analyzed = get_analyzed_stock_codes(db_path)
    if not analyzed:
        logger.warning("financial_metrics가 비어있음 — 분석 종목 0건")

    client = client or DARTClient()
    logger.info(
        "DART list.json 조회: %s ~ %s (KOSPI 전체)", bgn_de, end_de,
    )
    items = client.fetch_disclosure_list(
        bgn_de=bgn_de, end_de=end_de, corp_cls="Y",
    )

    relevant = [it for it in items if str(it.get("stock_code", "")) in analyzed]
    amendments = [it for it in relevant if is_amendment(it)]
    periodics = [it for it in relevant if is_periodic(it)]

    stats = {
        "bgn_de": bgn_de,
        "end_de": end_de,
        "total_disclosures": len(items),
        "analyzed_stocks": len(analyzed),
        "relevant_disclosures": len(relevant),
        "amendments": len(amendments),
        "periodics": len(periodics),
        "amendment_examples": [
            {
                "stock_code": it.get("stock_code"),
                "corp_name": it.get("corp_name"),
                "report_nm": it.get("report_nm"),
                "rcept_no": it.get("rcept_no"),
                "rcept_dt": it.get("rcept_dt"),
            }
            for it in amendments[:10]
        ],
        "periodic_examples": [
            {
                "stock_code": it.get("stock_code"),
                "corp_name": it.get("corp_name"),
                "report_nm": it.get("report_nm"),
                "rcept_no": it.get("rcept_no"),
                "rcept_dt": it.get("rcept_dt"),
            }
            for it in periodics[:10]
        ],
    }

    if apply:
        # 보고서 파일 저장 (재수집은 후속 Phase 담당이므로 흔적만 남김).
        report_dir = ROOT / "data" / "disclosure_reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        out = report_dir / (
            f"backfill_{bgn_de}_{end_de}_{today.strftime('%H%M%S')}.json"
        )
        out.write_text(
            json.dumps(stats, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("백필 보고서 저장: %s", out)
        stats["report_path"] = str(out)
    return stats


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="backfill_recent_disclosures",
        description="최근 N일 KOSPI 공시 중 분석 종목 영향을 분류 보고",
    )
    p.add_argument("--days", type=int, default=30,
                   help="조회 기간 (기본 30일)")
    p.add_argument("--apply", action="store_true",
                   help="보고서 파일을 disclosure_reports/에 저장 (DB 무수정)")
    p.add_argument("--db-path", default=None,
                   help="SQLite 경로 (기본: DBConfig.DB_PATH)")
    return p


def cli_main(argv: Optional[list[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    db_path = args.db_path or DBConfig.DB_PATH
    if not Path(db_path).exists():
        logger.error("DB가 없음: %s", db_path)
        return 2

    try:
        stats = backfill_recent(
            db_path=db_path, days=args.days, apply=args.apply,
        )
    except Exception as e:
        logger.error("백필 실패: %s", e, exc_info=True)
        return 1

    logger.info(
        "백필 결과 (%s ~ %s):"
        " total_disclosures=%d, analyzed=%d,"
        " relevant=%d, amendments=%d, periodics=%d",
        stats["bgn_de"], stats["end_de"],
        stats["total_disclosures"], stats["analyzed_stocks"],
        stats["relevant_disclosures"],
        stats["amendments"], stats["periodics"],
    )
    if stats["amendment_examples"]:
        logger.info("정정공시 예시:")
        for ex in stats["amendment_examples"]:
            logger.info(
                "  %s %s — %s (%s)",
                ex["stock_code"], ex.get("corp_name"),
                ex.get("report_nm"), ex.get("rcept_dt"),
            )
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())

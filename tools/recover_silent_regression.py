"""4-30 silent regression 회복: sector 누락으로 revenue=0이 된 종목 재추출.

대상 (4-30 진단 docs/diagnostic_20260430.md 참조):
  001500 현대차증권 (sector=증권)
  105560 KB금융 (sector=금융)
  139130 iM금융지주 (sector=금융)
  316140 우리금융지주 (sector=금융)
  064400 LG씨엔에스 (sector=IT 서비스, parser는 정상이나 캐시 hit으로 stale)

흐름:
  1. financial_metrics에서 현재 sector 조회 (없거나 '기타'면 인자 sector 사용)
  2. extract_financial_metrics(code, year=2025, sector=sector) 호출
  3. revenue 정상 추출 확인 후 save_financial_metrics (UPSERT 가드 적용 후)
  4. 064400은 캐시 invalidate 후 재호출

사용법:
  python tools/recover_silent_regression.py --dry-run
  python tools/recover_silent_regression.py --apply
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.dart_api import DARTClient  # noqa: E402
from config.settings import DBConfig  # noqa: E402
from database.models import Database  # noqa: E402

logger = logging.getLogger(__name__)

CACHE_DIR = ROOT / "data" / "dart_cache"

# 회복 대상: (code, fallback_sector, invalidate_cache)
_TARGETS: list[tuple[str, str, bool]] = [
    ("001500", "증권", False),      # 현대차증권
    ("105560", "금융", False),      # KB금융
    ("139130", "금융", False),      # iM금융지주
    ("316140", "금융", False),      # 우리금융지주
    ("064400", "IT 서비스", True),  # LG씨엔에스 (stale 캐시 무효화)
]


def _resolve_sector(db: Database, code: str, fallback: str) -> str:
    row = db.get_financial_metrics(code, 2025)
    cur = (row or {}).get("sector") if row else None
    # 4-29 regression으로 sector가 '기타'로 덮어진 경우 fallback 사용
    if not cur or cur == "기타":
        return fallback
    return cur


def _invalidate(code: str, year: int) -> bool:
    p = CACHE_DIR / f"{code}_{year}_annual.parquet"
    if p.exists():
        p.unlink()
        return True
    return False


def recover(apply: bool, year: int = 2025) -> dict:
    db = Database(db_path=DBConfig.DB_PATH)
    client = DARTClient()
    client.load_corp_codes()
    results = []
    try:
        for code, fb_sector, do_invalidate in _TARGETS:
            sector = _resolve_sector(db, code, fb_sector)
            before = db.get_financial_metrics(code, year) or {}
            invalidated = False
            if do_invalidate and apply:
                invalidated = _invalidate(code, year)
            try:
                metrics = client.extract_financial_metrics(
                    code, year=year, sector=sector,
                )
            except Exception as e:
                results.append({
                    "code": code, "sector": sector, "status": "error",
                    "error": str(e),
                })
                continue
            new_rev = int(metrics.get("revenue", 0) or 0)
            new_op = int(metrics.get("operating_income", 0) or 0)
            new_net = int(metrics.get("net_income", 0) or 0)
            applied = False
            if apply and new_rev > 0:
                # sector를 명시적으로 보존 (원래 sector가 KIS-기반)
                metrics["sector"] = sector
                db.save_financial_metrics(metrics)
                applied = True
            results.append({
                "code": code,
                "sector": sector,
                "before_revenue": before.get("revenue", 0),
                "new_revenue": new_rev,
                "new_op_income": new_op,
                "new_net_income": new_net,
                "rcept_no": metrics.get("rcept_no", ""),
                "cache_invalidated": invalidated,
                "applied": applied,
                "status": "ok" if new_rev > 0 else "no_revenue",
            })
    finally:
        db.close()
    return {"applied": apply, "year": year, "results": results}


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="recover_silent_regression")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="미리보기")
    g.add_argument("--apply", action="store_true", help="실제 UPDATE")
    p.add_argument("--year", type=int, default=2025)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _build_arg_parser().parse_args(argv)
    out = recover(apply=args.apply, year=args.year)
    print(f"applied={out['applied']} year={out['year']}")
    for r in out["results"]:
        print(
            f"  {r['code']} sector={r['sector']:<8} "
            f"before_rev={r.get('before_revenue', 0):>16} "
            f"new_rev={r.get('new_revenue', 0):>16} "
            f"status={r['status']} applied={r.get('applied', False)} "
            f"cache_inv={r.get('cache_invalidated', False)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

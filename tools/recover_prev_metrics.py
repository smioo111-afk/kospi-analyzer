"""4-30 P1-2: prev_revenue=0 종목 일괄 재추출.

진단(docs/diagnostic_20260430_prev_revenue.md)에서 식별된 122건+ 결손
회복. parser는 4-30 PR로 sector 분기 적용 완료 → sector를 인자로 전달해
재호출하면 캐시 hit으로 prev_revenue가 정상 추출된다.

분류 (회복 비용):
  - G (95건) 일반 종목 stale: 캐시 hit, DART API 호출 0회
  - D (25건) 금융주: 캐시 hit, DART API 호출 0회
  - A (5건) 2024 캐시 부재: DART API 1회 신규 호출 (--fetch-missing 옵션)

사용법:
    python tools/recover_prev_metrics.py --dry-run
    python tools/recover_prev_metrics.py --apply
    python tools/recover_prev_metrics.py --apply --fetch-missing
    python tools/recover_prev_metrics.py --dry-run --limit 5
"""

from __future__ import annotations

import argparse
import logging
import sys
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


def _list_targets(db: Database, year: int) -> list[tuple[str, str]]:
    """prev_revenue=0 AND revenue>0 종목과 sector를 반환."""
    conn = db._get_conn()
    rows = conn.execute(
        """SELECT stock_code, sector FROM financial_metrics
           WHERE year=? AND quarter='annual'
             AND revenue > 0 AND prev_revenue = 0
           ORDER BY revenue DESC""",
        (year,),
    ).fetchall()
    return [(r["stock_code"], r["sector"] or "") for r in rows]


def _has_prev_cache(code: str, year: int) -> bool:
    return (CACHE_DIR / f"{code}_{year - 1}_annual.parquet").exists()


def recover(
    apply: bool,
    year: int = 2025,
    limit: Optional[int] = None,
    fetch_missing: bool = False,
) -> dict:
    db = Database(db_path=DBConfig.DB_PATH)
    client = DARTClient()
    client.load_corp_codes()
    targets = _list_targets(db, year)
    if limit is not None:
        targets = targets[:limit]

    results: list[dict] = []
    cache_hits = 0
    cache_miss = 0
    api_calls = 0
    recovered = 0
    unchanged = 0

    try:
        for code, sector in targets:
            has_cache = _has_prev_cache(code, year)
            if not has_cache:
                cache_miss += 1
                if not fetch_missing:
                    results.append({
                        "code": code, "sector": sector,
                        "status": "skip_no_cache",
                        "applied": False,
                    })
                    continue
            else:
                cache_hits += 1

            try:
                metrics = client.extract_financial_metrics(
                    code, year=year, sector=sector or None,
                )
                if not has_cache and fetch_missing:
                    api_calls += 1
            except Exception as e:
                results.append({
                    "code": code, "sector": sector,
                    "status": "error", "error": str(e),
                    "applied": False,
                })
                continue

            new_prev = int(metrics.get("prev_revenue", 0) or 0)
            new_rev_g = float(metrics.get("revenue_growth_yoy", 0.0) or 0.0)
            new_op_g = float(metrics.get("op_income_growth_yoy", 0.0) or 0.0)

            if new_prev <= 0:
                unchanged += 1
                results.append({
                    "code": code, "sector": sector,
                    "status": "no_prev_after_retry",
                    "new_prev_revenue": new_prev,
                    "applied": False,
                })
                continue

            applied = False
            if apply:
                # sector 보존 (KIS-기반). UPSERT 가드는 0→nonzero 정상 갱신 허용.
                metrics["sector"] = sector or metrics.get("sector", "기타")
                db.save_financial_metrics(metrics)
                applied = True
                recovered += 1

            results.append({
                "code": code,
                "sector": sector,
                "status": "ok",
                "new_prev_revenue": new_prev,
                "new_revenue_growth_yoy": new_rev_g,
                "new_op_income_growth_yoy": new_op_g,
                "cache_hit": has_cache,
                "applied": applied,
            })
    finally:
        db.close()

    return {
        "applied": apply,
        "year": year,
        "target_count": len(targets),
        "cache_hits": cache_hits,
        "cache_miss": cache_miss,
        "api_calls": api_calls,
        "recovered": recovered,
        "unchanged": unchanged,
        "results": results,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="recover_prev_metrics")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    p.add_argument("--year", type=int, default=2025)
    p.add_argument("--limit", type=int, default=None,
                   help="처리 종목 수 제한 (테스트용)")
    p.add_argument("--fetch-missing", action="store_true",
                   help="2024 annual 캐시 부재 종목도 DART에서 신규 fetch")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _build_arg_parser().parse_args(argv)
    out = recover(
        apply=args.apply, year=args.year,
        limit=args.limit, fetch_missing=args.fetch_missing,
    )
    print(
        f"applied={out['applied']} year={out['year']} "
        f"target={out['target_count']} cache_hit={out['cache_hits']} "
        f"cache_miss={out['cache_miss']} api_calls={out['api_calls']} "
        f"recovered={out['recovered']} unchanged={out['unchanged']}"
    )
    # 처음 10건만 표시
    for r in out["results"][:10]:
        cells = [
            f"{r['code']}", f"sec={r['sector'] or '-':<6}",
            f"status={r['status']}",
        ]
        if "new_prev_revenue" in r:
            cells.append(f"prev={r['new_prev_revenue']:>16}")
        if "new_revenue_growth_yoy" in r:
            cells.append(f"rev_g={r['new_revenue_growth_yoy']:.2f}")
        cells.append(f"applied={r.get('applied', False)}")
        print("  " + "  ".join(cells))
    if len(out["results"]) > 10:
        print(f"  ... and {len(out['results']) - 10} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())

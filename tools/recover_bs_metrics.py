"""4-30 P1: BS account_id fallback 패치 후 BS 결손 일괄 회복.

진단 (docs/diagnostic_20260430_remaining_28.md) 그룹 A 8종 회복 도구.
대상: total_assets=0 OR total_equity=0 OR (net_income=0 AND revenue>0)
  AND rcept_no != ''  (= 보고서 수집됨, parser 결함만)

분류:
  - C (parser 결함): account_nm 변형(`총자산`/`자산 합계`/`기말자본`/`자본` 등)
    → BS account_ids fallback 패치 후 회복
  - F (stale 3 cell): 051900 assets+equity, 0126Z0 net
    → 현행 parser 정상, 동일 도구로 자동 흡수

DART API 호출: 0회 (전 종목 캐시 hit 예상)

사용법:
    python tools/recover_bs_metrics.py --dry-run
    python tools/recover_bs_metrics.py --apply
    python tools/recover_bs_metrics.py --dry-run --limit 3
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


def _list_targets(db: Database, year: int) -> list[dict]:
    """BS 결손 종목 (rcept_no 있음)을 반환."""
    conn = db._get_conn()
    rows = conn.execute(
        """SELECT stock_code, sector, revenue, operating_income, net_income,
                  total_assets, total_equity, roe, rcept_no
           FROM financial_metrics
           WHERE year=? AND quarter='annual'
             AND rcept_no != ''
             AND (
                  total_assets = 0
               OR total_equity = 0
               OR (revenue > 0 AND net_income = 0)
             )
           ORDER BY revenue DESC""",
        (year,),
    ).fetchall()
    return [dict(r) for r in rows]


def _has_cache(code: str, year: int) -> bool:
    return (CACHE_DIR / f"{code}_{year}_annual.parquet").exists()


def recover(
    apply: bool,
    year: int = 2025,
    limit: Optional[int] = None,
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
    recovered = 0
    unchanged = 0

    try:
        for t in targets:
            code = t["stock_code"]
            sector = t["sector"] or ""

            if not _has_cache(code, year):
                cache_miss += 1
                results.append({
                    "code": code, "sector": sector,
                    "status": "skip_no_cache",
                    "applied": False,
                })
                continue
            cache_hits += 1

            try:
                metrics = client.extract_financial_metrics(
                    code, year=year, sector=sector or None,
                )
            except Exception as e:
                results.append({
                    "code": code, "sector": sector,
                    "status": "error", "error": str(e),
                    "applied": False,
                })
                continue

            new_assets = int(metrics.get("total_assets", 0) or 0)
            new_equity = int(metrics.get("total_equity", 0) or 0)
            new_net = int(metrics.get("net_income", 0) or 0)

            old_assets = int(t["total_assets"] or 0)
            old_equity = int(t["total_equity"] or 0)
            old_net = int(t["net_income"] or 0)

            improved = (
                (old_assets == 0 and new_assets != 0)
                or (old_equity == 0 and new_equity != 0)
                or (old_net == 0 and new_net != 0)
            )

            if not improved:
                unchanged += 1
                results.append({
                    "code": code, "sector": sector,
                    "status": "no_change",
                    "old_assets": old_assets, "new_assets": new_assets,
                    "old_equity": old_equity, "new_equity": new_equity,
                    "old_net": old_net, "new_net": new_net,
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
                "code": code, "sector": sector,
                "status": "ok",
                "old_assets": old_assets, "new_assets": new_assets,
                "old_equity": old_equity, "new_equity": new_equity,
                "old_net": old_net, "new_net": new_net,
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
        "recovered": recovered,
        "unchanged": unchanged,
        "results": results,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="recover_bs_metrics")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    p.add_argument("--year", type=int, default=2025)
    p.add_argument("--limit", type=int, default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _build_arg_parser().parse_args(argv)
    out = recover(apply=args.apply, year=args.year, limit=args.limit)
    print(
        f"applied={out['applied']} year={out['year']} "
        f"target={out['target_count']} cache_hit={out['cache_hits']} "
        f"cache_miss={out['cache_miss']} "
        f"recovered={out['recovered']} unchanged={out['unchanged']}"
    )
    for r in out["results"]:
        cells = [f"{r['code']}", f"sec={r['sector'] or '-':<6}", f"status={r['status']}"]
        if "new_assets" in r:
            cells.append(
                f"assets={r['old_assets']:>14}→{r['new_assets']:>14}"
            )
            cells.append(
                f"equity={r['old_equity']:>14}→{r['new_equity']:>14}"
            )
            cells.append(
                f"net={r['old_net']:>14}→{r['new_net']:>14}"
            )
        cells.append(f"applied={r.get('applied', False)}")
        print("  " + "  ".join(cells))
    return 0


if __name__ == "__main__":
    sys.exit(main())

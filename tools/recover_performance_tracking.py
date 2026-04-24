"""오염된 performance_tracking 레코드 복구 도구.

2026-04-24 사건으로 14종목(80건) 레코드가 is_delisted=1, return_*=-100으로
오염됐다. 이 스크립트는 각 종목의 실제 생존 여부를 KIS API로 확인한 뒤,
정상 종목의 행을 "미계산" 상태로 되돌려 다음 update_performance_tracking
사이클에서 재계산되도록 한다.

기본 동작은 dry-run. --apply 없이 실제 DB 변경은 일어나지 않는다.

복구 로직:
  1) SELECT DISTINCT stock_code FROM performance_tracking WHERE is_delisted=1
  2) 각 종목에 대해 kis_client.aget_stock_price로 현재가 조회 시도
     - 성공 (current_price > 0) → "확정 생존"
     - 실패 또는 0 → "확정 불가, 상폐 유지"
  3) 확정 생존 종목의 모든 performance_tracking 행을 다음으로 갱신:
       is_delisted = 0
       delisted_detected_at = ''
       consecutive_fetch_failures = 0
       return_1w/1m/3m/6m/1y = 0
       price_after_1w/1m/3m/6m/1y = 0
     → 다음 스케줄 사이클에서 정상 재계산됨
  4) 확정 불가 종목은 출력만 하고 건드리지 않는다.

사용:
    python -m tools.recover_performance_tracking            # dry-run (기본)
    python -m tools.recover_performance_tracking --apply    # 실제 적용
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Optional

from collectors.kis_api import KISClient
from database.models import Database

logger = logging.getLogger(__name__)


# 대형주 화이트리스트: API 조회 실패해도 (일시 네트워크 이슈일 수 있음)
# 시총 500B 이상이면 복구 대상에 포함. 진짜 상장폐지는 수동 확인 후
# mark_stock_delisted로 재확정할 것.
LARGE_CAP_THRESHOLD_KRW = 500_000_000_000


async def _probe_alive(
    kis: KISClient, code: str,
) -> tuple[bool, int, Optional[str]]:
    """종목이 거래 가능한지 현재가 조회로 확인.

    Returns:
        (alive, price, error). alive=True이면 price > 0.
        실패/조회불가는 error에 이유 문자열.
    """
    try:
        r = await kis.aget_stock_price(code)
        price = int(r.get("current_price", 0) or 0)
        if price > 0:
            return True, price, None
        return False, 0, "current_price=0"
    except Exception as e:  # noqa: BLE001
        return False, 0, f"{type(e).__name__}: {str(e)[:120]}"


async def probe_delisted_stocks(
    db: Database,
) -> tuple[list[dict], list[dict]]:
    """is_delisted=1 종목을 전수 조회해 (alive, unconfirmed) 리스트로 분리."""
    conn = db._get_conn()
    rows = conn.execute(
        """SELECT DISTINCT stock_code, stock_name
           FROM performance_tracking
           WHERE is_delisted = 1
           ORDER BY stock_code""",
    ).fetchall()

    alive: list[dict] = []
    unconfirmed: list[dict] = []

    async with KISClient() as kis:
        for row in rows:
            code = row["stock_code"]
            name = row["stock_name"] or db.get_stock_name(code)
            is_alive, price, error = await _probe_alive(kis, code)
            market_cap = db._get_latest_market_cap(code)
            row_count = conn.execute(
                """SELECT COUNT(*) AS n FROM performance_tracking
                   WHERE stock_code = ? AND is_delisted = 1""",
                (code,),
            ).fetchone()["n"]
            entry = {
                "stock_code": code,
                "stock_name": name,
                "price": price,
                "market_cap": market_cap,
                "affected_rows": row_count,
                "error": error,
            }
            if is_alive:
                alive.append(entry)
            elif market_cap >= LARGE_CAP_THRESHOLD_KRW:
                # 조회 실패라도 대형주는 알려진 상장사로 복구 대상
                entry["note"] = "large-cap override"
                alive.append(entry)
            else:
                unconfirmed.append(entry)

    return alive, unconfirmed


def restore_stock(db: Database, stock_code: str) -> int:
    """한 종목의 performance_tracking 모든 오염 행을 미계산 상태로 되돌린다.

    Returns:
        UPDATE된 행 수.
    """
    conn = db._get_conn()
    cur = conn.execute(
        """UPDATE performance_tracking
           SET is_delisted = 0,
               delisted_detected_at = '',
               consecutive_fetch_failures = 0,
               price_after_1w = 0, return_1w = 0,
               price_after_1m = 0, return_1m = 0,
               price_after_3m = 0, return_3m = 0,
               price_after_6m = 0, return_6m = 0,
               price_after_1y = 0, return_1y = 0,
               signal_correct = 0,
               last_updated = ''
           WHERE stock_code = ? AND is_delisted = 1""",
        (stock_code,),
    )
    return cur.rowcount


def _format_entry(e: dict) -> str:
    cap = e.get("market_cap", 0)
    cap_str = f"{cap/1e8:,.0f}억" if cap else "n/a"
    parts = [
        f"  - {e['stock_code']} {e['stock_name'] or '(no name)':<12}",
        f"rows={e['affected_rows']}",
        f"mcap={cap_str}",
    ]
    if e.get("price"):
        parts.append(f"price={e['price']:,}")
    if e.get("note"):
        parts.append(f"[{e['note']}]")
    if e.get("error"):
        parts.append(f"err='{e['error']}'")
    return "  ".join(parts)


async def _amain(apply: bool, db_path: Optional[str]) -> int:
    db = Database(db_path=db_path) if db_path else Database()

    alive, unconfirmed = await probe_delisted_stocks(db)

    print("=" * 60)
    print("performance_tracking 복구 도구")
    print(f"모드: {'APPLY (실제 변경)' if apply else 'DRY-RUN (변경 없음)'}")
    print("=" * 60)
    print(f"is_delisted=1 종목 총 {len(alive) + len(unconfirmed)}건 발견")
    print()

    print(f"[확정 생존 / 복구 대상]: {len(alive)}종목")
    total_restorable_rows = sum(e["affected_rows"] for e in alive)
    for e in alive:
        print(_format_entry(e))
    print(f"  → 복구 시 영향 행 수: {total_restorable_rows}")
    print()

    print(f"[확정 불가 / 상폐 유지]: {len(unconfirmed)}종목")
    for e in unconfirmed:
        print(_format_entry(e))
    if unconfirmed:
        print(
            "  → 수동 확인 필요. 실제 상장폐지면 mark_stock_delisted로 재확정, "
            "정상이면 별도 디버그 후 이 스크립트 재실행."
        )
    print()

    if not apply:
        print("DRY-RUN 종료. 실제 적용: --apply")
        return 0

    # Apply
    applied_rows = 0
    for e in alive:
        n = restore_stock(db, e["stock_code"])
        applied_rows += n
        print(f"  복구: {e['stock_code']} → {n}행")
    db._get_conn().commit()
    print()
    print(f"복구 완료: {len(alive)}종목, {applied_rows}행 갱신")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m tools.recover_performance_tracking",
        description="2026-04-24 오염된 performance_tracking 복구 도구.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="실제 DB에 변경 적용 (없으면 dry-run)",
    )
    parser.add_argument(
        "--db-path", type=str, default=None,
        help="DB 경로 오버라이드 (기본: config.DBConfig.DB_PATH)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    code = asyncio.run(_amain(args.apply, args.db_path))
    sys.exit(code)


if __name__ == "__main__":
    main()

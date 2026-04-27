"""FCF 데이터 백필 — 기존 캐시(parquet)를 재파싱하여 free_cash_flow 보강.

배경: collectors/dart_api.py의 _get_account_value가 한글 account_nm 공백 변형
      매칭 실패로 OCF/CAPEX 결손 → FCF 64.5%가 0. account_id 우선 매칭 +
      공백 정규화 패치 후 캐시를 다시 읽어 DB만 갱신하면 됨 (DART API 재호출 불필요).

사용:
  dry-run (기본): python -m tools.backfill_fcf --year 2025
  실제 적용:      python -m tools.backfill_fcf --year 2025 --apply

영향 범위:
  financial_metrics 테이블의 free_cash_flow 컬럼만 갱신.
  다른 컬럼·테이블은 건드리지 않으며 캐시 파일은 읽기만 함.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from collectors.dart_api import DARTClient

logger = logging.getLogger("backfill_fcf")

CACHE_DIR = Path("data/dart_cache")
DB_PATH = "data/kospi_analyzer.db"

OCF_NAMES = ["영업활동현금흐름", "영업활동으로인한현금흐름"]
OCF_IDS = ["ifrs-full_CashFlowsFromUsedInOperatingActivities"]
CAPEX_NAMES = ["유형자산의취득", "유형자산취득", "투자활동으로인한유형자산취득"]
CAPEX_IDS = [
    "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    "ifrs-full_PurchaseOfPropertyPlantAndEquipment",
]


def _format_amount(v: int) -> str:
    if v == 0:
        return "0"
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1_000_000_000_000:
        return f"{sign}{a/1_000_000_000_000:,.2f}조"
    if a >= 100_000_000:
        return f"{sign}{a/100_000_000:,.0f}억"
    return f"{v:,}"


def _calc_fcf(client: DARTClient, df: pd.DataFrame) -> tuple[int, int, int]:
    ocf = client._get_account_value(df, "CF", OCF_NAMES, account_ids=OCF_IDS)
    capex = abs(client._get_account_value(df, "CF", CAPEX_NAMES, account_ids=CAPEX_IDS))
    fcf = ocf - capex if ocf != 0 else 0
    return ocf, capex, fcf


def _lookup_name(conn: sqlite3.Connection, code: str) -> str:
    row = conn.execute(
        "SELECT stock_name FROM stock_master WHERE stock_code=?", (code,)
    ).fetchone()
    if row and row["stock_name"]:
        return row["stock_name"]
    return "?"


def main() -> int:
    parser = argparse.ArgumentParser(description="DART FCF 백필")
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--apply", action="store_true", help="실제 DB 갱신")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--cache", default=str(CACHE_DIR))
    args = parser.parse_args()

    cache_dir = Path(args.cache)
    if not cache_dir.exists():
        print(f"ERROR: 캐시 디렉토리 없음: {cache_dir}")
        return 2

    client = DARTClient.__new__(DARTClient)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """SELECT stock_code, free_cash_flow, sector
           FROM financial_metrics
           WHERE year=? AND quarter='annual'""",
        (args.year,),
    ).fetchall()
    print(f"DB 대상 행 수: {len(rows)} (year={args.year}, quarter=annual)")

    affected: list[dict] = []
    no_cache: list[str] = []
    no_change: list[str] = []
    no_match: list[str] = []

    for r in rows:
        code = r["stock_code"]
        path = cache_dir / f"{code}_{args.year}_annual.parquet"
        if not path.exists():
            no_cache.append(code)
            continue
        try:
            df = pd.read_parquet(path)
        except Exception as e:
            logger.warning("캐시 읽기 실패 %s: %s", code, e)
            continue
        ocf, capex, new_fcf = _calc_fcf(client, df)
        if ocf == 0:
            no_match.append(code)
        old_fcf = int(r["free_cash_flow"] or 0)
        if new_fcf == old_fcf:
            no_change.append(code)
            continue
        affected.append({
            "code": code,
            "name": _lookup_name(conn, code),
            "sector": r["sector"] or "",
            "old_fcf": old_fcf,
            "new_fcf": new_fcf,
            "ocf": ocf,
            "capex": capex,
        })

    # 변화 분류
    recovered = sum(1 for a in affected if a["old_fcf"] == 0 and a["new_fcf"] > 0)
    new_neg   = sum(1 for a in affected if a["old_fcf"] == 0 and a["new_fcf"] < 0)
    shrunk    = sum(1 for a in affected if a["old_fcf"] > 0 and a["new_fcf"] < a["old_fcf"])
    grown     = sum(1 for a in affected if a["old_fcf"] >= 0 and a["new_fcf"] > a["old_fcf"] and a["old_fcf"] != 0)
    print(f"  → 보강 가능: {len(affected)}개")
    print(f"     · 결손 회복(0 → 양수):     {recovered}")
    print(f"     · 결손 → 음수 (진짜 음수): {new_neg}")
    print(f"     · 양수 → 더 작은 양수(CAPEX 차감): {shrunk}")
    print(f"     · 양수 → 더 큰 양수:        {grown}")
    print(f"  → 변경 없음:                {len(no_change)}개")
    print(f"  → 캐시 없음(다음 수집 필요): {len(no_cache)}개")
    print(f"  → 캐시는 있으나 OCF 매칭 0:  {len(no_match)}개")

    print("\n=== 표본 (앞 10개, 결손 회복 우선) ===")
    affected_sorted = sorted(
        affected, key=lambda a: (0 if a["old_fcf"] == 0 else 1, a["code"])
    )
    for a in affected_sorted[:10]:
        sec = f" ({a['sector']})" if a["sector"] else ""
        print(f"  [{a['code']}] {a['name']}{sec}")
        print(
            f"    fcf: {_format_amount(a['old_fcf']):>10s} "
            f"→ {_format_amount(a['new_fcf']):>10s}  "
            f"(ocf={_format_amount(a['ocf'])}, capex={_format_amount(a['capex'])})"
        )

    if no_cache:
        print(f"\n캐시 없는 종목 (앞 10): {no_cache[:10]}")

    if not args.apply:
        print("\n[DRY-RUN] --apply 없이 실행. DB 변경 없음.")
        return 0

    print(f"\n[APPLY] {len(affected)}개 행을 갱신합니다...")
    cur = conn.cursor()
    for a in affected:
        cur.execute(
            """UPDATE financial_metrics SET
                   free_cash_flow=?,
                   updated_at=datetime('now','localtime')
               WHERE stock_code=? AND year=? AND quarter='annual'""",
            (a["new_fcf"], a["code"], args.year),
        )
    conn.commit()
    print(f"DB 갱신 완료: {len(affected)}행")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())

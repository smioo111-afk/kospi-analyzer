"""DART 배당수익률 결손 백필 — 전년도(year-1) 폴백 호출 기반.

배경: financial_metrics(2025 annual) dividend_yield=0인 종목 67건. 원인은
      사업보고서 공시 시점에 배당 의사결정 미공시('-'). 전년도 사업보고서로
      1회 폴백하면 일부 회복 가능.

사용:
  dry-run (기본): python -m tools.backfill_dividend --year 2025
  실제 적용:      python -m tools.backfill_dividend --year 2025 --apply

DART API 호출이 필요 (캐시 없음). 결손 종목당 최대 2회 호출 (당해 + 전년).
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from collectors.dart_api import DARTClient

logger = logging.getLogger("backfill_dividend")
DB_PATH = "data/kospi_analyzer.db"


def main() -> int:
    parser = argparse.ArgumentParser(description="dividend_yield 결손 백필")
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--apply", action="store_true", help="실제 DB 갱신")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument(
        "--limit", type=int, default=0,
        help="처리할 결손 종목 수 (0=전수)",
    )
    args = parser.parse_args()

    client = DARTClient()
    client.load_corp_codes()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """SELECT stock_code, dividend_yield FROM financial_metrics
           WHERE year=? AND quarter='annual' AND dividend_yield = 0""",
        (args.year,),
    ).fetchall()

    if args.limit > 0:
        rows = rows[:args.limit]

    print(f"DB 결손(dividend_yield=0) 행 수: {len(rows)} (year={args.year})")
    print("DART alotMatter API 호출 시작 (각 종목당 최대 2회)...")

    recovered: list[tuple[str, float]] = []
    still_zero: list[str] = []
    no_corp: list[str] = []

    for r in rows:
        code = r["stock_code"]
        if not client.get_corp_code(code):
            no_corp.append(code)
            continue
        v = client._get_dividend_yield(code, args.year)
        if v > 0:
            recovered.append((code, v))
        else:
            still_zero.append(code)

    print(f"  → 회복 가능 (전년도 폴백): {len(recovered)}")
    print(f"  → 진짜 무배당 또는 미공시: {len(still_zero)}")
    print(f"  → corp_code 없음: {len(no_corp)}")

    print("\n=== 회복 표본 (앞 10) ===")
    for code, v in recovered[:10]:
        n = conn.execute(
            "SELECT stock_name FROM stock_master WHERE stock_code=?", (code,),
        ).fetchone()
        nm = n["stock_name"] if n else "?"
        print(f"  {code} {nm:<15s}  div_yield: 0 → {v}%")

    if not args.apply:
        print("\n[DRY-RUN] DB 변경 없음. --apply로 실제 갱신.")
        return 0

    cur = conn.cursor()
    for code, v in recovered:
        cur.execute(
            """UPDATE financial_metrics SET dividend_yield=?,
                   updated_at=datetime('now','localtime')
               WHERE stock_code=? AND year=? AND quarter='annual'""",
            (v, code, args.year),
        )
    conn.commit()
    print(f"\n[APPLY] {len(recovered)}행 dividend_yield 갱신 완료")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())

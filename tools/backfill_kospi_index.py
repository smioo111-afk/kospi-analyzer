"""KOSPI 종합지수 과거 N일 백필 — analysis_results.kospi_index 보강.

배경: main.py 분석 파이프라인이 이전에 KOSPI 지수를 수집하지 않아
      analysis_results.kospi_index가 100% 0. 묶음 D 패치로 오늘부터는
      KIS API에서 자동 수집되지만, 과거 행은 일회성 백필 필요.

데이터 소스: KIS inquire-daily-indexchartprice (TR FHKUP03500100, iscd '0001').

사용:
  dry-run: python -m tools.backfill_kospi_index --days 30
  실제 적용: python -m tools.backfill_kospi_index --days 30 --apply

동작:
  - 기존 analysis_results 행이 있으면 kospi_index 컬럼만 UPDATE.
  - 행이 없으면 INSERT 안 함 (분석 결과 자체가 없으면 백필할 의미 없음).
  - 휴장일은 KIS가 자동 제외.
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

from collectors.kis_api import KISClient

logger = logging.getLogger("backfill_kospi_index")
DB_PATH = "data/kospi_analyzer.db"


def main() -> int:
    parser = argparse.ArgumentParser(description="KOSPI 지수 백필")
    parser.add_argument("--days", type=int, default=30,
                        help="과거 며칠치 (기본 30일)")
    parser.add_argument("--apply", action="store_true", help="실제 DB 갱신")
    parser.add_argument("--db", default=DB_PATH)
    args = parser.parse_args()

    kis = KISClient()
    print(f"KIS daily-indexchartprice 호출: 최근 {args.days}일...")
    rows_kis = kis.get_kospi_daily_index(days=args.days)
    if not rows_kis:
        print("ERROR: KIS 응답 빈 결과")
        return 2
    closes: dict[str, float] = {r["date"]: r["close"] for r in rows_kis}
    print(f"거래일 {len(closes)}일치 받음.")

    # DB 백필 대상
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT analysis_date, kospi_index FROM analysis_results
           WHERE analysis_date >= date('now','localtime',? )
           ORDER BY analysis_date""",
        (f"-{args.days} days",),
    ).fetchall()

    print(f"\nDB analysis_results({args.days}일 내) 행 수: {len(rows)}")

    updates: list[tuple[str, float]] = []
    skip_no_close: list[str] = []
    already: list[str] = []
    for r in rows:
        d = r["analysis_date"]
        if r["kospi_index"]:
            already.append(d)
            continue
        c = closes.get(d)
        if c is None:
            skip_no_close.append(d)
            continue
        updates.append((d, c))

    print(f"  → 갱신 대상 (0 → KOSPI 종가): {len(updates)}")
    print(f"  → 이미 값 있음 (스킵): {len(already)}")
    print(f"  → KIS 종가 없음 (휴장 등): {len(skip_no_close)}")

    print("\n=== 표본 (앞 10) ===")
    for d, c in updates[:10]:
        print(f"  {d}  KOSPI = {c:,.2f}")

    if not args.apply:
        print("\n[DRY-RUN] DB 변경 없음. --apply로 실제 갱신.")
        return 0

    cur = conn.cursor()
    for d, c in updates:
        cur.execute(
            """UPDATE analysis_results SET kospi_index=?
               WHERE analysis_date=?""",
            (c, d),
        )
    conn.commit()
    print(f"\n[APPLY] {len(updates)}행 KOSPI 지수 갱신 완료")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    sys.exit(main())

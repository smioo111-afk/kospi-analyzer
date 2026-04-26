"""DART PL 데이터 백필 — 기존 캐시(parquet)를 재파싱하여 결손 행 보강.

배경: 캐시는 무결하나 _get_account_value의 IS-only 필터 버그로 K-IFRS
      단일 CIS 제출 종목 다수에서 PL이 0으로 저장됨. CIS fallback 패치
      후 캐시를 다시 읽어 DB만 갱신하면 됨 (DART API 재호출 불필요).

사용:
  dry-run (기본): python -m tools.backfill_dart_pl --year 2025
  실제 적용:      python -m tools.backfill_dart_pl --year 2025 --apply

영향 범위:
  financial_metrics 테이블의 revenue/operating_income/net_income/ebitda/
  depreciation/roe/operating_margin 컬럼만 갱신. 다른 컬럼·테이블은 건드리지 않음.
  캐시 파일은 읽기만 하며 수정·삭제하지 않음.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from collectors.dart_api import DARTClient

logger = logging.getLogger("backfill_dart_pl")

CACHE_DIR = Path("data/dart_cache")
DB_PATH = "data/kospi_analyzer.db"

REV_NAMES = ["매출액", "매출", "수익(매출액)", "영업수익"]
OP_NAMES = ["영업이익", "영업이익(손실)", "영업손익", "영업손실"]
NI_NAMES = ["당기순이익", "당기순이익(손실)"]
DEP_NAMES = ["감가상각비", "유무형자산상각비", "감가상각비와무형자산상각비"]


def _format_amount(v: int) -> str:
    if v == 0:
        return "0"
    if abs(v) >= 1_000_000_000_000:
        return f"{v/1_000_000_000_000:,.2f}조"
    if abs(v) >= 100_000_000:
        return f"{v/100_000_000:,.0f}억"
    return f"{v:,}"


def _extract_from_cache(
    client: DARTClient,
    df: pd.DataFrame,
    sector: str = "",
    stock_code: str = "",
) -> dict[str, int]:
    # 금융주(보험/증권/은행지주)는 합산 매출. 일반은 _get_account_value 기본.
    fin_rev = client._calc_financial_revenue(df, sector or None, stock_code)
    if fin_rev is not None:
        rev = fin_rev
    else:
        rev = client._get_account_value(df, "IS", REV_NAMES)
    op = client._get_account_value(df, "IS", OP_NAMES)
    ni = client._get_account_value(df, "IS", NI_NAMES)
    dep = client._get_account_value(df, "IS", DEP_NAMES)
    if dep == 0:
        dep = client._get_account_value(df, "CF", DEP_NAMES)
    ebitda = op + abs(dep) if op else 0
    return {
        "revenue": rev,
        "operating_income": op,
        "net_income": ni,
        "depreciation": dep,
        "ebitda": ebitda,
    }


def _calc_ratio(num: int, den: int) -> float:
    if not den:
        return 0.0
    return round(num / den * 100, 2)


def main() -> int:
    parser = argparse.ArgumentParser(description="DART PL 결손 백필")
    parser.add_argument("--year", type=int, default=2025, help="대상 사업연도")
    parser.add_argument("--apply", action="store_true", help="실제 DB 갱신")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--cache", default=str(CACHE_DIR))
    parser.add_argument(
        "--include-financial", action="store_true",
        help="금융주(보험/증권/은행지주) sector 분기 합산 매출 적용. "
             "WHERE 조건도 확장하여 부분 결손(매출만 0) 행도 대상으로 함.",
    )
    args = parser.parse_args()

    cache_dir = Path(args.cache)
    if not cache_dir.exists():
        print(f"ERROR: 캐시 디렉토리 없음: {cache_dir}")
        return 2

    client = DARTClient()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    if args.include_financial:
        # 금융주는 매출이 합산 라인이라 영업이익/순이익이 정상이어도 매출만 결손인 케이스가 다수.
        # WHERE 완화: revenue=0이거나 (sector가 금융업이고 revenue가 합산 후 더 큼).
        # 단순화: 금융주 모든 행 + 일반 결손 행을 포함.
        rows = conn.execute(
            """SELECT * FROM financial_metrics
               WHERE year=? AND quarter='annual'
                 AND (
                   (revenue=0 AND operating_income=0 AND net_income=0)
                   OR sector IN ('보험','증권','금융')
                 )""",
            (args.year,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM financial_metrics
               WHERE year=? AND quarter='annual'
                 AND revenue=0 AND operating_income=0 AND net_income=0""",
            (args.year,),
        ).fetchall()

    print(f"DB 대상 행 수: {len(rows)} (year={args.year}, quarter=annual, "
          f"include_financial={args.include_financial})")

    affected: list[dict[str, Any]] = []
    no_cache: list[str] = []
    still_zero: list[str] = []
    no_change: list[str] = []

    for r in rows:
        code = r["stock_code"]
        sector = r["sector"] or ""
        path = cache_dir / f"{code}_{args.year}_annual.parquet"
        if not path.exists():
            no_cache.append(code)
            continue
        try:
            df = pd.read_parquet(path)
        except Exception as e:
            logger.warning("캐시 읽기 실패 %s: %s", code, e)
            continue
        if df.empty:
            still_zero.append(code)
            continue
        new = _extract_from_cache(client, df, sector=sector, stock_code=code)
        if new["revenue"] == 0 and new["operating_income"] == 0 and new["net_income"] == 0:
            still_zero.append(code)
            continue

        equity = int(r["total_equity"] or 0)
        new_roe = _calc_ratio(new["net_income"], equity)
        new_opm = _calc_ratio(new["operating_income"], new["revenue"])

        before = {
            "revenue": int(r["revenue"] or 0),
            "operating_income": int(r["operating_income"] or 0),
            "net_income": int(r["net_income"] or 0),
            "ebitda": int(r["ebitda"] or 0),
        }
        after_pl = {
            "revenue": new["revenue"],
            "operating_income": new["operating_income"],
            "net_income": new["net_income"],
            "ebitda": new["ebitda"],
        }
        # 변화 없는 행은 affected에서 제외 (일반 지주/비금융 회귀 방지 검증).
        if before == after_pl:
            no_change.append(code)
            continue

        affected.append({
            "code": code,
            "name": _lookup_name(conn, code),
            "sector": sector,
            "before": {
                **before,
                "roe": float(r["roe"] or 0.0),
                "operating_margin": float(r["operating_margin"] or 0.0),
            },
            "after": {
                **new,
                "roe": new_roe,
                "operating_margin": new_opm,
            },
        })

    print(f"  → 캐시로 보강 가능: {len(affected)}개")
    print(f"  → 변경 없음 (이미 정상 또는 룰 동일): {len(no_change)}개")
    print(f"  → 캐시 없음(다음 수집 사이클 필요): {len(no_cache)}개")
    print(f"  → 캐시는 있으나 재추출도 0(BS만 있는 비영업회사 등): {len(still_zero)}개")

    print("\n=== 표본 (앞 8개) ===")
    for a in affected[:8]:
        sec_tag = f" ({a['sector']})" if a.get('sector') else ""
        print(f"  [{a['code']}] {a['name']}{sec_tag}")
        b, n = a["before"], a["after"]
        print(
            f"    revenue:  {_format_amount(b['revenue']):>12s} → {_format_amount(n['revenue']):>12s}"
        )
        print(
            f"    op_inc :  {_format_amount(b['operating_income']):>12s} → {_format_amount(n['operating_income']):>12s}"
        )
        print(
            f"    net_inc:  {_format_amount(b['net_income']):>12s} → {_format_amount(n['net_income']):>12s}"
        )
        print(
            f"    ebitda :  {_format_amount(b['ebitda']):>12s} → {_format_amount(n['ebitda']):>12s}"
        )
        print(f"    roe   :  {b['roe']:>10.2f}% → {n['roe']:>10.2f}%")
        print(f"    op_mgn:  {b['operating_margin']:>10.2f}% → {n['operating_margin']:>10.2f}%")

    if no_cache:
        print(f"\n캐시 없는 종목 (앞 10): {no_cache[:10]}")
    if still_zero:
        print(f"재추출 후에도 0인 종목 (앞 10): {still_zero[:10]}")

    if not args.apply:
        print("\n[DRY-RUN] --apply 없이 실행. DB 변경 없음.")
        return 0

    print(f"\n[APPLY] {len(affected)}개 행을 갱신합니다...")
    cur = conn.cursor()
    for a in affected:
        n = a["after"]
        cur.execute(
            """UPDATE financial_metrics SET
                   revenue=?, operating_income=?, net_income=?,
                   ebitda=?, depreciation=?,
                   roe=?, operating_margin=?,
                   updated_at=datetime('now','localtime')
               WHERE stock_code=? AND year=? AND quarter='annual'""",
            (
                n["revenue"], n["operating_income"], n["net_income"],
                n["ebitda"], n["depreciation"],
                n["roe"], n["operating_margin"],
                a["code"], args.year,
            ),
        )
    conn.commit()
    print(f"DB 갱신 완료: {cur.rowcount}행 (executemany 미사용으로 마지막 row 기준)")
    print(f"실제 갱신 의도: {len(affected)}행")
    return 0


def _lookup_name(conn: sqlite3.Connection, code: str) -> str:
    row = conn.execute(
        "SELECT stock_name FROM stock_master WHERE stock_code=?",
        (code,),
    ).fetchone()
    if row and row["stock_name"]:
        return row["stock_name"]
    return "?"


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())

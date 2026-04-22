"""
async KIS 클라이언트 드라이런 (Phase 5)

실 KIS 모의투자 API를 블루칩 5종목으로 검증한다.
  - 토큰 발급/캐시
  - 단건 조회: aget_stock_price, aget_daily_chart, aget_investor_trading
  - 배치 조회: aget_all_stock_prices + aget_all_daily_charts + aget_all_investor_trading
  - Rate limit 실동작
  - 총 소요 시간

DB 저장·텔레그램 발송 없음. 파이프라인 나머지 모듈 건드리지 않음.

실행: python -m tools.dry_run_async
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors.kis_api import KISClient, KISAPIError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("dry_run")

# 블루칩 5종목 (샘플)
CODES = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "005380",  # 현대차
    "035720",  # 카카오
    "051910",  # LG화학
]


def _print_sep(title: str) -> None:
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


async def main() -> int:
    _print_sep("1. 토큰 확인")
    async with KISClient() as kis:
        ok = await kis.acheck_token()
        if not ok:
            print("❌ 토큰 발급 실패. .env 확인 후 재시도")
            return 1
        print("✓ 토큰 유효")

        _print_sep("2. 단건: 삼성전자 현재가")
        t0 = time.monotonic()
        try:
            p = await kis.aget_stock_price("005930")
            print(
                f"✓ 현재가={p['current_price']:,}원 "
                f"PER={p['per']} PBR={p['pbr']} "
                f"시총={p['market_cap']:,}원")
        except KISAPIError as e:
            print(f"❌ 조회 실패: {e}")
            return 1

        _print_sep("3. 단건: 삼성전자 일봉 (최근 10일)")
        try:
            chart = await kis.aget_daily_chart("005930", days=10)
            print(f"✓ {len(chart)}일치 수신")
            for c in chart[:3]:
                print(
                    f"  {c['date']}: 시{c['open']:,} "
                    f"종{c['close']:,} 거래량{c['volume']:,}")
        except KISAPIError as e:
            print(f"❌ 일봉 조회 실패: {e}")
            return 1

        _print_sep("4. 단건: 삼성전자 투자자별 매매동향")
        try:
            inv = await kis.aget_investor_trading("005930", days=25)
            print(
                f"✓ 외국인 연속매수 {inv['foreign_net_buy_days']}일, "
                f"기관 연속매수 {inv['institutional_net_buy_days']}일")
            print(
                f"  외국인 5d={inv['foreign_net_buy_5d']}, "
                f"20d={inv['foreign_net_buy_20d']} trend={inv['foreign_trend']}")
        except KISAPIError as e:
            print(f"❌ 수급 조회 실패: {e}")
            return 1

        _print_sep(f"5. 배치: {len(CODES)}종목 병렬 (prices+charts+investors)")
        t1 = time.monotonic()
        price_task = kis.aget_all_stock_prices(CODES)
        chart_task = kis.aget_all_daily_charts(CODES, days=60)
        inv_task = kis.aget_all_investor_trading(CODES, days=25)
        try:
            prices, charts, investors = await asyncio.gather(
                price_task, chart_task, inv_task)
        except Exception as e:
            print(f"❌ 배치 조회 실패: {e}")
            return 1
        t2 = time.monotonic()

        print(f"✓ prices {len(prices)}/{len(CODES)}, "
              f"charts {len(charts)}/{len(CODES)}, "
              f"investors {len(investors)}/{len(CODES)}")
        print(f"  총 {len(CODES)*3}콜, 소요 {t2 - t1:.2f}s, "
              f"실효 rate {(len(CODES)*3)/(t2-t1):.2f} calls/s")
        for p in prices[:3]:
            print(f"  {p['stock_code']} {p['stock_name']}: "
                  f"{p['current_price']:,}원 PER={p['per']}")

    total = time.monotonic() - t0
    _print_sep("요약")
    print(f"전체 드라이런 소요: {total:.2f}s")
    print("DB 저장/텔레그램 발송 없음. 파이프라인 나머지 단계 미실행.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

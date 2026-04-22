"""
async KIS 클라이언트 벤치마크 (Phase 4)

KIS API를 aioresponses로 mock하여 실제 네트워크 없이 rate limiter
동작·처리량을 측정한다.

시나리오:
  - 배치 크기: 10 / 50 / 200 종목
  - rate_limit_per_sec: 2 (롤백 시 sync 속도) vs 15 (권장 async)
  - 호출 종류: aget_all_stock_prices + aget_all_daily_charts

각 조합의 소요 시간·실효 rate·성공률을 표로 출력하고,
docs/async_benchmark_results.md에 저장한다.

실행:
  python -m tools.benchmark_collect
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aioresponses import aioresponses

from collectors.kis_api import KISClient
from config.settings import KISConfig


# ================================================================
# Mock 응답 생성기
# ================================================================
def _price_payload(code: str) -> dict:
    return {
        "rt_cd": "0",
        "output": {
            "hts_kor_isnm": f"Stock_{code}",
            "bstp_kor_isnm": "전기·전자",
            "stck_prpr": "50000",
            "prdy_ctrt": "1.0",
            "acml_vol": "1000000",
            "acml_tr_pbmn": "50000000000",
            "hts_avls": "3000000",
            "per": "10.0",
            "pbr": "1.0",
            "eps": "5000",
            "bps": "50000",
            "stck_dryc_hgpr": "60000",
            "stck_dryc_lwpr": "40000",
        },
    }


def _chart_payload(code: str) -> dict:
    # 60일치 단순 캔들
    today = datetime.now()
    output2 = []
    for i in range(60):
        d = today - timedelta(days=i)
        output2.append({
            "stck_bsop_date": d.strftime("%Y%m%d"),
            "stck_oprc": "49500",
            "stck_hgpr": "50500",
            "stck_lwpr": "49000",
            "stck_clpr": "50000",
            "acml_vol": "1000000",
        })
    return {"rt_cd": "0", "output2": output2}


def _register_mocks(m: aioresponses, codes: list[str]) -> None:
    base = KISConfig.BASE_URL
    for code in codes:
        m.get(
            f"{base}/uapi/domestic-stock/v1/quotations/inquire-price"
            f"?FID_COND_MRKT_DIV_CODE=J&FID_INPUT_ISCD={code}",
            payload=_price_payload(code),
        )
        # 일봉 URL은 쿼리스트링이 복잡하므로 prefix 정규식 매칭
        pass
    # 일봉은 파라미터 순서가 매번 달라질 수 있어 전체 endpoint를 regex로
    import re
    m.get(
        re.compile(
            r".*inquire-daily-itemchartprice.*",
        ),
        payload=_chart_payload("_"),
        repeat=True,
    )


def _install_fake_token(client: KISClient) -> None:
    client._token_manager._access_token = "fake-token"
    client._token_manager._token_expired_at = (
        datetime.now() + timedelta(hours=24))


# ================================================================
# 단일 벤치 실행
# ================================================================
async def _bench(
    n_codes: int,
    rate: int,
    fail_threshold: float = 0.5,
) -> dict:
    codes = [f"{i:06d}" for i in range(1, n_codes + 1)]

    with aioresponses() as m:
        _register_mocks(m, codes)
        async with KISClient(
            rate_limit_per_sec=rate,
            fail_threshold=fail_threshold,
        ) as kis:
            _install_fake_token(kis)

            t0 = time.monotonic()
            prices = await kis.aget_all_stock_prices(codes)
            t1 = time.monotonic()
            charts = await kis.aget_all_daily_charts(codes, days=60)
            t2 = time.monotonic()

    price_elapsed = t1 - t0
    chart_elapsed = t2 - t1
    total = t2 - t0

    # 각 배치는 N콜. 전체 2N콜.
    effective_rate = (2 * n_codes) / total if total > 0 else 0.0

    return {
        "n_codes": n_codes,
        "rate_limit": rate,
        "price_success": len(prices),
        "chart_success": len(charts),
        "price_elapsed_s": price_elapsed,
        "chart_elapsed_s": chart_elapsed,
        "total_elapsed_s": total,
        "effective_calls_per_sec": effective_rate,
    }


def _theoretical_min(n_calls: int, rate: int) -> float:
    """토큰 버킷 이론 최소 시간.

    초기 버킷 size=rate로 시작. 첫 rate개는 즉시 소비,
    나머지 (n-rate)개는 1/rate 간격으로 소비.
    """
    if n_calls <= rate:
        return 0.0
    return (n_calls - rate) / rate


# ================================================================
# 전체 벤치 실행 + 리포트
# ================================================================
def _format_row(r: dict, theory_min: float) -> str:
    return (
        f"| {r['n_codes']:>6} | {r['rate_limit']:>4} | "
        f"{r['total_elapsed_s']:>8.2f}s | "
        f"{theory_min:>7.2f}s | "
        f"{r['effective_calls_per_sec']:>8.2f} | "
        f"{r['price_success']}/{r['n_codes']} | "
        f"{r['chart_success']}/{r['n_codes']} |"
    )


async def _bench_production(
    n_codes: int,
    rate: int,
    fail_threshold: float = 0.5,
) -> dict:
    """main.py 운영 패턴과 동일하게 3개 배치를 concurrent gather로 실행.

    aget_all_stock_prices + aget_all_daily_charts + aget_all_investor_trading
    모두 같은 KISClient 인스턴스의 _limiter를 공유.
    """
    codes = [f"{i:06d}" for i in range(1, n_codes + 1)]
    with aioresponses() as m:
        _register_mocks(m, codes)
        # 수급(inquire-investor)도 regex로 매칭
        import re
        m.get(
            re.compile(r".*inquire-investor.*"),
            payload={"rt_cd": "0", "output": []},
            repeat=True,
        )
        async with KISClient(
            rate_limit_per_sec=rate,
            fail_threshold=fail_threshold,
        ) as kis:
            _install_fake_token(kis)
            t0 = time.monotonic()
            prices_t = kis.aget_all_stock_prices(codes)
            charts_t = kis.aget_all_daily_charts(codes, days=60)
            inv_t = kis.aget_all_investor_trading(codes, days=25)
            prices, charts, investors = await asyncio.gather(
                prices_t, charts_t, inv_t)
            t1 = time.monotonic()
    total = t1 - t0
    n_calls = 3 * n_codes
    return {
        "n_codes": n_codes,
        "n_calls": n_calls,
        "rate_limit": rate,
        "elapsed_s": total,
        "effective_calls_per_sec": n_calls / total if total > 0 else 0.0,
        "prices": len(prices),
        "charts": len(charts),
        "investors": len(investors),
    }


async def main() -> None:
    # rate=2는 200코드 기준 ~200초 소요 예상. 50까지만.
    cases = [
        (10, 2),
        (50, 2),
        (10, 15),
        (50, 15),
        (200, 15),
    ]

    results: list[dict] = []
    for n, rate in cases:
        print(f"\n=== 벤치: N={n}, rate={rate}/sec ===", flush=True)
        res = await _bench(n, rate)
        results.append(res)
        print(
            f"  소요 {res['total_elapsed_s']:.2f}s | "
            f"실효 rate {res['effective_calls_per_sec']:.2f} calls/s | "
            f"prices {res['price_success']}/{n}, "
            f"charts {res['chart_success']}/{n}",
            flush=True,
        )

    # === 운영 시뮬레이션: 932 종목 × 3 API = 2796 콜 ===
    print("\n=== 운영 시뮬레이션: 932 종목 × 3 API (concurrent gather) ===",
          flush=True)
    prod = await _bench_production(932, 15)
    print(
        f"  N=932, rate=15, 총 {prod['n_calls']}콜, "
        f"소요 {prod['elapsed_s']:.2f}s, "
        f"실효 {prod['effective_calls_per_sec']:.2f} calls/s, "
        f"결과 prices={prod['prices']} charts={prod['charts']} "
        f"investors={prod['investors']}",
        flush=True,
    )

    # 리포트 생성
    header = (
        "| N codes | rate | elapsed | theory min "
        "| eff calls/s | price OK | chart OK |\n"
        "|--------:|-----:|--------:|-----------:|"
        "------------:|:--------:|:--------:|"
    )
    rows = []
    for r in results:
        # 2N콜의 이론 최소 시간
        tmin = _theoretical_min(2 * r["n_codes"], r["rate_limit"])
        rows.append(_format_row(r, tmin))

    # 운영 시뮬레이션용 이론값
    prod_theory = _theoretical_min(prod["n_calls"], prod["rate_limit"])
    prod_row = (
        f"| {prod['n_codes']:>6} | {prod['rate_limit']:>4} | "
        f"{prod['elapsed_s']:>8.2f}s | {prod_theory:>7.2f}s | "
        f"{prod['effective_calls_per_sec']:>8.2f} | "
        f"{prod['prices']}/{prod['n_codes']} (p) + "
        f"{prod['charts']}/{prod['n_codes']} (c) + "
        f"{prod['investors']}/{prod['n_codes']} (i) |"
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report = f"""# async 벤치마크 결과

측정 시각: {now}
환경: aioresponses mock (실 네트워크 없음), Python 3.10.12
도구: tools/benchmark_collect.py

## 목적

rate_limit_per_sec 설정이 실효 처리량에 미치는 영향과
aiolimiter 토큰 버킷의 이론값 대비 실측 괴리를 확인한다.
실 KIS API에 의존하지 않으므로 네트워크 지터·KIS 서버 응답
시간은 제외된 "클라이언트 한계" 측정이다.

## 결과

### 단일 배치 쌍 (prices + charts, 순차 실행)

{header}
{chr(10).join(rows)}

### 운영 시뮬레이션: 3 배치 concurrent gather (prices + charts + investors)

main.py `_collect_data`의 실제 패턴. 같은 KISClient 인스턴스의
`_limiter`를 3 배치가 공유하므로 총 rate는 rate_limit_per_sec로 강제된다.

| N codes | rate | elapsed | theory min | eff calls/s | success |
|--------:|-----:|--------:|-----------:|------------:|:-------:|
{prod_row}

### 필드 정의

- **elapsed**: `aget_all_stock_prices + aget_all_daily_charts` 총 소요 시간
- **theory min**: 토큰 버킷 이론 최소 시간
  - 초기 버킷 크기 = rate (burst 허용)
  - 첫 rate개는 즉시 소비, 이후 (N-rate)/rate 초
  - 2N콜 (prices + charts) 기준
- **eff calls/s**: `(2 × N) / elapsed`, 실측 처리량
- **OK 비율**: 성공 건수 / 요청 건수. mock이라 항상 100%.

### 해석 가이드

1. N이 rate보다 크면 총 소요 ≈ theory min. 오버헤드는
   (elapsed - theory_min) / N 로 계산되는 콜당 상수.
2. rate=2 vs rate=15의 배율은 이론상 7.5배. 실측도 비슷해야 정상.
3. 실제 KIS 환경에서는 네트워크 응답 시간이 더해지지만, 현재
   파이프라인 병목은 rate limit이지 네트워크 지연이 아니라는
   것이 주요 가정 (초당 2콜은 명백히 rate-bound).

## 운영 환경 예상 (932 종목 기준, 월요일 전종목 스캔)

PER/PBR 보강 + 차트 + 수급 ≒ **3N 콜 = 2796 콜**.
- rate=2  (롤백): 이론 (2796-2)/2 ≈ 1397s ≒ 23분
- rate=15 (기본): 이론 (2796-15)/15 ≈ 185s ≒ 3분
- 실제 KIS는 네트워크 응답 평균 100-200ms 가산. 3개 배치가
  gather로 병렬 실행되면 네트워크 지연은 rate 기다림과 겹쳐
  대부분 숨겨짐. 최종 예상 3~4분.

현 운영 로그(2026-04-07): 203 종목 `_collect_data` 18분 중 KIS
네트워크 대기 ~7분. async 전환 후 **7분 → ~2분** 예측.
DART 재무 11분은 변동 없음 (out-of-scope).

## 롤백 경로 검증

`KIS_RATE_LIMIT_PER_SEC=2` 환경변수로 기존 sync 속도 수준을
재현할 수 있음을 벤치에서 확인(rate=2 case).
"""

    report_path = Path("docs/async_benchmark_results.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"\n리포트 저장: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())

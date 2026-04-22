"""
KIS Async Client 단위 테스트 (Phase 2-①)

pytest-asyncio + aioresponses 기반. 네트워크 호출 없이
aiohttp 레이어를 mock한다.

실행: pytest tests/test_kis_async.py -v
"""

import asyncio
import os
import sys
import time

import pytest
from aioresponses import aioresponses

sys.path.insert(0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.kis_api import (
    KISAPIError,
    KISBatchFailureError,
    KISClient,
)
from config.settings import KISConfig


# 테스트 전용 토큰 주입 헬퍼
def _install_fake_token(client: KISClient) -> None:
    """토큰 발급을 스킵하고 가짜 토큰을 주입한다."""
    from datetime import datetime, timedelta
    client._token_manager._access_token = "fake-token"
    client._token_manager._token_expired_at = (
        datetime.now() + timedelta(hours=24))


BASE = KISConfig.BASE_URL


def _price_url() -> str:
    return (f"{BASE}/uapi/domestic-stock/v1/quotations/inquire-price"
            "?FID_COND_MRKT_DIV_CODE=J&FID_INPUT_ISCD=005930")


def _price_response(code: str = "005930", price: int = 70000) -> dict:
    return {
        "rt_cd": "0",
        "output": {
            "hts_kor_isnm": "삼성전자",
            "bstp_kor_isnm": "전기·전자",
            "stck_prpr": str(price),
            "prdy_ctrt": "1.5",
            "acml_vol": "1000000",
            "acml_tr_pbmn": "50000000000",
            "hts_avls": "5000000",
            "per": "15.0",
            "pbr": "1.2",
            "eps": "5000",
            "bps": "60000",
            "stck_dryc_hgpr": "80000",
            "stck_dryc_lwpr": "60000",
        },
    }


# ================================================================
# 1. 단건 조회 성공
# ================================================================
@pytest.mark.asyncio
async def test_single_price_fetch_success():
    with aioresponses() as m:
        m.get(_price_url(), payload=_price_response())
        async with KISClient(rate_limit_per_sec=100) as kis:
            _install_fake_token(kis)
            result = await kis.aget_stock_price("005930")
    assert result["stock_code"] == "005930"
    assert result["stock_name"] == "삼성전자"
    assert result["current_price"] == 70000
    assert result["market_cap"] == 5_000_000 * 100_000_000


# ================================================================
# 2. Rate limit 실제 적용
# ================================================================
@pytest.mark.asyncio
async def test_rate_limit_enforced():
    """rate_limit_per_sec=5로 20콜 → 최소 (20-5)/5 = 3초 소요."""
    with aioresponses() as m:
        # aioresponses: 같은 URL 반복 matching 위해 repeat=True
        m.get(_price_url(), payload=_price_response(), repeat=True)
        async with KISClient(rate_limit_per_sec=5) as kis:
            _install_fake_token(kis)
            start = time.monotonic()
            tasks = [kis.aget_stock_price("005930") for _ in range(20)]
            await asyncio.gather(*tasks)
            elapsed = time.monotonic() - start
    # 20콜을 초당 5콜로 뿌리면 이론상 3~4초 소요.
    # 허용 오차: 최소 2.5초 (버킷 초기 여유 감안).
    assert elapsed >= 2.5, f"rate가 안 지켜짐: elapsed={elapsed:.2f}s"
    assert elapsed < 10.0, f"비정상적으로 느림: elapsed={elapsed:.2f}s"


# ================================================================
# 3. 배치 부분 실패 (임계치 이하)
# ================================================================
@pytest.mark.asyncio
async def test_batch_partial_failure_below_threshold():
    """10개 중 1개 실패 (10%) → 임계치 20% 이하 → 정상 반환."""
    codes = [f"{i:06d}" for i in range(1, 11)]
    with aioresponses() as m:
        for i, code in enumerate(codes):
            url = (f"{BASE}/uapi/domestic-stock/v1/quotations/inquire-price"
                   f"?FID_COND_MRKT_DIV_CODE=J&FID_INPUT_ISCD={code}")
            if i == 0:
                # 첫 종목은 모든 재시도에서 500 (MAX_RETRIES+1 회 대비).
                m.get(url, status=500, repeat=True)
            else:
                m.get(url, payload=_price_response(code, 10000 + i))
        async with KISClient(
            rate_limit_per_sec=100, fail_threshold=0.2,
        ) as kis:
            _install_fake_token(kis)
            results = await kis.aget_all_stock_prices(codes)
    assert len(results) == 9  # 1건 실패, 9건 성공


# ================================================================
# 4. 배치 실패율 초과 → KISBatchFailureError
# ================================================================
@pytest.mark.asyncio
async def test_batch_failure_above_threshold_raises():
    """10개 중 3개 실패 (30%) → 임계치 20% 초과 → raise."""
    codes = [f"{i:06d}" for i in range(1, 11)]
    with aioresponses() as m:
        for i, code in enumerate(codes):
            url = (f"{BASE}/uapi/domestic-stock/v1/quotations/inquire-price"
                   f"?FID_COND_MRKT_DIV_CODE=J&FID_INPUT_ISCD={code}")
            if i < 3:
                m.get(url, status=500, repeat=True)
            else:
                m.get(url, payload=_price_response(code, 10000 + i))
        async with KISClient(
            rate_limit_per_sec=100, fail_threshold=0.2,
        ) as kis:
            _install_fake_token(kis)
            with pytest.raises(KISBatchFailureError) as excinfo:
                await kis.aget_all_stock_prices(codes)
    assert "30" in str(excinfo.value) or "3/10" in str(excinfo.value)


# ================================================================
# 5. 토큰 동시 갱신 직렬화
# ================================================================
@pytest.mark.asyncio
async def test_token_concurrent_refresh_single_request(monkeypatch):
    """여러 async 태스크가 동시에 토큰을 요청해도 발급은 1회만."""
    call_count = {"n": 0}

    def fake_issue(self):
        call_count["n"] += 1
        from datetime import datetime, timedelta
        self._access_token = f"tok-{call_count['n']}"
        self._token_expired_at = datetime.now() + timedelta(hours=24)

    monkeypatch.setattr(
        "collectors.kis_api.KISTokenManager._issue_new_token", fake_issue)

    async with KISClient() as kis:
        # 캐시/토큰 초기화하여 반드시 발급 경로 타게
        kis._token_manager._access_token = ""
        kis._token_manager._token_cache_path = (
            kis._token_manager._token_cache_path.parent
            / "nonexistent_test.json")

        async def _get():
            return await kis._token_manager.get_token_async()

        results = await asyncio.gather(*[_get() for _ in range(10)])
    assert call_count["n"] == 1, f"토큰 발급 {call_count['n']}회 발생"
    # 모든 호출이 같은 토큰 반환
    assert len(set(results)) == 1


# ================================================================
# 6. 재시도 소진 → KISAPIError
# ================================================================
@pytest.mark.asyncio
async def test_retry_exhausted_raises(monkeypatch):
    """HTTP 500 반복 → MAX_RETRIES 후 KISAPIError.

    RETRY_BACKOFF_BASE를 1.0으로 patch하여 백오프 0초로 시간 단축.
    """
    monkeypatch.setattr(KISConfig, "RETRY_BACKOFF_BASE", 1.0)
    with aioresponses() as m:
        m.get(_price_url(), status=500, repeat=True)
        async with KISClient(rate_limit_per_sec=100) as kis:
            _install_fake_token(kis)
            with pytest.raises(KISAPIError):
                await kis.aget_stock_price("005930")


# ================================================================
# 7. async with 밖에서 호출 → RuntimeError
# ================================================================
@pytest.mark.asyncio
async def test_call_outside_context_manager_raises():
    kis = KISClient(rate_limit_per_sec=100)
    _install_fake_token(kis)
    with pytest.raises(RuntimeError, match="async with"):
        await kis.aget_stock_price("005930")


# ================================================================
# 8. 빈 종목 리스트 배치 호출
# ================================================================
@pytest.mark.asyncio
async def test_batch_empty_codes_returns_empty():
    async with KISClient(rate_limit_per_sec=100) as kis:
        _install_fake_token(kis)
        prices = await kis.aget_all_stock_prices([])
        charts = await kis.aget_all_daily_charts([])
        investors = await kis.aget_all_investor_trading([])
    assert prices == []
    assert charts == {}
    assert investors == {}


# ================================================================
# 9. 환경변수로 rate 오버라이드
# ================================================================
def test_env_var_overrides_default_rate(monkeypatch):
    from collectors.kis_api import _default_rate_limit
    monkeypatch.setenv("KIS_RATE_LIMIT_PER_SEC", "3")
    assert _default_rate_limit() == 3
    monkeypatch.setenv("KIS_RATE_LIMIT_PER_SEC", "not_a_number")
    assert _default_rate_limit() == 15
    monkeypatch.delenv("KIS_RATE_LIMIT_PER_SEC", raising=False)
    assert _default_rate_limit() == 15

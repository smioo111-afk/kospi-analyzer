"""
KIS Sync 래퍼 하위 호환 테스트 (Phase 2-②)

기존 sync 호출부(database/models.py, main.py 일부)가 그대로
동작하는지 검증한다. aioresponses로 aiohttp 레이어만 mock.

실행: pytest tests/test_kis_sync_compat.py -v
"""

import asyncio
import os
import sys

import pytest
from aioresponses import aioresponses

sys.path.insert(0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.kis_api import KISClient, _run_sync
from config.settings import KISConfig

BASE = KISConfig.BASE_URL


def _install_fake_token(client: KISClient) -> None:
    from datetime import datetime, timedelta
    client._token_manager._access_token = "fake-token"
    client._token_manager._token_expired_at = (
        datetime.now() + timedelta(hours=24))


def _price_payload(price: int = 70000) -> dict:
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


def _price_url(code: str) -> str:
    return (f"{BASE}/uapi/domestic-stock/v1/quotations/inquire-price"
            f"?FID_COND_MRKT_DIV_CODE=J&FID_INPUT_ISCD={code}")


# ================================================================
# 1. sync 단건 호출 — 루프 없는 컨텍스트
# ================================================================
def test_sync_get_stock_price_outside_loop():
    with aioresponses() as m:
        m.get(_price_url("005930"), payload=_price_payload(70000))
        kis = KISClient(rate_limit_per_sec=100)
        _install_fake_token(kis)
        result = kis.get_stock_price("005930")
    assert result["stock_code"] == "005930"
    assert result["current_price"] == 70000


# ================================================================
# 2. sync 배치 호출
# ================================================================
def test_sync_get_all_stock_prices():
    codes = ["000001", "000002", "000003"]
    with aioresponses() as m:
        for i, c in enumerate(codes):
            m.get(_price_url(c), payload=_price_payload(1000 + i))
        kis = KISClient(rate_limit_per_sec=100)
        _install_fake_token(kis)
        results = kis.get_all_stock_prices(codes)
    assert len(results) == 3
    assert {r["stock_code"] for r in results} == set(codes)


# ================================================================
# 3. sync check_token (네트워크 없이 캐시 사용)
# ================================================================
def test_sync_check_token_with_cached_token():
    kis = KISClient(rate_limit_per_sec=100)
    _install_fake_token(kis)
    assert kis.check_token() is True


# ================================================================
# 4. sync 래퍼를 async 루프 안에서 호출 → RuntimeError
# ================================================================
@pytest.mark.asyncio
async def test_sync_wrapper_inside_running_loop_raises():
    kis = KISClient(rate_limit_per_sec=100)
    _install_fake_token(kis)
    with pytest.raises(RuntimeError, match="실행 중인 이벤트 루프"):
        kis.get_stock_price("005930")


# ================================================================
# 5. _run_sync 헬퍼 직접 검증
# ================================================================
def test_run_sync_outside_loop():
    async def _c():
        return 42
    assert _run_sync(_c()) == 42


@pytest.mark.asyncio
async def test_run_sync_inside_loop_raises():
    async def _c():
        return 42
    with pytest.raises(RuntimeError):
        _run_sync(_c())


# ================================================================
# 6. 연속 sync 호출 — 세션이 매번 재사용되지는 않지만 정상 동작
# ================================================================
def test_sync_multiple_calls_reuse_client():
    with aioresponses() as m:
        # 같은 URL로 2회 호출 → 각각 응답 등록
        m.get(_price_url("005930"), payload=_price_payload(70000))
        m.get(_price_url("005930"), payload=_price_payload(71000))
        kis = KISClient(rate_limit_per_sec=100)
        _install_fake_token(kis)
        r1 = kis.get_stock_price("005930")
        r2 = kis.get_stock_price("005930")
    assert r1["current_price"] == 70000
    assert r2["current_price"] == 71000


# ================================================================
# 7. models.py가 KISClient의 get_stock_price를 호출하는 시나리오 smoke
# ================================================================
def test_models_update_performance_tracking_smoke():
    """database.models.Database.update_performance_tracking 경로 smoke.

    실제 시그니처는 `get_stock_price` 하나만 사용. 여기서는
    실패 예외가 전파되는지와 성공 시 current_price 필드가
    접근 가능한지만 확인한다.
    """
    with aioresponses() as m:
        m.get(_price_url("999999"), payload=_price_payload(12345))
        kis = KISClient(rate_limit_per_sec=100)
        _install_fake_token(kis)
        # models.py:1227의 호출 형태 그대로
        price_data = kis.get_stock_price("999999")
        current_price = price_data.get("current_price", 0)
    assert current_price == 12345

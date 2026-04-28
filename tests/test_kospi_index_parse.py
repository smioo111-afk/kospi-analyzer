"""KOSPI 인덱스 응답 파서 회귀 테스트.

인덱스 API(`inquire-index-price`)는 개별 종목과 다른 등락률 키
(`bstp_nmix_prdy_ctrt`)를 사용한다. 과거 `prdy_ctrt`로 잘못 읽어
change_rate=0.0 silent fail이 발생했다.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.kis_api import KISClient  # noqa: E402


# 2026-04-28 라이브 응답에서 캡처한 실제 인덱스 키 패턴
LIVE_KOSPI_OUTPUT = {
    "bstp_nmix_prpr": "6641.02",
    "bstp_nmix_prdy_vrss": "25.99",
    "bstp_nmix_prdy_ctrt": "0.39",
    # 개별 종목 키는 인덱스 응답에 없음 (의도적으로 누락)
    # "prdy_ctrt": ...
}


@pytest.mark.asyncio
async def test_aget_kospi_index_uses_correct_change_rate_key():
    """`bstp_nmix_prdy_ctrt`에서 등락률을 읽어야 한다."""
    kis = KISClient()
    with patch.object(
        kis, "_request_get",
        new=AsyncMock(return_value={"output": LIVE_KOSPI_OUTPUT}),
    ):
        result = await kis.aget_kospi_index()

    assert result["index"] == 6641.02
    assert result["change"] == 25.99
    assert result["change_rate"] == 0.39, (
        f"change_rate가 0.39여야 함 — 잘못된 키({['prdy_ctrt']}) 사용 시 0.0"
    )


@pytest.mark.asyncio
async def test_aget_kospi_index_does_not_fall_back_to_prdy_ctrt():
    """레거시 `prdy_ctrt` 키만 있고 `bstp_nmix_prdy_ctrt`가 없으면 0.0.

    실제 인덱스 응답에는 `prdy_ctrt`가 없다는 걸 검증.
    """
    kis = KISClient()
    raw = {
        "bstp_nmix_prpr": "6641.02",
        "bstp_nmix_prdy_vrss": "25.99",
        "prdy_ctrt": "0.39",  # 부적절한 키
    }
    with patch.object(
        kis, "_request_get",
        new=AsyncMock(return_value={"output": raw}),
    ):
        result = await kis.aget_kospi_index()
    assert result["change_rate"] == 0.0


@pytest.mark.asyncio
async def test_aget_kospi_index_handles_negative_change():
    """하락장 응답도 정확."""
    kis = KISClient()
    raw = {
        "bstp_nmix_prpr": "6500.00",
        "bstp_nmix_prdy_vrss": "-50.00",
        "bstp_nmix_prdy_ctrt": "-0.76",
    }
    with patch.object(
        kis, "_request_get",
        new=AsyncMock(return_value={"output": raw}),
    ):
        result = await kis.aget_kospi_index()
    assert result["change"] == -50.0
    assert result["change_rate"] == -0.76


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

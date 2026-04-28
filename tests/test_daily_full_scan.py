"""매일 풀 스캔 정책 회귀 테스트.

`AnalysisPipeline._determine_target_codes`가 요일에 무관하게 항상 None을
반환하는지 확인한다 (= 코스피 전종목 스캔). 화~금 TOP 50 추적 분기가
재도입되지 않게 가드한다.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest


@pytest.fixture
def pipeline():
    """AnalysisPipeline 인스턴스. KIS/DB 외부 의존성은 사용 안 함."""
    from main import AnalysisPipeline
    p = AnalysisPipeline()
    yield p
    p.cleanup()


def test_determine_target_codes_returns_none_on_monday(pipeline):
    """월요일에도 None (full scan)."""
    fake = datetime(2026, 4, 27)  # Monday
    assert fake.weekday() == 0
    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value = fake
        result = pipeline._determine_target_codes()
    assert result is None


def test_determine_target_codes_returns_none_on_tuesday(pipeline):
    """화요일도 None (TOP 50 추적 모드 제거됨)."""
    fake = datetime(2026, 4, 28)  # Tuesday
    assert fake.weekday() == 1
    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value = fake
        result = pipeline._determine_target_codes()
    assert result is None


def test_determine_target_codes_returns_none_on_friday(pipeline):
    """금요일도 None."""
    fake = datetime(2026, 5, 1)  # Friday
    assert fake.weekday() == 4
    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value = fake
        result = pipeline._determine_target_codes()
    assert result is None


def test_determine_target_codes_ignores_top_codes(pipeline):
    """이전 TOP 50 데이터가 있어도 풀 스캔 (분기 안 함)."""
    with patch.object(
        pipeline, "_get_top_stock_codes", return_value=["005930", "000660"]
    ) as mock_top:
        result = pipeline._determine_target_codes()
    assert result is None
    # _get_top_stock_codes는 호출되지 않아야 함 (dead path 보장)
    mock_top.assert_not_called()


def test_determine_target_codes_ignores_portfolio(pipeline):
    """포트폴리오 종목이 있어도 풀 스캔 (분기 안 함)."""
    with patch.object(
        pipeline.db, "get_portfolio",
        return_value=[{"stock_code": "005930", "stock_name": "삼성전자"}],
    ) as mock_pf:
        result = pipeline._determine_target_codes()
    assert result is None
    mock_pf.assert_not_called()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

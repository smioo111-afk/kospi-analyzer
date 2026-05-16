"""지주사 할인 회귀 테스트.

검증 항목:
  - 종목명/섹터 패턴으로 지주사 식별
  - 적정주가 fair_value_low/high만 30% 할인
  - value_score는 변하지 않음 (PER/PBR 점수는 그대로)
  - 일반 종목은 할인 미적용
"""

import os
import sys

import pytest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from analysis.scorer import ScoringEngine
from config.settings import ScoringConfig


@pytest.fixture
def engine() -> ScoringEngine:
    return ScoringEngine()


def _price(stock_code: str, stock_name: str, current: int, per: float, pbr: float, mcap: int) -> dict:
    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "current_price": current,
        "per": per,
        "pbr": pbr,
        "market_cap": mcap,
        "volume": 1_000_000,
        "trading_value": 10_000_000_000,
    }


def _fin(sector: str = "기타", **kw) -> dict:
    base = {
        "sector": sector,
        "roe": 8.0,
        "operating_margin": 5.0,
        "debt_ratio": 100.0,
        "current_ratio": 150.0,
        "revenue_growth_yoy": 5.0,
        "op_income_growth_yoy": 5.0,
        "operating_income": 100_000_000_000,
        "prev_operating_income": 90_000_000_000,
        "net_income": 80_000_000_000,
        "prev_net_income": 70_000_000_000,
        "revenue": 1_000_000_000_000,
        "ebitda": 150_000_000_000,
        "total_liabilities": 500_000_000_000,
        "cash_equivalents": 100_000_000_000,
        "free_cash_flow": 50_000_000_000,
    }
    base.update(kw)
    return base


# ================================================================
# 식별 로직
# ================================================================
def test_holding_pattern_recognized_by_name(engine: ScoringEngine) -> None:
    """종목명에 '홀딩스'/'지주'/'Holdings' 포함 시 지주사."""
    assert engine._is_holding_company("AK홀딩스", "기타") is True
    assert engine._is_holding_company("롯데지주", "금융") is True
    assert engine._is_holding_company("Some Holdings", "기타") is True


def test_holding_pattern_recognized_by_sector(engine: ScoringEngine) -> None:
    """DART sector가 '지주회사'이면 지주사."""
    assert engine._is_holding_company("일반종목", "지주회사") is True


def test_normal_stock_not_holding(engine: ScoringEngine) -> None:
    """일반 종목은 지주사가 아니다."""
    assert engine._is_holding_company("삼성전자", "전기·전자") is False
    assert engine._is_holding_company("현대차", "운송장비·부품") is False


def test_holding_empty_inputs(engine: ScoringEngine) -> None:
    assert engine._is_holding_company("", "") is False
    assert engine._is_holding_company(None, None) is False  # type: ignore[arg-type]


# ================================================================
# 적정주가 30% 할인
# ================================================================
def test_holding_discount_applied_to_fair_value(engine: ScoringEngine) -> None:
    """지주사는 fair_value_low/high가 정확히 30% 깎인다."""
    # 동일 재무 조건으로 일반 종목 vs 지주사 비교
    price_normal = _price("000001", "테스트일반", 10_000, per=10.0, pbr=1.0, mcap=1_000_000_000_000)
    price_holding = _price("000002", "테스트홀딩스", 10_000, per=10.0, pbr=1.0, mcap=1_000_000_000_000)
    fin = _fin(sector="기타")

    fv_normal = engine._calc_fair_value(price_normal, fin)
    fv_holding = engine._calc_fair_value(price_holding, fin)

    assert fv_normal["is_holding"] is False
    assert fv_holding["is_holding"] is True
    assert fv_holding["holding_discount"] == ScoringConfig.HOLDING_DISCOUNT_RATE

    expected_low = int(fv_normal["low"] * (1.0 - ScoringConfig.HOLDING_DISCOUNT_RATE))
    expected_high = int(fv_normal["high"] * (1.0 - ScoringConfig.HOLDING_DISCOUNT_RATE))
    assert fv_holding["low"] == expected_low
    assert fv_holding["high"] == expected_high


def test_holding_discount_skipped_for_normal_stock(engine: ScoringEngine) -> None:
    """일반 종목은 적정주가 할인 없음."""
    price = _price("000001", "삼성전자", 60_000, per=12.0, pbr=1.2, mcap=400_000_000_000_000)
    fin = _fin(sector="전기·전자")
    fv = engine._calc_fair_value(price, fin)
    assert fv["is_holding"] is False
    assert fv["holding_discount"] == 0.0


def test_holding_value_score_unchanged(engine: ScoringEngine) -> None:
    """value_score는 할인되지 않는다 — PER/PBR 점수는 일반 종목과 동일."""
    price_normal = _price("000001", "테스트일반", 10_000, per=8.0, pbr=0.6, mcap=1_000_000_000_000)
    price_holding = _price("000002", "테스트홀딩스", 10_000, per=8.0, pbr=0.6, mcap=1_000_000_000_000)
    fin = _fin(sector="기타")

    val_normal = engine._calc_value_score(price_normal, fin)
    val_holding = engine._calc_value_score(price_holding, fin)
    assert val_normal["total"] == val_holding["total"], (
        "지주사라도 value_score는 변하면 안 된다 (PER/PBR이 실제로 낮은 사실)."
    )


def test_holding_full_score_dict_carries_flags(engine: ScoringEngine) -> None:
    """calculate_score 결과 dict에 is_holding/holding_discount가 노출된다."""
    price = _price("000002", "테스트홀딩스", 10_000, per=10.0, pbr=1.0, mcap=1_000_000_000_000)
    fin = _fin(sector="기타", stock_code="000002", year=2025)
    chart = [{"close": 10_000, "volume": 1_000_000} for _ in range(60)]
    out = engine.calculate_score(price, fin, chart)
    assert out["is_holding"] is True
    assert out["holding_discount"] == ScoringConfig.HOLDING_DISCOUNT_RATE

"""ScoringEngine 단위 테스트 — silent fail 차단 회귀 검증.

대상 함수:
  - _score_growth: rate==0/None을 결손으로 처리, 가산점 차단
  - _score_debt: ratio==0/None을 결손으로 처리, 만점 차단

실행: pytest tests/test_scorer.py -v
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


# ================================================================
# CRIT-2: _score_growth — 결손은 default(0), 진짜 성장만 가산점
# ================================================================
def test_score_growth_with_zero_returns_default(engine: ScoringEngine) -> None:
    """rate=0은 결손 신호이므로 default(0)을 반환해야 한다.
    이전 버그: thresholds [(20,7),...,(0.0,2),...] 첫 매칭으로 2점 부여.
    """
    cfg = ScoringConfig
    # revenue
    assert engine._score_growth(
        0.0, cfg.REVENUE_GROWTH_THRESHOLDS, cfg.REVENUE_GROWTH_DEFAULT_SCORE
    ) == cfg.REVENUE_GROWTH_DEFAULT_SCORE
    assert cfg.REVENUE_GROWTH_DEFAULT_SCORE == 0
    # operating income
    assert engine._score_growth(
        0.0, cfg.OP_INCOME_GROWTH_THRESHOLDS, cfg.OP_INCOME_GROWTH_DEFAULT_SCORE
    ) == cfg.OP_INCOME_GROWTH_DEFAULT_SCORE


def test_score_growth_with_none_returns_default(engine: ScoringEngine) -> None:
    cfg = ScoringConfig
    assert engine._score_growth(
        None, cfg.REVENUE_GROWTH_THRESHOLDS, cfg.REVENUE_GROWTH_DEFAULT_SCORE
    ) == 0


def test_score_growth_strong_positive_unchanged(engine: ScoringEngine) -> None:
    """진짜 양수 성장률은 회귀 없음."""
    cfg = ScoringConfig
    # 25% 성장 → 7점 (≥20.0)
    assert engine._score_growth(
        25.0, cfg.REVENUE_GROWTH_THRESHOLDS, cfg.REVENUE_GROWTH_DEFAULT_SCORE
    ) == 7
    # 12% → 5점
    assert engine._score_growth(
        12.0, cfg.REVENUE_GROWTH_THRESHOLDS, cfg.REVENUE_GROWTH_DEFAULT_SCORE
    ) == 5
    # 6% → 4점
    assert engine._score_growth(
        6.0, cfg.REVENUE_GROWTH_THRESHOLDS, cfg.REVENUE_GROWTH_DEFAULT_SCORE
    ) == 4


def test_score_growth_negative_unchanged(engine: ScoringEngine) -> None:
    """음수 성장률(역성장)은 회귀 없음."""
    cfg = ScoringConfig
    # -5% → 1점 (≥-10.0)
    assert engine._score_growth(
        -5.0, cfg.REVENUE_GROWTH_THRESHOLDS, cfg.REVENUE_GROWTH_DEFAULT_SCORE
    ) == 1
    # -25% → default(0)
    assert engine._score_growth(
        -25.0, cfg.REVENUE_GROWTH_THRESHOLDS, cfg.REVENUE_GROWTH_DEFAULT_SCORE
    ) == 0


def test_score_growth_small_positive_still_scored(engine: ScoringEngine) -> None:
    """0.01% 같은 미세 양수는 여전히 임계값 매칭 (0.0,2가 아니라 다음 임계)."""
    cfg = ScoringConfig
    # 0.01 → -10.0 임계 통과(>= -10), 1점
    assert engine._score_growth(
        0.01, cfg.REVENUE_GROWTH_THRESHOLDS, cfg.REVENUE_GROWTH_DEFAULT_SCORE
    ) == 2  # ≥0.0 → 2점


# ================================================================
# HIGH-1: _score_debt — 결손은 default(0), 만점 차단
# ================================================================
def test_score_debt_with_zero_returns_default(engine: ScoringEngine) -> None:
    """debt_ratio=0은 결손이므로 default(0). 이전 버그: MAX_SCORE=5 부여."""
    cfg = ScoringConfig
    assert engine._score_debt(0) == cfg.DEBT_RATIO_DEFAULT_SCORE == 0
    assert engine._score_debt(0.0) == 0


def test_score_debt_with_none_returns_default(engine: ScoringEngine) -> None:
    assert engine._score_debt(None) == 0


def test_score_debt_with_negative_returns_default(engine: ScoringEngine) -> None:
    """음수도 결손 신호."""
    assert engine._score_debt(-10.0) == 0


def test_score_debt_low_ratio_high_score(engine: ScoringEngine) -> None:
    """진짜 낮은 부채비율은 회귀 없이 높은 점수."""
    cfg = ScoringConfig
    # 30% → 첫 임계 (50.0,5) 통과 → 5점
    assert engine._score_debt(30.0) == 5
    # 80% → (100.0,3) → 3점
    assert engine._score_debt(80.0) == 3
    # 150% → (200.0,1) → 1점
    assert engine._score_debt(150.0) == 1


def test_score_debt_high_ratio_default(engine: ScoringEngine) -> None:
    """높은 부채비율(>200%)은 default(0)."""
    assert engine._score_debt(300.0) == 0


# ================================================================
# 통합: _calc_growth_score / _calc_financial_score 결손 종합
# ================================================================
def test_calc_growth_score_with_all_zero_inputs(engine: ScoringEngine) -> None:
    """결손 dict로 호출 시 매출/영업 성장 점수 0. 이전 버그: 2+2=4 가산."""
    fin = {
        "revenue_growth_yoy": 0.0,
        "op_income_growth_yoy": 0.0,
        "operating_income": 0,
        "prev_operating_income": 0,
        "net_income": 0,
        "prev_net_income": 0,
        "consecutive_op_decline_years": 0,
        "stock_code": "TEST",
        "year": 2025,
    }
    out = engine._calc_growth_score(fin)
    assert out["revenue_growth_score"] == 0
    assert out["op_income_growth_score"] == 0


def test_calc_financial_score_with_zero_debt(engine: ScoringEngine) -> None:
    """debt_ratio=0(결손)은 만점 아님."""
    fin = {
        "roe": 0.0,
        "operating_margin": 0.0,
        "debt_ratio": 0.0,  # ← 결손
        "current_ratio": 0.0,
    }
    out = engine._calc_financial_score(fin)
    # 모두 결손이므로 total = 0
    assert out["debt_ratio_score"] == 0
    assert out["total"] == 0


def test_calc_financial_score_real_low_debt_unchanged(engine: ScoringEngine) -> None:
    """진짜 낮은 부채비율은 회귀 없이 5점."""
    fin = {
        "roe": 15.0,  # 실제값
        "operating_margin": 12.0,
        "debt_ratio": 30.0,  # ← 진짜 30%
        "current_ratio": 200.0,
    }
    out = engine._calc_financial_score(fin)
    assert out["debt_ratio_score"] == 5


# ================================================================
# MED-5: PL 결손 식별 — 페널티 판정 불가 사유 표시
# ================================================================
def test_growth_score_pl_missing_emits_data_missing_reason(engine: ScoringEngine) -> None:
    """rev/op/net 모두 0(PL 결손)이면 penalty_reasons에 결손 표시."""
    fin = {
        "stock_code": "TEST", "year": 2025,
        "revenue": 0, "operating_income": 0, "net_income": 0,
        "prev_operating_income": 0, "prev_net_income": 0,
        "revenue_growth_yoy": 0.0, "op_income_growth_yoy": 0.0,
        "consecutive_revenue_decline_years": 5,  # ← 결손이지만 페널티 적용 안 됨
        "consecutive_loss_years": 5,
        "consecutive_op_decline_years": 0,
    }
    out = engine._calc_growth_score(fin)
    assert out["total_penalties"] == 0
    assert any("결손" in r for r in out["penalty_reasons"])
    # consecutive_*_years>=3 로직이 우회되어야 (결손 종목은 신뢰 못 함)
    assert all("3년 연속" not in r for r in out["penalty_reasons"])


def test_growth_score_real_loss_still_applies_penalty(engine: ScoringEngine) -> None:
    """진짜 적자(rev>0, net<0, consecutive_loss>=3)는 페널티 적용 — 회귀 방지."""
    fin = {
        "stock_code": "TEST", "year": 2025,
        "revenue": 1_000_000_000_000,  # ← 결손 아님
        "operating_income": -50_000_000_000,
        "net_income": -100_000_000_000,
        "prev_operating_income": 10_000_000_000,
        "prev_net_income": 20_000_000_000,
        "revenue_growth_yoy": -5.0,
        "op_income_growth_yoy": -200.0,
        "consecutive_revenue_decline_years": 0,
        "consecutive_loss_years": 3,  # ← 3년 적자 페널티 발동
        "consecutive_op_decline_years": 0,
    }
    out = engine._calc_growth_score(fin)
    # 3년 적자 페널티 + 흑자→적자 전환 페널티
    assert out["total_penalties"] != 0
    reasons = " | ".join(out["penalty_reasons"])
    assert "3년 연속 적자" in reasons


def test_growth_score_normal_profit_no_penalty(engine: ScoringEngine) -> None:
    """정상 흑자 종목은 페널티 없고 결손 사유도 없다 — 회귀 방지."""
    fin = {
        "stock_code": "TEST", "year": 2025,
        "revenue": 1_000_000_000_000,
        "operating_income": 100_000_000_000,
        "net_income": 80_000_000_000,
        "prev_operating_income": 90_000_000_000,
        "prev_net_income": 70_000_000_000,
        "revenue_growth_yoy": 10.0,
        "op_income_growth_yoy": 11.0,
        "consecutive_revenue_decline_years": 0,
        "consecutive_loss_years": 0,
        "consecutive_op_decline_years": 0,
    }
    out = engine._calc_growth_score(fin)
    assert out["total_penalties"] == 0
    assert out["penalty_reasons"] == []

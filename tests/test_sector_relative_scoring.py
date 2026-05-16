"""섹터 상대 PBR/ROE 점수 회귀 테스트.

검증 항목:
  - _score_pbr: ratio = pbr/sector_avg_pbr 기반 채점
  - _score_roe: ratio = roe/sector_avg_roe 기반 채점
  - 섹터 평균이 없는/0인 경우 절대 임계로 폴백
  - 음수/0 입력은 default(0)
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
# PBR 섹터 상대 채점
# ================================================================
def test_pbr_sector_relative_max_when_half_of_avg(engine: ScoringEngine) -> None:
    """PBR이 섹터 평균의 0.4배(<0.5)면 만점(3점)."""
    # 전기·전자 avg = 2.0 → 0.4배 = 0.8
    score = engine._score_pbr(0.8, "전기·전자")
    assert score == 3


def test_pbr_sector_relative_high_pbr_sector_still_good(engine: ScoringEngine) -> None:
    """제약 avg 3.0 → 절대 PBR 2.0이어도 ratio=0.67, 2점."""
    score = engine._score_pbr(2.0, "제약")
    assert score == 2  # ratio 0.67 < 0.8 → 2점


def test_pbr_sector_relative_low_pbr_sector_penalized(engine: ScoringEngine) -> None:
    """전기·가스 avg 0.4 → 절대 PBR 0.6은 ratio=1.5로 0점."""
    score = engine._score_pbr(0.6, "전기·가스")
    assert score == 0


def test_pbr_zero_returns_default(engine: ScoringEngine) -> None:
    """PBR ≤ 0이면 default(0)."""
    assert engine._score_pbr(0.0, "전기·전자") == 0
    assert engine._score_pbr(-1.0, "전기·전자") == 0


def test_pbr_unknown_sector_uses_default_sector_avg(engine: ScoringEngine) -> None:
    """알 수 없는 섹터는 DEFAULT_SECTOR_PBR(=1.0)로 채점 — 폴백 동작."""
    # ratio = 0.5/1.0 = 0.5 → 임계 미만이므로 2점
    score = engine._score_pbr(0.5, "존재하지않는섹터")
    assert score in (2, 3)  # 임계가 < 0.5/0.8 둘 다 매칭 가능


def test_pbr_at_avg_gets_zero(engine: ScoringEngine) -> None:
    """PBR이 정확히 섹터 평균과 같으면 ratio=1.0, default(0)."""
    avg = ScoringConfig.SECTOR_AVG_PBR["화학"]  # 0.8
    score = engine._score_pbr(avg, "화학")
    assert score == 0  # ratio == 1.0, < 1.0 매칭 X


# ================================================================
# ROE 섹터 상대 채점
# ================================================================
def test_roe_sector_relative_max_for_high_ratio(engine: ScoringEngine) -> None:
    """ROE가 섹터 평균의 1.5배 이상이면 만점(5점)."""
    # 전기·전자 avg = 5.5 → 1.5배 = 8.25
    score = engine._score_roe(9.0, "전기·전자")
    assert score == 5


def test_roe_sector_relative_low_roe_sector_lenient(engine: ScoringEngine) -> None:
    """저ROE 섹터(전기·가스 avg 1.2)에서 ROE=2%여도 ratio=1.67로 만점."""
    score = engine._score_roe(2.0, "전기·가스")
    assert score == 5


def test_roe_sector_relative_high_roe_sector_strict(engine: ScoringEngine) -> None:
    """고ROE 섹터(기계·장비 avg 18.7)에서 ROE=10%면 ratio=0.53, 1점."""
    score = engine._score_roe(10.0, "기계·장비")
    assert score == 1


def test_roe_zero_returns_default(engine: ScoringEngine) -> None:
    """ROE ≤ 0이면 default(0)."""
    assert engine._score_roe(0.0, "전기·전자") == 0
    assert engine._score_roe(-5.0, "전기·전자") == 0


def test_roe_unknown_sector_uses_default(engine: ScoringEngine) -> None:
    """알 수 없는 섹터는 DEFAULT_SECTOR_ROE(=8.0) 기준으로 채점."""
    # ratio = 12/8 = 1.5 → 만점
    score = engine._score_roe(12.0, "존재하지않는섹터")
    assert score == 5


# ================================================================
# 통합: _calc_financial_score
# ================================================================
def test_financial_score_uses_sector_roe(engine: ScoringEngine) -> None:
    """_calc_financial_score 경로에서 섹터 ROE가 사용되는지 확인."""
    fin_low_roe_sector = {
        "sector": "전기·가스",
        "roe": 2.0,  # ratio 1.67 → 만점 5
        "operating_margin": 0.0,
        "debt_ratio": 0.0,
        "current_ratio": 0.0,
    }
    out = engine._calc_financial_score(fin_low_roe_sector)
    assert out["roe_score"] == 5

    fin_high_roe_sector = {
        "sector": "기계·장비",
        "roe": 2.0,  # ratio 0.107 → 0점
        "operating_margin": 0.0,
        "debt_ratio": 0.0,
        "current_ratio": 0.0,
    }
    out = engine._calc_financial_score(fin_high_roe_sector)
    assert out["roe_score"] == 0

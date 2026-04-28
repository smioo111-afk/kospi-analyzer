"""적정주가 괴리율 계산 회귀 테스트.

`_calc_fair_value`의 gap_pct 계산은 다음 의미를 가진다:
  - 음수: current_price < fair_low → 저평가
  - 0: fair_low <= current_price <= fair_high → 적정 범위 내
  - 양수: current_price > fair_high → 고평가

과거에는 항상 fair_low 단일 기준이라, 적정 범위 안 종목도 큰 양수가
나오던 silent fail이 있었다 (4-28 효성 +84.9%, 현대글로비스 +98.7%).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.scorer import ScoringEngine  # noqa: E402


def _engine_with_sector_avg(per: float = 10.0, pbr: float = 1.0,
                             ev_eb: float = 8.0) -> ScoringEngine:
    """업종 평균을 고정값으로 주입한 엔진."""
    eng = ScoringEngine()
    eng.set_sector_averages({
        "기타": {"avg_per": per, "avg_pbr": pbr, "avg_ev_ebitda": ev_eb},
    })
    return eng


def _calc(eng: ScoringEngine, current_price: int, per: float, pbr: float,
          market_cap: int = 1_000_000_000_000):
    """_calc_fair_value 호출 헬퍼."""
    price = {
        "current_price": current_price, "per": per, "pbr": pbr,
        "market_cap": market_cap,
    }
    fin = {
        "sector": "기타", "op_income_growth_yoy": 0.0,
        "ebitda": 0, "total_liabilities": 0, "cash_equivalents": 0,
    }
    return eng._calc_fair_value(price, fin)


# ----------------------------------------------------------------------
# 적정 범위 내 → 0%
# ----------------------------------------------------------------------
def test_gap_pct_inside_range_returns_zero():
    """현재가가 [fair_low, fair_high] 내면 0% (적정)."""
    eng = _engine_with_sector_avg(per=10.0, pbr=1.0)
    # PER=10, current=10000 → EPS=1000 → fair_low ~= 7000, fair_high ~= 12000
    r = _calc(eng, current_price=10000, per=10.0, pbr=1.0)
    assert r["low"] > 0 and r["high"] > 0
    assert r["low"] <= 10000 <= r["high"], (
        f"현재가 10000이 적정 범위 [{r['low']}, {r['high']}] 내에 있어야 함"
    )
    assert r["gap_pct"] == 0.0


# ----------------------------------------------------------------------
# 범위 미만 → 음수 (저평가)
# ----------------------------------------------------------------------
def test_gap_pct_below_range_negative():
    """현재가 < fair_low → 음수."""
    eng = _engine_with_sector_avg(per=10.0, pbr=1.0)
    # PER=20일 때 EPS=current/20. avg_per=10 기준이면 fair_low = EPS×7
    # current=10000, per=20 → EPS=500 → fair_low ~= 3500
    # 현재가가 fair_low보다 낮은 케이스 만들기: per를 매우 작게 줘서 EPS 크게
    r = _calc(eng, current_price=5000, per=2.0, pbr=0.5)
    # EPS=2500, avg_per=10 → fair_low~17500
    assert r["low"] > 5000
    assert r["gap_pct"] < 0


# ----------------------------------------------------------------------
# 범위 초과 → 양수 (고평가)
# ----------------------------------------------------------------------
def test_gap_pct_above_range_positive():
    """현재가 > fair_high → 양수."""
    eng = _engine_with_sector_avg(per=10.0, pbr=1.0)
    # 현재가 매우 높게: per=50 → EPS=current/50, avg_per=10 → fair_high~12×EPS
    r = _calc(eng, current_price=100000, per=50.0, pbr=10.0)
    # EPS=2000, avg_per=10 → fair_high≈24000 < 100000
    assert r["high"] < 100000
    assert r["gap_pct"] > 0


# ----------------------------------------------------------------------
# 4-28 사용자 발견 사례 회귀 (효성 패턴)
# ----------------------------------------------------------------------
def test_gap_pct_no_false_overvalued_when_in_range():
    """효성 사례 패턴: 현재가가 적정 범위 안인데 +84.9% 표시되던 결함 회귀.

    과거 결함: gap = (current - fair_low) / fair_low * 100
    수정 후: 적정 범위 내면 0%
    """
    eng = _engine_with_sector_avg(per=10.0, pbr=1.0)
    # 적정 범위 안 케이스
    r = _calc(eng, current_price=10000, per=10.0, pbr=1.0)
    # 과거 코드라면 gap = (10000 - 7000) / 7000 * 100 ≈ +42.9% (고평가)
    # 새 코드: 적정 범위 안 → 0%
    assert r["low"] <= 10000 <= r["high"]
    assert r["gap_pct"] == 0.0, (
        f"적정 범위 안인데 gap={r['gap_pct']} (과거 결함 패턴)"
    )


# ----------------------------------------------------------------------
# fair_low <= 0 (계산 불가) → 0
# ----------------------------------------------------------------------
def test_gap_pct_zero_when_no_models():
    """모델 0개일 때 gap_pct=0.0."""
    eng = ScoringEngine()
    # PER=0, PBR=0 → 두 모델 다 스킵, EBITDA 0 → 모델3도 스킵
    r = _calc(eng, current_price=10000, per=0.0, pbr=0.0)
    assert r["gap_pct"] == 0.0
    assert r["method"] == "계산불가"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

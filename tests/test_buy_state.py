"""analysis/buy_state.py 단위 테스트."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.buy_state import (  # noqa: E402
    BuyState,
    calculate_buy_score,
    classify_buy_state,
    get_state_label,
    get_state_reason,
)


def _score(**kw) -> dict:
    """기본값: BUY 분류되는 정상 종목 (TOP 10 통과 가정)."""
    base = {
        "stock_code": "005930", "stock_name": "삼성전자",
        "current_price": 70000,
        "fair_value_low": 80000, "fair_value_high": 100000,
        "signal": "hold", "stoploss_price": 65000,
        "momentum_score": 12, "total_score": 60,
        "week52_position": 50,
    }
    base.update(kw)
    return base


# -------------------------------------------------------------------
# classify_buy_state
# -------------------------------------------------------------------
def test_classify_buy_normal_case():
    assert classify_buy_state(_score()) == BuyState.BUY


def test_classify_avoid_data_missing_logs_warning(caplog):
    with caplog.at_level("WARNING"):
        result = classify_buy_state(_score(current_price=0))
    assert result == BuyState.AVOID
    assert "데이터 부족" in caplog.text or "필수 데이터 부족" in caplog.text


def test_classify_avoid_data_missing_fair_high():
    assert classify_buy_state(_score(fair_value_high=0)) == BuyState.AVOID


def test_classify_avoid_sell_signal():
    assert classify_buy_state(_score(signal="sell")) == BuyState.AVOID


def test_classify_avoid_overvalued():
    # cp(110000) > fair_high(100000)
    assert classify_buy_state(_score(current_price=110000)) == BuyState.AVOID


def test_classify_avoid_close_stoploss_under_3pct():
    # cp=70000, sl=68500 → sl_dist = 2.14% < 3% 차단
    assert classify_buy_state(
        _score(current_price=70000, stoploss_price=68500)
    ) == BuyState.AVOID


def test_classify_buy_stoploss_just_above_3pct():
    # cp=70000, sl=67900 → sl_dist = 3.0% (≥3%) → BUY
    # 3% 경계 정확히는 차단 안 됨
    assert classify_buy_state(
        _score(current_price=70000, stoploss_price=67800)
    ) == BuyState.BUY


def test_classify_avoid_high_52week_over_85():
    assert classify_buy_state(_score(week52_position=90)) == BuyState.AVOID


def test_classify_avoid_value_trap_undervalued_weak_momentum():
    # cp=70000, fair_low=80000, fair_high=100000 → fair_mid=90000
    # cp <= fair_mid (저평가) + momentum=2 < 5 → 가치 함정
    assert classify_buy_state(
        _score(momentum_score=2)
    ) == BuyState.AVOID


def test_classify_buy_undervalued_with_strong_momentum():
    # 같은 저평가 상황에 momentum=12 → BUY
    assert classify_buy_state(
        _score(momentum_score=12)
    ) == BuyState.BUY


def test_classify_buy_top_10_passes_filter():
    """모든 위험 회피 + 적정 범위 안 → BUY."""
    assert classify_buy_state(_score(
        current_price=85000,  # fair_low~fair_high 사이
        momentum_score=8,
        week52_position=60,
    )) == BuyState.BUY


# -------------------------------------------------------------------
# calculate_buy_score
# -------------------------------------------------------------------
def test_calculate_buy_score_no_history_zero_change():
    """history 없으면 가속도/지속성 가산 0."""
    s = _score(momentum_score=20, total_score=60, week52_position=50,
               current_price=70000, stoploss_price=65000)
    score = calculate_buy_score(s, history=None)
    # mom=30, change=0, total=12, w52=7.5, cons=0, sl=(7.14/10)*5≈3.57
    # = 30 + 0 + 12 + 7.5 + 0 + 3.57 = 53.07
    assert score == pytest.approx(53.07, abs=0.05)


def test_calculate_buy_score_with_acceleration_clamped():
    """가속도가 ±5 범위 안이면 그대로 반영."""
    s = _score(momentum_score=15)
    history = [
        {"momentum_score": 12},
        {"momentum_score": 10},
        {"momentum_score": 11},
    ]
    # avg_3d = 11.0, change = 4 → in [-5, 5]
    bs = calculate_buy_score(s, history=history)
    bs_no_hist = calculate_buy_score(s, history=None)
    # history 있으면 가속도 + 지속성 차이만큼 더 높음
    assert bs > bs_no_hist


def test_calculate_buy_score_clamps_extreme_change():
    """가속도 +20 같은 극단값은 +5로 clamp."""
    s = _score(momentum_score=20)
    history = [
        {"momentum_score": 0},
        {"momentum_score": 0},
        {"momentum_score": 0},
    ]
    # change = 20 → clamp to 5
    bs = calculate_buy_score(s, history=history)
    s2 = _score(momentum_score=20)
    history2 = [
        {"momentum_score": 15},
        {"momentum_score": 15},
        {"momentum_score": 15},
    ]
    # change = 5 → 동일하게 5로 clamp
    bs2 = calculate_buy_score(s2, history=history2)
    assert bs == bs2


def test_calculate_buy_score_consistency_bonus():
    """오늘 momentum >= 직전 → +5."""
    s = _score(momentum_score=10)
    h_up = [{"momentum_score": 8}]
    h_down = [{"momentum_score": 12}]
    # h_up: cons=1 → +5, h_down: cons=0
    assert calculate_buy_score(s, h_up) - calculate_buy_score(s, h_down) == 5.0


def test_calculate_buy_score_normalized_scale_0_100():
    """현실적 입력에 대해 0~100 범위 안."""
    # 강한 매수 (max-ish): momentum=20, total=100, w52=0, sl 멀리
    s_max = _score(
        momentum_score=20, total_score=100, week52_position=0,
        current_price=70000, stoploss_price=63000,
    )
    h_strong = [
        {"momentum_score": 10},
        {"momentum_score": 10},
        {"momentum_score": 10},
    ]
    bs_max = calculate_buy_score(s_max, h_strong)
    assert 0 <= bs_max <= 100

    # 약한 매수: momentum=0, total=0
    s_min = _score(
        momentum_score=0, total_score=0, week52_position=100,
        current_price=70000, stoploss_price=10000,
    )
    bs_min = calculate_buy_score(s_min, history=None)
    assert 0 <= bs_min <= 100
    assert bs_max > bs_min


def test_calculate_buy_score_priority_order():
    """모멘텀 강한 종목이 약한 종목보다 buy_score 높음."""
    strong = _score(momentum_score=18, total_score=60, week52_position=30)
    weak = _score(momentum_score=5, total_score=60, week52_position=30)
    assert calculate_buy_score(strong) > calculate_buy_score(weak)


# -------------------------------------------------------------------
# 라벨 + 사유
# -------------------------------------------------------------------
def test_state_labels_match_emojis():
    assert get_state_label(BuyState.BUY) == "🟢 BUY"
    assert get_state_label(BuyState.WATCH) == "🟡 WATCH"
    assert get_state_label(BuyState.AVOID) == "🔴 AVOID"


def test_state_reason_avoid_provides_reason():
    s = _score(signal="sell")
    assert get_state_reason(BuyState.AVOID, s) == "매도 신호"

    s2 = _score(week52_position=90)
    assert get_state_reason(BuyState.AVOID, s2) == "52주 고점"

    s3 = _score(current_price=110000)
    assert get_state_reason(BuyState.AVOID, s3) == "고평가"

    s4 = _score(current_price=70000, stoploss_price=68500)
    assert get_state_reason(BuyState.AVOID, s4) == "손절 근접"

    s5 = _score(momentum_score=2)
    assert get_state_reason(BuyState.AVOID, s5) == "가치 함정"

    s6 = _score(current_price=0)
    assert get_state_reason(BuyState.AVOID, s6) == "데이터 부족"


def test_state_reason_buy_returns_empty():
    assert get_state_reason(BuyState.BUY, _score()) == ""
    assert get_state_reason(BuyState.WATCH, _score()) == ""

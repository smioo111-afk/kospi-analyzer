"""60MA 추세 필터 단위 테스트 (v3.1).

대상:
  - _calc_ma60: 60일 종가 평균 헬퍼
  - SignalGenerator.filter_stocks: 60MA 추세 필터
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.signals import SignalGenerator, _calc_ma60
from config.settings import SignalConfig


def _make_chart(closes: list[int]) -> list[dict]:
    """일봉 리스트 생성. 리스트 순서는 마지막이 가장 최신이라 가정."""
    return [{"close": c, "volume": 1000000} for c in closes]


# ================================================================
# _calc_ma60
# ================================================================
def test_calc_ma60_with_full_60_days() -> None:
    chart = _make_chart([50000] * 60)
    assert _calc_ma60(chart) == 50000.0


def test_calc_ma60_uses_last_60_when_longer() -> None:
    # 첫 10개는 무시되고 마지막 60개의 평균.
    chart = _make_chart([10000] * 10 + [50000] * 60)
    assert _calc_ma60(chart) == 50000.0


def test_calc_ma60_returns_zero_on_short_data() -> None:
    chart = _make_chart([50000] * 59)
    assert _calc_ma60(chart) == 0.0


def test_calc_ma60_returns_zero_on_empty() -> None:
    assert _calc_ma60([]) == 0.0
    assert _calc_ma60(None) == 0.0  # type: ignore[arg-type]


def test_calc_ma60_skips_zero_closes() -> None:
    # 결손 close가 한 개라도 있으면 표본 60 미만으로 0.0 반환.
    chart = _make_chart([0] + [50000] * 59)
    assert _calc_ma60(chart) == 0.0


# ================================================================
# filter_stocks: 60MA 필터
# ================================================================
def _base_stock(code: str, price: int) -> dict:
    """필터 통과 기본 조건 충족하는 stock dict."""
    return {
        "stock_code": code,
        "current_price": price,
        "market_cap": 1_000_000_000_000,   # 1조 (≥ MIN 5000억)
        "trading_value": 50_000_000_000,    # 500억 (≥ MIN 50억)
        "total_score": 70,
    }


def _base_fin(code: str) -> dict:
    return {"stock_code": code, "consecutive_loss_years": 0}


def test_filter_excludes_below_60ma_minus_buffer() -> None:
    """현재가가 60MA × 0.97보다 낮으면 제외."""
    gen = SignalGenerator()
    # 60MA = 50000, buffer 3% → 컷오프 = 48500.
    chart = _make_chart([50000] * 60)
    # 48000 < 48500 → 제외
    stock = _base_stock("A", 48000)
    out = gen.filter_stocks([stock], [_base_fin("A")], {"A": chart})
    assert out == []


def test_filter_includes_within_3pct_buffer() -> None:
    """현재가가 60MA × 0.97 이상이면 통과 (버퍼 안)."""
    gen = SignalGenerator()
    chart = _make_chart([50000] * 60)
    # 48500 = 50000 × 0.97 (경계). MA60_FILTER_BUFFER_PCT=3.0 가정.
    # 코드는 `current_price < ma60 * buffer` 로 strict less-than 이므로
    # 같은 값은 통과.
    cutoff = int(50000 * (1 - SignalConfig.MA60_FILTER_BUFFER_PCT / 100))
    stock = _base_stock("B", cutoff)
    out = gen.filter_stocks([stock], [_base_fin("B")], {"B": chart})
    assert len(out) == 1
    assert out[0]["stock_code"] == "B"


def test_filter_passes_when_no_chart_data() -> None:
    """차트 데이터 없으면 60MA 필터 생략 (보수적 통과)."""
    gen = SignalGenerator()
    stock = _base_stock("C", 10000)
    # chart_dict 인자 자체 미주입
    out = gen.filter_stocks([stock], [_base_fin("C")])
    assert len(out) == 1


def test_filter_passes_when_chart_too_short() -> None:
    """차트가 60일 미만이면 60MA 필터 생략."""
    gen = SignalGenerator()
    short = _make_chart([50000] * 30)
    stock = _base_stock("D", 10000)
    out = gen.filter_stocks([stock], [_base_fin("D")], {"D": short})
    assert len(out) == 1


def test_filter_passes_when_above_60ma() -> None:
    """현재가가 60일선 위면 당연히 통과."""
    gen = SignalGenerator()
    chart = _make_chart([50000] * 60)
    stock = _base_stock("E", 55000)
    out = gen.filter_stocks([stock], [_base_fin("E")], {"E": chart})
    assert len(out) == 1

"""Tests for MessageFormatter.format_portfolio_for_report (박스 형식 + 전일 대비)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.formatter import MessageFormatter  # noqa: E402


@pytest.fixture
def fmt() -> MessageFormatter:
    return MessageFormatter()


def _portfolio_entry(
    code: str = "005930",
    name: str = "삼성전자",
    avg_price: int = 70000,
    qty: int = 10,
    buy_count: int = 1,
) -> dict:
    return {
        "stock_code": code,
        "stock_name": name,
        "avg_buy_price": avg_price,
        "total_quantity": qty,
        "total_invested": avg_price * qty,
        "buy_count": buy_count,
        "lots": [
            {
                "buy_date": "2026-01-01",
                "buy_price": avg_price,
                "quantity": qty,
            }
        ],
    }


def test_format_portfolio_includes_box_format(fmt):
    portfolio = [_portfolio_entry(qty=10, avg_price=70000)]
    scores = {"005930": {"current_price": 75000, "signal_label": "⭐ 보유", "total_score": 50}}
    result = fmt.format_portfolio_for_report(portfolio, scores_map=scores)
    assert result is not None
    assert "┌ 삼성전자 (005930)" in result
    assert "└──────────────" in result
    assert "│ 매수: 70,000원 × 10주 = 700,000원" in result
    assert "│ 현재: 75,000원 × 10주 = 750,000원" in result
    assert "│ 손익: +50,000원" in result


def test_format_portfolio_shows_daily_change(fmt):
    portfolio = [_portfolio_entry(avg_price=70000, qty=10)]
    scores = {"005930": {"current_price": 75000}}
    previous_prices = {"005930": 74500}  # 어제: 74,500
    result = fmt.format_portfolio_for_report(
        portfolio, scores_map=scores, previous_prices=previous_prices,
    )
    assert "│ 전일대비: +500원" in result
    # +500/74500*100 = 0.67%
    assert "(+0.67%)" in result
    assert "📈" in result


def test_format_portfolio_shows_daily_change_negative(fmt):
    portfolio = [_portfolio_entry(avg_price=70000, qty=10)]
    scores = {"005930": {"current_price": 70000}}
    previous_prices = {"005930": 71000}  # 어제 71,000 -> 오늘 70,000 (-1000)
    result = fmt.format_portfolio_for_report(
        portfolio, scores_map=scores, previous_prices=previous_prices,
    )
    assert "│ 전일대비: -1,000원" in result
    assert "📉" in result


def test_format_portfolio_handles_no_previous_price(fmt):
    portfolio = [_portfolio_entry()]
    scores = {"005930": {"current_price": 75000}}
    result = fmt.format_portfolio_for_report(
        portfolio, scores_map=scores, previous_prices={"005930": 0},
    )
    # 전일 가격이 없으면 전일대비 라인 미출력
    assert "전일대비" not in result


def test_format_portfolio_total_summary(fmt):
    portfolio = [
        _portfolio_entry("005930", "삼성전자", 70000, 10),
        _portfolio_entry("000660", "SK하이닉스", 200000, 5),
    ]
    scores = {
        "005930": {"current_price": 75000},
        "000660": {"current_price": 210000},
    }
    previous_prices = {"005930": 74500, "000660": 205000}
    result = fmt.format_portfolio_for_report(
        portfolio, scores_map=scores, previous_prices=previous_prices,
    )
    # 합계 섹션
    assert "📊 합계" in result
    assert "투자: 1,700,000원" in result  # 700k + 1M
    assert "평가: 1,800,000원" in result  # 750k + 1.05M
    assert "손익: +100,000원" in result
    # 전일 평가: 745k + 1.025M = 1,770,000  → 변화 +30,000
    assert "전일대비: +30,000원" in result


def test_format_portfolio_warning_lines(fmt):
    portfolio = [_portfolio_entry(avg_price=70000, qty=10)]
    scores = {
        "005930": {
            "current_price": 60000,
            "signal": "sell",
            "signal_label": "🔴 매도",
            "total_score": 20,
        },
    }
    stoploss = {
        "005930": {"effective_stoploss": 60000, "effective_stoploss_pct": -7.0},
    }
    result = fmt.format_portfolio_for_report(
        portfolio, scores_map=scores, stoploss_map=stoploss,
    )
    assert "│ 손절: 60,000원 (-7.0%)" in result
    # current_price(60000) <= stoploss(60000)*1.02 → 손절라인 접근 경고
    assert "⚠️ 삼성전자 (005930): 손절라인 접근 주의" in result
    # signal=sell → 매도 신호 경고
    assert "🔴 삼성전자 (005930): 매도 신호 (20점)" in result


def test_format_portfolio_buy_count_multi(fmt):
    p = _portfolio_entry(avg_price=70000, qty=20, buy_count=3)
    result = fmt.format_portfolio_for_report([p], scores_map={"005930": {"current_price": 70000}})
    # 추가 매수 이력은 평단 형식으로 표시
    assert "│ 매수 3회 (평단: 70,000원)" in result
    # 단일 매수 라인은 없어야 함
    assert "│ 매수: 70,000원 × 20주" not in result


def test_format_portfolio_empty_returns_none(fmt):
    assert fmt.format_portfolio_for_report([]) is None


def test_format_portfolio_no_total_daily_change_when_zero(fmt):
    # 전일 가격 == 현재가 → 합계 변화 0 → 합계 전일대비 라인 미출력
    portfolio = [_portfolio_entry(avg_price=70000, qty=10)]
    scores = {"005930": {"current_price": 70000}}
    previous_prices = {"005930": 70000}
    result = fmt.format_portfolio_for_report(
        portfolio, scores_map=scores, previous_prices=previous_prices,
    )
    # 종목별 라인은 daily_change != 0 조건이라 미표시 OK
    # 합계도 변화 0 → "전일대비:" 합계 라인은 없어야 함
    assert "   전일대비:" not in result


# ---------------------------------------------------------------------
# format_portfolio (/portfolio 명령어 본문)
# ---------------------------------------------------------------------
def test_format_portfolio_command_includes_daily_change(fmt):
    portfolio = [_portfolio_entry(avg_price=70000, qty=10)]
    scores = {"005930": {"current_price": 75000, "signal_label": "⭐", "total_score": 50}}
    previous_prices = {"005930": 74500}
    result = fmt.format_portfolio(
        portfolio, scores_map=scores, previous_prices=previous_prices,
    )
    assert "│ 전일대비: +500원" in result
    assert "(+0.67%)" in result
    # 합계 전일대비도 포함
    assert "   전일대비: +5,000원" in result  # 74500*10 → 75000*10 = +5000


def test_format_portfolio_command_empty_message(fmt):
    msg = fmt.format_portfolio([])
    assert "보유 종목이 없습니다" in msg


def test_format_portfolio_command_no_previous_prices(fmt):
    portfolio = [_portfolio_entry()]
    scores = {"005930": {"current_price": 75000}}
    result = fmt.format_portfolio(portfolio, scores_map=scores)
    # previous_prices 없으면 전일대비 라인 미출력
    assert "전일대비" not in result

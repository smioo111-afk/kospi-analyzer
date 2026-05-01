"""포맷터: TOP10 표시 + 매수 추천 섹션 테스트."""

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


def _stock(**kw) -> dict:
    base = {
        "stock_code": "005930", "stock_name": "삼성전자",
        "total_score": 60, "signal_label": "⭐ 보유",
        "current_price": 70000, "per": 12.0, "pbr": 1.1, "roe": 8.5,
        "fair_value_low": 80000, "fair_value_high": 100000,
        "fair_value_gap": -10.0,
        "momentum_score": 12, "week52_position": 45,
    }
    base.update(kw)
    return base


# -------------------------------------------------------------------
# _format_stock_entry — 매수상태 라인은 TOP10에서 미출력 (추천 섹션 전용)
# -------------------------------------------------------------------
def test_format_top_omits_buy_state_line_even_when_buy(fmt):
    s = _stock(buy_state="buy", buy_state_label="🟢 BUY", buy_score=65.05)
    out = fmt._format_stock_entry("1️⃣", s, {}, current_rank=1, prev_map={})
    assert "매수상태:" not in out


def test_format_top_omits_buy_state_line_even_when_avoid(fmt):
    s = _stock(
        buy_state="avoid", buy_state_label="🔴 AVOID",
        buy_score=20.0, buy_state_reason="52주 고점",
    )
    out = fmt._format_stock_entry("1️⃣", s, {}, current_rank=1, prev_map={})
    assert "매수상태:" not in out


def test_format_top_no_buy_state_field_skipped(fmt):
    """buy_state 필드 없어도 매수상태 라인 미출력."""
    s = _stock()
    out = fmt._format_stock_entry("1️⃣", s, {}, current_rank=1, prev_map={})
    assert "매수상태:" not in out


# -------------------------------------------------------------------
# format_buy_recommendations
# -------------------------------------------------------------------
def test_format_buy_recommendations_lists_only_buy(fmt):
    top = [
        _stock(stock_code="A", buy_state="buy", buy_score=50.0),
        _stock(stock_code="B", buy_state="avoid",
               buy_state_reason="고평가", buy_score=20.0),
        _stock(stock_code="C", buy_state="buy", buy_score=40.0),
    ]
    out = fmt.format_buy_recommendations(top)
    assert "🎯 매수 가능 종목" in out
    # BUY만 매수 가능 섹션에 들어감
    assert "1. " in out  # 첫 번째
    assert "2. " in out  # 두 번째
    # AVOID 섹션 별도
    assert "🔴 매수 회피" in out
    assert "고평가" in out


def test_format_buy_recommendations_sorts_by_score(fmt):
    top = [
        _stock(stock_code="A", stock_name="에이", buy_state="buy", buy_score=30.0),
        _stock(stock_code="B", stock_name="비", buy_state="buy", buy_score=70.0),
        _stock(stock_code="C", stock_name="씨", buy_state="buy", buy_score=50.0),
    ]
    out = fmt.format_buy_recommendations(top)
    # 정렬: B(70) > C(50) > A(30)
    pos_b = out.find("비")
    pos_c = out.find("씨")
    pos_a = out.find("에이")
    assert pos_b < pos_c < pos_a


def test_format_buy_recommendations_empty_state(fmt):
    """BUY 0개면 안내 메시지."""
    top = [
        _stock(stock_code="A", buy_state="avoid", buy_state_reason="매도 신호"),
        _stock(stock_code="B", buy_state="avoid", buy_state_reason="고평가"),
    ]
    out = fmt.format_buy_recommendations(top)
    assert "오늘 매수 가능 종목 없음" in out
    # AVOID 섹션은 정상 출력
    assert "🔴 매수 회피" in out


def test_format_avoid_section_with_reasons(fmt):
    top = [
        _stock(stock_code="A", stock_name="에이", buy_state="avoid",
               buy_state_reason="매도 신호"),
        _stock(stock_code="B", stock_name="비", buy_state="avoid",
               buy_state_reason="가치 함정"),
        _stock(stock_code="C", stock_name="씨", buy_state="avoid",
               buy_state_reason="52주 고점"),
    ]
    out = fmt.format_buy_recommendations(top)
    assert "에이 (A): 매도 신호" in out
    assert "비 (B): 가치 함정" in out
    assert "씨 (C): 52주 고점" in out


def test_daily_report_includes_buy_recommendations(fmt):
    """format_daily_report 통합 — top_10에 buy_state 있으면 섹션 포함."""
    top_10 = [
        _stock(stock_code="A", stock_name="에이", buy_state="buy",
               buy_state_label="🟢 BUY", buy_score=60.0),
        _stock(stock_code="B", stock_name="비", buy_state="avoid",
               buy_state_label="🔴 AVOID", buy_score=20.0,
               buy_state_reason="고평가"),
    ]
    msgs = fmt.format_daily_report(
        top_10=top_10, warnings=[],
        stats={"total_analyzed": 245, "after_filter": 200},
    )
    full = "\n".join(msgs)
    assert "🎯 매수 가능 종목" in full
    assert "🔴 매수 회피" in full
    assert "고평가" in full


def test_daily_report_skips_buy_section_when_no_buy_state(fmt):
    """기존 호환: top_10에 buy_state 필드 없으면 섹션 미출력."""
    top_10 = [_stock(stock_code="A", stock_name="에이")]
    msgs = fmt.format_daily_report(
        top_10=top_10, warnings=[],
        stats={"total_analyzed": 245, "after_filter": 200},
    )
    full = "\n".join(msgs)
    assert "🎯 매수 가능 종목" not in full

"""MessageFormatter 단위 테스트 — /stock v3 5카테고리 출력 회귀 방지.

대상:
  - format_stock_detail: 정상 v3 dict → 5 카테고리 모두 표시
  - 결손 필드 → "—" 표시
  - v1 표현(가치투자/재무건전성 헤더)이 더 이상 나오지 않음

실행: pytest tests/test_formatter.py -v
"""

import os
import sys

import pytest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from bot.formatter import MessageFormatter


@pytest.fixture
def fmt() -> MessageFormatter:
    return MessageFormatter()


def _v3_dict(**overrides) -> dict:
    """analysis_results.top_10_json 형태의 v3 풀 dict (TOP 10 종목)."""
    base = {
        "stock_code": "005930", "stock_name": "삼성전자",
        "total_score": 55, "signal_label": "⭐ 보유", "reason": "종합 55점",
        "value_score": 6, "financial_score": 18, "growth_score": 15,
        "momentum_score": 8, "quality_score": 8,
        "per": 33.44, "pbr": 3.43, "roe": 10.36,
        "operating_margin": 13.07, "debt_ratio": 29.94,
        "dividend_yield": 1.50,
        "peg": 3.06, "ev_ebitda": 12.5, "psr": 2.1, "fcf_yield": 5.2,
        "revenue_growth": 10.88, "op_income_growth": 33.23,
        "current_price": 219500, "market_cap": 1_283_300_000_000_000,
        "fair_value_low": 199539, "fair_value_high": 403330,
        "fair_value_gap": 10.0,
        "foreign_net_buy_5d": 3, "foreign_net_buy_20d": 12,
        "institutional_net_buy_5d": -2, "institutional_net_buy_20d": 5,
        "week52_position": 67.3,
        "stoploss_price": 204135, "stoploss_pct": -7.0,
    }
    base.update(overrides)
    return base


def _v1_only_dict(**overrides) -> dict:
    """stock_scores 테이블 컬럼만 있는 v1 dict (TOP 10 미진입 종목)."""
    base = {
        "stock_code": "023530", "stock_name": "롯데쇼핑",
        "total_score": 24, "signal_label": "🔴 매도",
        "reason": "종합점수 24점 < 45점",
        "value_score": 6, "financial_score": 1, "momentum_score": 10,
        "per": 66.03, "pbr": 0.22,
        "roe": 0.44, "operating_margin": 3.98, "debt_ratio": 124.77,
        "dividend_yield": 4.63,
        "current_price": 120300, "market_cap": 3_403_137_226_500,
        "stoploss_price": 111878, "stoploss_pct": -7.0,
    }
    base.update(overrides)
    return base


# ================================================================
# 5 카테고리 헤더
# ================================================================
def test_stock_detail_v3_has_five_category_headers(fmt: MessageFormatter) -> None:
    msg = fmt.format_stock_detail(_v3_dict())
    assert "── 가치 (30) ──" in msg
    assert "── 재무 (20) ──" in msg
    assert "── 성장 (20) ──" in msg
    assert "── 모멘텀 (20) ──" in msg
    assert "── 퀄리티 (10) ──" in msg


def test_stock_detail_excludes_v1_headers(fmt: MessageFormatter) -> None:
    """v1 표현이 더 이상 나오지 않아야 한다."""
    msg = fmt.format_stock_detail(_v3_dict())
    assert "가치투자" not in msg
    assert "재무건전성" not in msg
    # v3에서 '모멘텀' 단독 라벨은 사용 가능하지만 '/40', '/35', '/25' 같은
    # v1 배점이 나오면 안 된다.
    assert "/40" not in msg
    assert "/35" not in msg
    assert "/25" not in msg


# ================================================================
# 정상 v3 dict → 풀 출력
# ================================================================
def test_stock_detail_v3_renders_all_fields(fmt: MessageFormatter) -> None:
    msg = fmt.format_stock_detail(_v3_dict())
    # 핵심 지표
    assert "PER: 33.44" in msg
    assert "PBR: 3.43" in msg
    assert "ROE: 10.36%" in msg
    assert "영업이익률: 13.07%" in msg
    assert "부채비율: 29.94%" in msg
    # 성장
    assert "매출 성장: 10.88%" in msg
    assert "영업이익 성장: 33.23%" in msg
    # 적정주가
    assert "199,539~403,330원" in msg
    assert "+10.0% 고평가" in msg
    # 손절
    assert "손절라인: 204,135원" in msg
    # 수급 (5d/20d)
    assert "수급(외/기 5d):" in msg
    assert "수급(외/기 20d):" in msg
    # 52주
    assert "52주 위치: 67.3%" in msg
    # 점수 배점
    assert "점수: 6/30" in msg
    assert "점수: 18/20" in msg
    assert "점수: 15/20" in msg
    assert "점수: 8/20" in msg
    assert "점수: 8/10" in msg


def test_stock_detail_undervalued_label(fmt: MessageFormatter) -> None:
    msg = fmt.format_stock_detail(
        _v3_dict(fair_value_gap=-15.4, fair_value_low=100000, fair_value_high=200000)
    )
    assert "(-15.4% 저평가)" in msg


# ================================================================
# 결손 필드 → "—" 또는 안내문
# ================================================================
def test_stock_detail_v1_only_falls_back_gracefully(fmt: MessageFormatter) -> None:
    """stock_scores만 있는 종목(TOP 10 미진입)은 v3 신규 필드를 '—' 또는 안내."""
    msg = fmt.format_stock_detail(_v1_only_dict())
    # v1 컬럼은 표시
    assert "PER: 66.03" in msg
    assert "ROE: 0.44%" in msg
    assert "부채비율: 124.77%" in msg
    # v3 신규 필드 결손
    assert "데이터 없음 (TOP 10 진입 이력 없음)" in msg  # 성장
    # 적정주가 행은 fair_value_low 없으면 표시 안 함
    assert "적정주가:" not in msg
    # 5 카테고리 헤더는 모두 있어야 (점수 0이라도)
    assert "── 가치 (30) ──" in msg
    assert "── 성장 (20) ──" in msg
    assert "── 퀄리티 (10) ──" in msg


def test_stock_detail_zero_values_show_dash(fmt: MessageFormatter) -> None:
    """ROE=0(결손)은 0%가 아니라 '—'."""
    msg = fmt.format_stock_detail(
        _v3_dict(roe=0, operating_margin=0, dividend_yield=0)
    )
    # ROE: — 가 들어가야 (0.00% 아님)
    assert "ROE: —" in msg
    assert "영업이익률: —" in msg
    assert "ROE: 0%" not in msg
    assert "ROE: 0.00%" not in msg


# ================================================================
# 손절·이력
# ================================================================
def test_stock_detail_history_section(fmt: MessageFormatter) -> None:
    history = [
        {"analysis_date": "2026-04-24", "total_score": 55, "signal_label": "⭐ 보유"},
        {"analysis_date": "2026-04-23", "total_score": 54, "signal_label": "⭐ 보유"},
    ]
    msg = fmt.format_stock_detail(_v3_dict(), history=history)
    assert "── 최근 이력 ──" in msg
    assert "2026-04-24: 55점" in msg
    assert "2026-04-23: 54점" in msg


def test_stock_detail_signal_reason_shown(fmt: MessageFormatter) -> None:
    msg = fmt.format_stock_detail(_v3_dict(reason="가치 점수 낮음"))
    assert "사유: 가치 점수 낮음" in msg


# ================================================================
# format_daily_report (v3 회귀 방지) — 핵심 섹션만
# ================================================================
def test_daily_report_includes_undervaluation_section(fmt: MessageFormatter) -> None:
    """어제 발송 형식(저평가 괴리율 TOP) 회귀 방지."""
    top_10 = [_v3_dict(stock_code=f"00593{i}", total_score=80 - i * 2) for i in range(10)]
    scored_list = [
        _v3_dict(stock_code="888888", stock_name="저평가A",
                 fair_value_gap=-30.0, foreign_net_buy_20d=5,
                 roe=8.0, consecutive_op_decline_years=0,
                 fair_value_low=10000, fair_value_high=20000),
    ]
    msgs = fmt.format_daily_report(
        top_10=top_10, warnings=[], stats={},
        scored_list=scored_list, kospi_index=2500.0,
    )
    full = "\n".join(msgs)
    assert "저평가 괴리율 TOP" in full
    assert "저평가A" in full or "888888" in full

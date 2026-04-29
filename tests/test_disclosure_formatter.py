"""A1 Phase 3: bot.formatter 공시 영향 섹션 회귀 테스트."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.disclosure_impact import (  # noqa: E402
    DisclosureImpact,
    ScoreSnapshot,
)
from bot.formatter import (  # noqa: E402
    MessageFormatter,
    format_disclosure_section,
)
from collectors.dart_disclosure import Disclosure  # noqa: E402


# ----------------------------------------------------------------------
# 빌더
# ----------------------------------------------------------------------
def _disc(stock_code="004800", corp_name="효성",
          report_nm="[기재정정]사업보고서") -> Disclosure:
    return Disclosure(
        rcept_no="20260429001",
        corp_code="00111111",
        stock_code=stock_code,
        corp_name=corp_name,
        report_nm=report_nm,
        rcept_dt="20260429",
        rm="",
    )


def _snap(code="004800", total=50, signal="hold", **kw) -> ScoreSnapshot:
    base = dict(
        stock_code=code, stock_name="TEST",
        total_score=total, value_score=15, financial_score=10,
        growth_score=8, momentum_score=10, quality_score=7,
        signal=signal,
    )
    base.update(kw)
    return ScoreSnapshot(**base)


def _impact(*, code="004800", before_total=50, after_total=55,
            before_signal="hold", after_signal="hold",
            corp_name="효성", report_nm="[기재정정]사업보고서",
            **diffs) -> DisclosureImpact:
    before = _snap(code=code, total=before_total, signal=before_signal)
    after = _snap(code=code, total=after_total, signal=after_signal)
    base = dict(
        disclosure=_disc(code, corp_name, report_nm),
        stock_code=code,
        before=before,
        after=after,
        total_diff=after_total - before_total,
        value_diff=0, financial_diff=0,
        growth_diff=0, momentum_diff=0, quality_diff=0,
        signal_changed=(before_signal != after_signal),
    )
    base.update(diffs)
    return DisclosureImpact(**base)


# ----------------------------------------------------------------------
# format_disclosure_section
# ----------------------------------------------------------------------
def test_format_empty_returns_empty_string():
    assert format_disclosure_section([]) == ""


def test_format_single_significant_impact_includes_essentials():
    imp = _impact(before_total=55, after_total=62)  # +7
    out = format_disclosure_section([imp])
    assert "📋 공시 영향 변화" in out
    assert "효성 (004800)" in out
    assert "[기재정정]사업보고서" in out
    assert "55 → 62" in out
    assert "+7" in out


def test_format_signal_change_displayed():
    imp = _impact(before_total=50, after_total=51,
                  before_signal="hold", after_signal="buy")
    # |diff|=1 < 5 이지만 signal 변경 → significant
    out = format_disclosure_section([imp])
    assert "신호:" in out
    assert "보유" in out
    assert "매수" in out


def test_format_negative_diff_uses_warning_icon():
    imp = _impact(before_total=60, after_total=52)  # -8
    out = format_disclosure_section([imp])
    assert "⚠️" in out
    assert "-8" in out


def test_format_positive_diff_uses_rocket_icon():
    imp = _impact(before_total=50, after_total=60)  # +10
    out = format_disclosure_section([imp])
    assert "🚀" in out
    assert "+10" in out


def test_format_category_changes_shown_when_3_or_more():
    imp = _impact(before_total=50, after_total=58, value_diff=4,
                  financial_diff=2, growth_diff=3)  # +8 total
    out = format_disclosure_section([imp])
    # value_diff=4, growth_diff=3 → 표시 / financial_diff=2 → 미표시
    assert "가치 +4" in out
    assert "성장 +3" in out
    assert "재무" not in out


def test_format_minor_changes_grouped_with_count():
    """significant 아닌 점수 변동은 minor 그룹으로 한 줄씩."""
    imps = [
        _impact(code="004800", before_total=50, after_total=52),  # +2 minor
        _impact(code="005930", before_total=60, after_total=63,
                corp_name="삼성전자"),  # +3 minor
    ]
    out = format_disclosure_section(imps)
    assert "📊 작은 변화 (2건)" in out
    assert "004800 효성" in out
    assert "005930 삼성전자" in out


def test_format_info_only_grouped_by_category():
    """점수 영향 0인 정보성 공시는 자사주/배당/M&A/기타 그룹으로."""
    imps = [
        _impact(code="005930", before_total=70, after_total=70,
                corp_name="삼성전자",
                report_nm="주요사항보고서(자기주식취득결정)"),
        _impact(code="005380", before_total=65, after_total=65,
                corp_name="현대차",
                report_nm="현금ㆍ현물배당결정"),
        _impact(code="000270", before_total=63, after_total=63,
                corp_name="기아",
                report_nm="현금ㆍ현물배당결정"),
        _impact(code="028260", before_total=55, after_total=55,
                corp_name="삼성물산",
                report_nm="주요사항보고서(회사합병결정)"),
    ]
    out = format_disclosure_section(imps)
    assert "📌 자사주 (1건)" in out
    assert "📌 배당 (2건)" in out
    assert "📌 M&A (1건)" in out
    assert "삼성전자" in out
    assert "현대차" in out


def test_format_truncates_minor_at_5_items():
    """모두 |diff|<5 (minor) 8건 → 5개만 표시 + 외 3건."""
    imps = [
        # diff 1, 2, 3, 4, 1, 2, 3, 4 — 모두 5 미만 (minor)
        _impact(code=f"00000{i}", before_total=50,
                after_total=50 + (1 + i % 4),
                corp_name=f"종목{i}")
        for i in range(8)
    ]
    out = format_disclosure_section(imps)
    assert "📊 작은 변화 (8건)" in out
    assert "외 3건" in out  # 8 - 5 = 3


def test_format_truncates_info_at_5_per_group():
    imps = [
        _impact(code=f"00000{i}", before_total=50, after_total=50,
                corp_name=f"종목{i}", report_nm="현금ㆍ현물배당결정")
        for i in range(8)
    ]
    out = format_disclosure_section(imps)
    assert "📌 배당 (8건)" in out
    assert "외 3건" in out


def test_format_orders_significant_signal_change_first():
    """signal 변경 종목이 |diff| 큰 종목보다 우선."""
    a = _impact(code="111111", before_total=50, after_total=70,
                corp_name="크게변경")  # +20, no signal change → significant
    b = _impact(code="222222", before_total=50, after_total=53,
                before_signal="hold", after_signal="buy",
                corp_name="신호변경")  # +3, signal change → significant
    out = format_disclosure_section([a, b])
    # signal 변경 종목이 먼저 등장
    pos_signal = out.find("신호변경")
    pos_big = out.find("크게변경")
    assert pos_signal != -1 and pos_big != -1
    assert pos_signal < pos_big


def test_format_combines_all_three_groups():
    """significant + minor + info-only 모두 있을 때 헤더 + 3 그룹."""
    imps = [
        _impact(code="111", before_total=50, after_total=58,
                corp_name="유의미"),  # +8 significant
        _impact(code="222", before_total=60, after_total=62,
                corp_name="작은변화"),  # +2 minor
        _impact(code="333", before_total=70, after_total=70,
                corp_name="배당정보",
                report_nm="현금ㆍ현물배당결정"),  # info
    ]
    out = format_disclosure_section(imps)
    # 헤더
    assert "📋 공시 영향 변화" in out
    # significant
    assert "유의미" in out
    assert "🚀" in out
    # minor
    assert "📊 작은 변화 (1건)" in out
    # info
    assert "📌 배당 (1건)" in out


# ----------------------------------------------------------------------
# format_daily_report 통합
# ----------------------------------------------------------------------
def _minimal_top_10() -> list[dict]:
    return [
        {"stock_code": "005930", "stock_name": "삼성전자",
         "total_score": 80, "signal": "strong_buy",
         "signal_label": "STRONG_BUY", "current_price": 70000,
         "per": 12.0, "pbr": 1.1, "roe": 8.5},
    ]


def test_daily_report_omits_section_when_no_impacts():
    fmt = MessageFormatter()
    msgs = fmt.format_daily_report(
        top_10=_minimal_top_10(), warnings=[],
        stats={"total_analyzed": 245, "after_filter": 200},
        kospi_index=6500.0, kospi_change=0.5,
        disclosure_impacts=None,
    )
    full = "\n".join(msgs)
    assert "📋 공시 영향 변화" not in full


def test_daily_report_omits_section_when_empty_list():
    fmt = MessageFormatter()
    msgs = fmt.format_daily_report(
        top_10=_minimal_top_10(), warnings=[],
        stats={"total_analyzed": 245, "after_filter": 200},
        disclosure_impacts=[],
    )
    full = "\n".join(msgs)
    assert "📋 공시 영향 변화" not in full


def test_daily_report_includes_disclosures_when_present():
    fmt = MessageFormatter()
    imps = [_impact(before_total=55, after_total=62)]
    msgs = fmt.format_daily_report(
        top_10=_minimal_top_10(), warnings=[],
        stats={"total_analyzed": 245, "after_filter": 200},
        disclosure_impacts=imps,
    )
    full = "\n".join(msgs)
    assert "📋 공시 영향 변화" in full
    assert "효성" in full
    assert "+7" in full


def test_daily_report_section_position_before_warnings():
    """공시 섹션은 모멘텀 다음, 경고 종목 이전에 위치."""
    fmt = MessageFormatter()
    imps = [_impact(before_total=55, after_total=62, corp_name="공시종목")]
    msgs = fmt.format_daily_report(
        top_10=_minimal_top_10(),
        warnings=[{"stock_code": "999999", "stock_name": "경고종목"}],
        stats={"total_analyzed": 100, "after_filter": 80},
        stoploss_map={"999999": {
            "effective_stoploss_pct": -10, "warnings": ["변동성"],
        }},
        disclosure_impacts=imps,
    )
    full = "\n".join(msgs)
    pos_disc = full.find("공시 영향 변화")
    pos_warn = full.find("경고 종목")
    assert pos_disc != -1
    assert pos_warn != -1
    assert pos_disc < pos_warn


# ----------------------------------------------------------------------
# 길이 검증
# ----------------------------------------------------------------------
def test_long_report_with_30_disclosures_within_message_limit():
    """30건 공시가 들어가도 텔레그램 4096자 한도 내에서 분할 발송."""
    fmt = MessageFormatter()
    # 30건 mixed: 5 significant, 10 minor, 15 info-only
    imps = []
    for i in range(5):
        imps.append(_impact(
            code=f"{i:06d}",
            before_total=50, after_total=58 + i,
            corp_name=f"S{i}",
        ))
    for i in range(10):
        imps.append(_impact(
            code=f"1{i:05d}",
            before_total=50, after_total=52,
            corp_name=f"M{i}",
        ))
    for i in range(15):
        imps.append(_impact(
            code=f"2{i:05d}",
            before_total=50, after_total=50,
            corp_name=f"I{i}",
            report_nm="현금ㆍ현물배당결정",
        ))
    msgs = fmt.format_daily_report(
        top_10=_minimal_top_10(), warnings=[],
        stats={"total_analyzed": 245, "after_filter": 200},
        disclosure_impacts=imps,
    )
    # 각 메시지가 4096자 이하
    assert all(len(m) <= 4096 for m in msgs), (
        [len(m) for m in msgs]
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

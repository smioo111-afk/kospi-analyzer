"""A1 Phase 1: collectors/dart_disclosure 회귀 테스트."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.dart_disclosure import (  # noqa: E402
    Disclosure,
    DisclosureType,
    classify_disclosure,
    fetch_disclosures,
    needs_data_refresh,
)


# ----------------------------------------------------------------------
# Disclosure dataclass + DisclosureType enum
# ----------------------------------------------------------------------
def _disc(**kw) -> Disclosure:
    """기본값으로 채운 Disclosure 빌더 — 테스트 가독성용."""
    base = dict(
        rcept_no="20260429001234",
        corp_code="00126380",
        stock_code="005930",
        corp_name="삼성전자",
        report_nm="사업보고서",
        rcept_dt="20260429",
        rm="",
    )
    base.update(kw)
    return Disclosure(**base)


def test_disclosure_dataclass_creation():
    d = _disc()
    assert d.rcept_no == "20260429001234"
    assert d.corp_code == "00126380"
    assert d.stock_code == "005930"
    assert d.corp_name == "삼성전자"
    assert d.report_nm == "사업보고서"
    assert d.rcept_dt == "20260429"
    assert d.rm == ""


def test_disclosure_type_enum_values():
    # 후속 코드가 string value에 의존하므로 회귀 차단.
    assert DisclosureType.PERIODIC.value == "periodic"
    assert DisclosureType.MAJOR.value == "major"
    assert DisclosureType.AMENDMENT.value == "amendment"
    assert DisclosureType.DIVIDEND.value == "dividend"
    assert DisclosureType.BUYBACK.value == "buyback"
    assert DisclosureType.MA.value == "ma"
    assert DisclosureType.OTHER.value == "other"


def test_is_amendment_detects_correction_in_name():
    assert _disc(report_nm="[기재정정]사업보고서").is_amendment
    assert _disc(report_nm="[정정]주요사항보고서").is_amendment
    assert _disc(report_nm="[첨부정정]분기보고서").is_amendment


def test_is_amendment_detects_rm_correction_code():
    """report_nm이 깨끗해도 rm에 '정'이 있으면 정정공시."""
    assert _disc(report_nm="사업보고서", rm="정").is_amendment


def test_is_amendment_returns_false_for_normal():
    assert not _disc(report_nm="사업보고서").is_amendment
    assert not _disc(report_nm="분기보고서(1Q26)").is_amendment
    assert not _disc(report_nm="주요사항보고서(자기주식취득결정)").is_amendment


# ----------------------------------------------------------------------
# classify_disclosure
# ----------------------------------------------------------------------
def test_classify_periodic_business_report():
    assert classify_disclosure(_disc(report_nm="사업보고서")) == DisclosureType.PERIODIC


def test_classify_quarterly_separate_from_periodic():
    """M1: 분기보고서는 PERIODIC이 아닌 QUARTERLY로 분류."""
    assert (classify_disclosure(_disc(report_nm="분기보고서(1Q26)"))
            == DisclosureType.QUARTERLY)


def test_classify_halfly_separate_from_periodic():
    """M1: 반기보고서는 HALFLY로 분류."""
    assert (classify_disclosure(_disc(report_nm="반기보고서"))
            == DisclosureType.HALFLY)


def test_classify_amendment_overrides_others():
    """정정공시는 다른 모든 분류를 덮어쓴다."""
    # 사업보고서 정정 → AMENDMENT (PERIODIC 아님)
    assert (classify_disclosure(_disc(report_nm="[기재정정]사업보고서"))
            == DisclosureType.AMENDMENT)
    # 자사주 결정 정정 → AMENDMENT (BUYBACK 아님)
    assert (classify_disclosure(
        _disc(report_nm="[기재정정]주요사항보고서(자기주식취득결정)"))
            == DisclosureType.AMENDMENT)


def test_classify_buyback():
    assert (classify_disclosure(
        _disc(report_nm="주요사항보고서(자기주식취득결정)"))
            == DisclosureType.BUYBACK)
    assert (classify_disclosure(
        _disc(report_nm="자기주식소각결정"))
            == DisclosureType.BUYBACK)


def test_classify_dividend():
    assert (classify_disclosure(_disc(report_nm="현금ㆍ현물배당결정"))
            == DisclosureType.DIVIDEND)
    assert (classify_disclosure(_disc(report_nm="주당배당금공시"))
            == DisclosureType.DIVIDEND)


def test_classify_ma_merger():
    assert (classify_disclosure(_disc(report_nm="주요사항보고서(회사합병결정)"))
            == DisclosureType.MA)
    assert (classify_disclosure(_disc(report_nm="주식교환ㆍ이전결정"))
            == DisclosureType.MA)
    assert (classify_disclosure(_disc(report_nm="회사분할결정"))
            == DisclosureType.MA)


def test_classify_major_other():
    assert (classify_disclosure(_disc(report_nm="주요사항보고서(특별관계자거래)"))
            == DisclosureType.MAJOR)


def test_classify_other_returns_other():
    assert (classify_disclosure(_disc(report_nm="공정공시"))
            == DisclosureType.OTHER)
    assert (classify_disclosure(_disc(report_nm="기업설명회개최"))
            == DisclosureType.OTHER)


# ----------------------------------------------------------------------
# needs_data_refresh
# ----------------------------------------------------------------------
def test_needs_refresh_for_periodic_annual_only():
    """M1: 연간 사업보고서만 재수집 트리거. 분기/반기는 별도 처리."""
    assert needs_data_refresh(_disc(report_nm="사업보고서"))


def test_quarterly_does_not_trigger_refresh():
    """M1: financial_metrics가 annual-only라 분기보고서는 재수집 안 함."""
    assert not needs_data_refresh(_disc(report_nm="분기보고서(1Q26)"))


def test_halfly_does_not_trigger_refresh():
    """M1: 반기보고서도 annual-only 정책상 재수집 안 함."""
    assert not needs_data_refresh(_disc(report_nm="반기보고서"))


def test_needs_refresh_for_amendment():
    assert needs_data_refresh(_disc(report_nm="[기재정정]사업보고서"))
    assert needs_data_refresh(_disc(report_nm="[정정]자기주식취득결정"))


def test_needs_refresh_for_ma():
    assert needs_data_refresh(_disc(report_nm="주요사항보고서(회사합병결정)"))
    assert needs_data_refresh(_disc(report_nm="회사분할결정"))


def test_no_refresh_for_dividend():
    assert not needs_data_refresh(_disc(report_nm="현금ㆍ현물배당결정"))


def test_no_refresh_for_buyback():
    assert not needs_data_refresh(_disc(report_nm="자기주식취득결정"))


def test_no_refresh_for_other():
    assert not needs_data_refresh(_disc(report_nm="공정공시"))
    assert not needs_data_refresh(_disc(report_nm="주요사항보고서(특별관계자거래)"))


# ----------------------------------------------------------------------
# fetch_disclosures
# ----------------------------------------------------------------------
def _make_client_with_items(items: list[dict]) -> MagicMock:
    client = MagicMock()
    client.fetch_disclosure_list = MagicMock(return_value=items)
    return client


def test_fetch_disclosures_returns_list_of_disclosure():
    items = [
        {
            "rcept_no": "20260429001",
            "corp_code": "00111111",
            "stock_code": "004800",
            "corp_name": "효성",
            "report_nm": "분기보고서(1Q26)",
            "rcept_dt": "20260429",
            "rm": "",
        }
    ]
    client = _make_client_with_items(items)
    out = fetch_disclosures(
        date_from="20260429", date_to="20260429", client=client,
    )
    assert len(out) == 1
    assert isinstance(out[0], Disclosure)
    assert out[0].stock_code == "004800"
    assert out[0].report_nm == "분기보고서(1Q26)"
    client.fetch_disclosure_list.assert_called_once_with(
        bgn_de="20260429", end_de="20260429", corp_cls="Y",
    )


def test_fetch_disclosures_filters_by_analyzed_codes():
    items = [
        {"rcept_no": "1", "corp_code": "a", "stock_code": "004800",
         "corp_name": "효성", "report_nm": "사업보고서", "rcept_dt": "20260429"},
        {"rcept_no": "2", "corp_code": "b", "stock_code": "999999",
         "corp_name": "외부", "report_nm": "사업보고서", "rcept_dt": "20260429"},
        {"rcept_no": "3", "corp_code": "c", "stock_code": "005930",
         "corp_name": "삼성", "report_nm": "[기재정정]사업보고서",
         "rcept_dt": "20260429"},
    ]
    client = _make_client_with_items(items)
    out = fetch_disclosures(
        date_from="20260429", date_to="20260429",
        analyzed_codes={"004800", "005930"}, client=client,
    )
    codes = {d.stock_code for d in out}
    assert codes == {"004800", "005930"}


def test_fetch_disclosures_handles_empty():
    client = _make_client_with_items([])
    out = fetch_disclosures(
        date_from="20260429", date_to="20260429", client=client,
    )
    assert out == []


def test_fetch_disclosures_handles_missing_keys():
    """일부 키가 빠진 응답도 빈 문자열로 안전하게 변환."""
    items = [
        {"rcept_no": "1", "corp_code": "a", "stock_code": "004800",
         "corp_name": "효성", "report_nm": "사업보고서", "rcept_dt": "20260429"},
        # rm 없음 — 기본 ''로
    ]
    client = _make_client_with_items(items)
    out = fetch_disclosures(
        date_from="20260429", date_to="20260429", client=client,
    )
    assert len(out) == 1
    assert out[0].rm == ""


def test_fetch_disclosures_corp_cls_pass_through():
    """corp_cls 파라미터는 클라이언트에 그대로 전달."""
    client = _make_client_with_items([])
    fetch_disclosures(
        date_from="20260101", date_to="20260131",
        corp_cls="K", client=client,
    )
    client.fetch_disclosure_list.assert_called_once_with(
        bgn_de="20260101", end_de="20260131", corp_cls="K",
    )


def test_fetch_disclosures_handles_api_failure():
    """클라이언트가 예외를 던지면 그대로 전파 (호출자가 처리)."""
    client = MagicMock()
    client.fetch_disclosure_list = MagicMock(
        side_effect=RuntimeError("DART 503"),
    )
    with pytest.raises(RuntimeError, match="DART 503"):
        fetch_disclosures(
            date_from="20260429", date_to="20260429", client=client,
        )


# ----------------------------------------------------------------------
# 통합 시나리오
# ----------------------------------------------------------------------
def test_full_workflow_classify_and_filter_periodic_only():
    """fetch → classify → needs_refresh 조합 파이프라인."""
    items = [
        {"rcept_no": "1", "corp_code": "a", "stock_code": "004800",
         "corp_name": "효성", "report_nm": "분기보고서(1Q26)",
         "rcept_dt": "20260429"},
        {"rcept_no": "2", "corp_code": "b", "stock_code": "005930",
         "corp_name": "삼성전자", "report_nm": "현금ㆍ현물배당결정",
         "rcept_dt": "20260429"},
        {"rcept_no": "3", "corp_code": "c", "stock_code": "000270",
         "corp_name": "기아", "report_nm": "[기재정정]사업보고서",
         "rcept_dt": "20260429"},
    ]
    client = _make_client_with_items(items)
    disclosures = fetch_disclosures(
        date_from="20260429", date_to="20260429", client=client,
    )
    refresh_needed = [d for d in disclosures if needs_data_refresh(d)]
    types = {d.stock_code: classify_disclosure(d) for d in disclosures}

    # M1: 분기보고서는 QUARTERLY, 재수집 안 함
    assert types["004800"] == DisclosureType.QUARTERLY
    assert types["005930"] == DisclosureType.DIVIDEND
    assert types["000270"] == DisclosureType.AMENDMENT
    # 기아(정정)만 재수집 필요. 효성(분기)은 annual-only 정책으로 제외.
    assert {d.stock_code for d in refresh_needed} == {"000270"}


# ----------------------------------------------------------------------
# M1: QUARTERLY/HALFLY enum + 회귀
# ----------------------------------------------------------------------
def test_disclosure_type_includes_quarterly_and_halfly():
    """M1: 신규 enum 값 회귀."""
    assert DisclosureType.QUARTERLY.value == "quarterly"
    assert DisclosureType.HALFLY.value == "halfly"


def test_periodic_only_matches_annual_business_report():
    """PERIODIC은 사업보고서(연간)만. 분기/반기 키워드와 명확 분리."""
    assert (classify_disclosure(_disc(report_nm="사업보고서"))
            == DisclosureType.PERIODIC)
    # 사업보고서가 부분문자열인 다른 보고서명은 없으나, 가드용.
    assert (classify_disclosure(_disc(report_nm="[기재정정]사업보고서"))
            == DisclosureType.AMENDMENT)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

"""DART API 파싱 단위 테스트 — CIS fallback 검증.

배경: K-IFRS 단일 포괄손익계산서(sj_div='CIS')만 제출하는 기업이 다수.
      _get_account_value가 IS만 보던 버그(77% PL 결손)의 회귀 방지.

실행: pytest tests/test_dart_api.py -v
"""

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from collectors.dart_api import DARTClient


CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "dart_cache"


def _row(sj_div: str, account_nm: str, thstrm_amount: str) -> dict:
    return {
        "sj_div": sj_div,
        "account_nm": account_nm,
        "thstrm_amount": thstrm_amount,
    }


@pytest.fixture
def client() -> DARTClient:
    return DARTClient()


# ================================================================
# 1) IS만 있을 때 — 기존 정상 케이스
# ================================================================
def test_is_only_normal_case(client: DARTClient) -> None:
    df = pd.DataFrame([
        _row("BS", "자산총계", "1000000"),
        _row("IS", "매출액", "500000"),
        _row("IS", "영업이익", "100000"),
        _row("IS", "당기순이익", "80000"),
    ])
    assert client._get_account_value(df, "IS", ["매출액"]) == 500000
    assert client._get_account_value(df, "IS", ["영업이익"]) == 100000
    assert client._get_account_value(df, "IS", ["당기순이익"]) == 80000


# ================================================================
# 2) CIS만 있을 때 — 이번 수정 핵심
# ================================================================
def test_cis_only_fallback(client: DARTClient) -> None:
    df = pd.DataFrame([
        _row("BS", "자산총계", "37910298847681"),
        _row("CIS", "매출", "13738354725833"),
        _row("CIS", "영업이익", "547037221095"),
        _row("CIS", "당기순이익(손실)", "73555557525"),
    ])
    assert client._get_account_value(df, "IS", ["매출액", "매출", "수익(매출액)", "영업수익"]) == 13738354725833
    assert client._get_account_value(df, "IS", ["영업이익", "영업이익(손실)", "영업손익"]) == 547037221095
    assert client._get_account_value(df, "IS", ["당기순이익", "당기순이익(손실)"]) == 73555557525


# ================================================================
# 3) IS와 CIS 둘 다 있으면 IS 우선
# ================================================================
def test_both_is_and_cis_prefers_is(client: DARTClient) -> None:
    df = pd.DataFrame([
        _row("IS", "매출액", "100"),
        _row("CIS", "매출액", "999"),
    ])
    assert client._get_account_value(df, "IS", ["매출액"]) == 100


# ================================================================
# 4) IS·CIS 둘 다 없으면 0
# ================================================================
def test_neither_is_nor_cis_returns_zero(client: DARTClient) -> None:
    df = pd.DataFrame([
        _row("BS", "자산총계", "1000"),
        _row("CF", "영업활동현금흐름", "200"),
    ])
    assert client._get_account_value(df, "IS", ["매출액"]) == 0


# ================================================================
# 5) IS는 있으나 매칭되는 계정명이 없을 때 → CIS로 fallback
# ================================================================
def test_is_present_but_account_missing_falls_back_to_cis(client: DARTClient) -> None:
    df = pd.DataFrame([
        _row("IS", "기타수익", "10"),
        _row("IS", "기타비용", "20"),
        _row("CIS", "매출액", "777"),
    ])
    assert client._get_account_value(df, "IS", ["매출액"]) == 777


# ================================================================
# 6) 비-IS 호출은 fallback 영향 없음 (BS는 BS만, CF는 CF만)
# ================================================================
def test_non_is_div_no_fallback(client: DARTClient) -> None:
    df = pd.DataFrame([
        _row("BS", "자산총계", "5000"),
        _row("CIS", "자산총계", "9999"),  # CIS에 들어있어도 BS 호출은 무시
    ])
    assert client._get_account_value(df, "BS", ["자산총계"]) == 5000


# ================================================================
# 7) 영업손실 라벨 매칭 (적자기업 — 011170, 020150 패턴)
# ================================================================
def test_operating_loss_label_matched_as_negative(client: DARTClient) -> None:
    """적자기업이 '영업손실' 라벨로 표기하는 경우 음수값으로 추출."""
    df = pd.DataFrame([
        _row("CIS", "매출", "18483005314922"),
        _row("CIS", "영업손실", "-943115729953"),
        _row("CIS", "기본및희석주당중단영업이익", "130"),
    ])
    op = client._get_account_value(
        df, "IS", ["영업이익", "영업이익(손실)", "영업손익", "영업손실"]
    )
    assert op == -943115729953


def test_operating_loss_does_not_match_eps_partial(client: DARTClient) -> None:
    """'기본및희석주당중단영업이익' 같은 EPS 라벨이 정확 일치 단계에서
    '영업이익' 부분 매칭으로 잘못 잡히지 않아야 한다.
    (정확 일치 단계가 부분 일치보다 우선이라 회귀 방지된다)"""
    df = pd.DataFrame([
        _row("CIS", "기본및희석주당중단영업이익", "130"),
        _row("CIS", "영업손실", "-9000"),
    ])
    # 정확 일치로 영업손실 매칭 → -9000
    op = client._get_account_value(
        df, "IS", ["영업이익", "영업이익(손실)", "영업손익", "영업손실"]
    )
    assert op == -9000


# ================================================================
# 9) dividend_yield 전년도 폴백 (HIGH-2)
# ================================================================
def test_dividend_yield_uses_prev_year_when_current_missing(monkeypatch, client: DARTClient) -> None:
    """당해 사업보고서에 배당수익률 미공시('-' → 0.0)면 전년도로 폴백."""
    calls: list[int] = []

    def fake_fetch(code: str, year: int) -> float:
        calls.append(year)
        if year == 2025:
            return 0.0  # 미공시
        if year == 2024:
            return 1.5
        return 0.0

    monkeypatch.setattr(client, "_fetch_dividend_yield_for_year", fake_fetch)
    result = client._get_dividend_yield("005930", 2025)
    assert result == 1.5
    assert calls == [2025, 2024]


def test_dividend_yield_uses_current_when_present(monkeypatch, client: DARTClient) -> None:
    """당해 정상값이면 전년도 호출하지 않음 (회귀 방지 + 호출 절약)."""
    calls: list[int] = []

    def fake_fetch(code: str, year: int) -> float:
        calls.append(year)
        return 2.3 if year == 2025 else 1.0

    monkeypatch.setattr(client, "_fetch_dividend_yield_for_year", fake_fetch)
    result = client._get_dividend_yield("005930", 2025)
    assert result == 2.3
    assert calls == [2025]


def test_dividend_yield_zero_when_both_years_missing(monkeypatch, client: DARTClient) -> None:
    """양 연도 모두 미공시면 0.0 (진짜 무배당)."""
    monkeypatch.setattr(
        client, "_fetch_dividend_yield_for_year",
        lambda code, year: 0.0,
    )
    assert client._get_dividend_yield("005930", 2025) == 0.0


# ================================================================
# 10) 빈값/"-" 안전 처리 (회귀)
# ================================================================
def test_empty_amount_safe(client: DARTClient) -> None:
    df = pd.DataFrame([_row("IS", "매출액", "")])
    assert client._get_account_value(df, "IS", ["매출액"]) == 0
    df = pd.DataFrame([_row("IS", "매출액", "-")])
    assert client._get_account_value(df, "IS", ["매출액"]) == 0


# ================================================================
# 8) 실제 캐시 fixture로 회귀 검증 (있을 때만)
# ================================================================
@pytest.mark.skipif(
    not (CACHE_DIR / "023530_2025_annual.parquet").exists(),
    reason="023530 cache parquet not present in this checkout",
)
def test_real_cache_023530_lotte_shopping(client: DARTClient) -> None:
    df = pd.read_parquet(CACHE_DIR / "023530_2025_annual.parquet")
    # 결손 패턴: IS=0, CIS=36 (조사 보고서 기준)
    assert (df["sj_div"] == "IS").sum() == 0
    assert (df["sj_div"] == "CIS").sum() > 0
    # CIS fallback이 동작하면 정상값 추출
    revenue = client._get_account_value(df, "IS", ["매출액", "매출", "수익(매출액)", "영업수익"])
    op_income = client._get_account_value(df, "IS", ["영업이익", "영업이익(손실)", "영업손익"])
    net_income = client._get_account_value(df, "IS", ["당기순이익", "당기순이익(손실)"])
    assert revenue > 0, "CIS fallback 실패: 매출 결손"
    assert op_income > 0, "CIS fallback 실패: 영업이익 결손"
    assert net_income != 0, "CIS fallback 실패: 당기순이익 결손"


@pytest.mark.skipif(
    not (CACHE_DIR / "005930_2025_annual.parquet").exists(),
    reason="005930 cache parquet not present in this checkout",
)
def test_real_cache_005930_samsung_no_regression(client: DARTClient) -> None:
    df = pd.read_parquet(CACHE_DIR / "005930_2025_annual.parquet")
    # 정상 종목: IS와 CIS 둘 다 있음
    assert (df["sj_div"] == "IS").sum() > 0
    revenue = client._get_account_value(df, "IS", ["매출액"])
    # 삼성전자 2025 매출액은 333조원대 (조사 보고서 raw 기준)
    assert revenue > 100_000_000_000_000


# ================================================================
# 11) 금융주 sector 분기 — _calc_financial_revenue (묶음 F)
# ================================================================
def test_insurance_revenue_ifrs4_pattern(client: DARTClient) -> None:
    """일반 손보 — 보험수익 + 투자영업수익 합산."""
    df = pd.DataFrame([
        _row("CIS", "보험수익", "14142885317456"),       # 14.14조
        _row("CIS", "투자영업수익", "3163940132746"),    # 3.16조
        _row("CIS", "이자수익", "30176345797"),
    ])
    result = client._calc_financial_revenue(df, "보험", "001450")
    assert result == 14142885317456 + 3163940132746


def test_insurance_revenue_ifrs17_pattern(client: DARTClient) -> None:
    """삼성생명 IFRS17 — 보험서비스수익 + 이자수익 + 수수료수익."""
    df = pd.DataFrame([
        _row("CIS", "보험서비스수익", "9890442000000"),
        _row("CIS", "이자수익", "8406565000000"),
        _row("CIS", "수수료수익", "2177176000000"),
    ])
    result = client._calc_financial_revenue(df, "보험", "032830")
    assert result == 9890442000000 + 8406565000000 + 2177176000000


def test_securities_revenue_uses_operating_when_present(client: DARTClient) -> None:
    """증권사 영업수익이 정확 매칭이면 그대로 사용 (한화투자/미래/키움)."""
    df = pd.DataFrame([
        _row("CIS", "영업수익", "3094578000000"),
        _row("CIS", "수수료수익", "281200000000"),
        _row("CIS", "이자수익", "391509000000"),
    ])
    result = client._calc_financial_revenue(df, "증권", "003530")
    assert result == 3094578000000


def test_securities_revenue_falls_back_to_components(client: DARTClient) -> None:
    """삼성증권/NH 같이 영업수익 라벨 부재 → 수수료+이자+외환 합산."""
    df = pd.DataFrame([
        _row("CIS", "수수료수익", "1420400000000"),
        _row("CIS", "이자수익", "1670057300671"),
        _row("CIS", "외환거래이익", "0"),
    ])
    result = client._calc_financial_revenue(df, "증권", "016360")
    assert result == 1420400000000 + 1670057300671


def test_bank_holding_revenue_components_sum(client: DARTClient) -> None:
    """BANK_HOLDING_CODES — 이자수익 + 수수료수익 + 보험수익 합산."""
    df = pd.DataFrame([
        _row("CIS", "이자수익", "27988801000000"),
        _row("CIS", "수수료수익", "4564323000000"),
        _row("CIS", "보험수익", "3364322000000"),
    ])
    result = client._calc_financial_revenue(df, "금융", "055550")  # 신한지주
    assert result == 27988801000000 + 4564323000000 + 3364322000000


def test_general_holding_in_금융_sector_unchanged(client: DARTClient) -> None:
    """sector='금융'이지만 BANK_HOLDING_CODES 외 (두산/CJ/LG 등) → None."""
    df = pd.DataFrame([
        _row("CIS", "이자수익", "999999"),
        _row("CIS", "수수료수익", "999999"),
    ])
    # 000150 두산 — sector='금융'인 일반 지주
    result = client._calc_financial_revenue(df, "금융", "000150")
    assert result is None


def test_non_financial_sector_unchanged(client: DARTClient) -> None:
    """비금융 sector (전기·전자, 화학 등) → None (기본 룰 사용)."""
    df = pd.DataFrame([
        _row("CIS", "매출액", "333605938000000"),
        _row("CIS", "이자수익", "9999"),
    ])
    assert client._calc_financial_revenue(df, "전기·전자", "005930") is None
    assert client._calc_financial_revenue(df, "화학", "011170") is None


def test_extract_financial_metrics_uses_sector_for_bank(
    client: DARTClient, monkeypatch
) -> None:
    """extract_financial_metrics에 sector='금융'+BANK 코드 전달 시 합산 매출 반환."""
    df = pd.DataFrame([
        _row("BS", "자산총계", "100000000"),
        _row("BS", "자본총계", "10000000"),
        _row("CIS", "이자수익", "27988801000000"),
        _row("CIS", "수수료수익", "4564323000000"),
        _row("CIS", "당기순이익(손실)", "5084519000000"),
    ])
    monkeypatch.setattr(client, "get_financial_statements", lambda code, y: df)
    metrics = client.extract_financial_metrics(
        "055550", year=2025, sector="금융",
    )
    # 합산: 이자(27.99조) + 수수료(4.56조) = 32.55조
    assert metrics["revenue"] == 27988801000000 + 4564323000000


# ================================================================
# FCF 매칭 보강 (account_id 우선 + 공백 정규화)
# ================================================================
# 배경: 한글 account_nm 공백 변형으로 OCF/CAPEX 매칭이 광범위 실패.
#       IFRS account_id로 100% 정확 매칭 가능.
#       docs/fcf_collection_audit_20260427.md 참조.

def test_account_id_match_takes_priority(client: DARTClient) -> None:
    """account_id가 주어지면 nm보다 우선 매칭."""
    df = pd.DataFrame([
        _row("CF", "엉뚱한이름", "999"),
        _row("CF", "원하는이름", "111"),
    ])
    df.loc[0, "account_id"] = "ifrs-full_TARGET"
    df.loc[1, "account_id"] = "ifrs-full_OTHER"
    result = client._get_account_value(
        df, "CF", ["원하는이름"],
        account_ids=["ifrs-full_TARGET"],
    )
    assert result == 999  # account_id 우선


# ================================================================
# N1: IFRS account_id로 매출/영업이익/당기순이익 매칭 보강
# ================================================================
def test_revenue_matches_via_ifrs_full_revenue_id(client: DARTClient) -> None:
    """account_nm이 변형되어도 ifrs-full_Revenue로 매출액 매칭."""
    df = pd.DataFrame([
        _row("CIS", "수익(매출액)", "2186637586358"),
    ])
    df.loc[0, "account_id"] = "ifrs-full_Revenue"
    result = client._get_account_value(
        df, "IS",
        ["매출액", "매출", "수익(매출액)", "영업수익"],
        account_ids=["ifrs-full_Revenue"],
    )
    assert result == 2186637586358


def test_op_income_matches_via_dart_op_income_loss(client: DARTClient) -> None:
    """K-IFRS 한국 회사 표준 dart_OperatingIncomeLoss로 영업이익 매칭."""
    df = pd.DataFrame([
        _row("CIS", "영업이익", "104374493823"),
    ])
    df.loc[0, "account_id"] = "dart_OperatingIncomeLoss"
    result = client._get_account_value(
        df, "IS",
        ["영업이익", "영업이익(손실)", "영업손익", "영업손실"],
        account_ids=[
            "dart_OperatingIncomeLoss",
            "ifrs-full_ProfitLossFromOperatingActivities",
        ],
    )
    assert result == 104374493823


def test_op_income_matches_via_ifrs_op_activities(client: DARTClient) -> None:
    """일부 회사는 ifrs-full_ProfitLossFromOperatingActivities 사용."""
    df = pd.DataFrame([
        _row("CIS", "영업이익(손실)", "55_000_000_000"),
    ])
    df.loc[0, "account_id"] = "ifrs-full_ProfitLossFromOperatingActivities"
    result = client._get_account_value(
        df, "IS",
        ["영업이익", "영업이익(손실)"],
        account_ids=[
            "dart_OperatingIncomeLoss",
            "ifrs-full_ProfitLossFromOperatingActivities",
        ],
    )
    assert result == 55_000_000_000


def test_net_income_matches_via_ifrs_profit_loss(client: DARTClient) -> None:
    """ifrs-full_ProfitLoss로 당기순이익 매칭 — 변형 account_nm 통과."""
    df = pd.DataFrame([
        _row("IS", "당기순이익(손실)", "80_000_000_000"),
    ])
    df.loc[0, "account_id"] = "ifrs-full_ProfitLoss"
    result = client._get_account_value(
        df, "IS",
        ["당기순이익", "당기순이익(손실)"],
        account_ids=["ifrs-full_ProfitLoss"],
    )
    assert result == 80_000_000_000


def test_revenue_via_id_when_nm_completely_missing(client: DARTClient) -> None:
    """account_nm이 본 후보 리스트에 전혀 없을 때도 ID로만 매칭."""
    df = pd.DataFrame([
        _row("IS", "totally_unknown_label", "1234567890"),
    ])
    df.loc[0, "account_id"] = "ifrs-full_Revenue"
    result = client._get_account_value(
        df, "IS",
        ["매출액"],  # 후보에 없음
        account_ids=["ifrs-full_Revenue"],
    )
    assert result == 1234567890


def test_account_id_falls_through_to_nm(client: DARTClient) -> None:
    """account_id 미일치 시 nm 매칭으로 fallback."""
    df = pd.DataFrame([
        _row("CF", "원하는이름", "111"),
    ])
    df.loc[0, "account_id"] = "ifrs-full_NOMATCH"
    result = client._get_account_value(
        df, "CF", ["원하는이름"],
        account_ids=["ifrs-full_NOTHERE"],
    )
    assert result == 111


def test_whitespace_normalization_works(client: DARTClient) -> None:
    """공백 변형 ('영업활동으로 인한 현금흐름') → 정규화로 매칭."""
    df = pd.DataFrame([
        _row("CF", "영업활동으로 인한 현금흐름", "421000000000"),
    ])
    result = client._get_account_value(
        df, "CF", ["영업활동현금흐름", "영업활동으로인한현금흐름"],
    )
    assert result == 421000000000


def test_normalize_nm_strips_spaces() -> None:
    assert DARTClient._normalize_nm("영업활동으로 인한 현금흐름") == "영업활동으로인한현금흐름"
    assert DARTClient._normalize_nm("유형자산의 취득") == "유형자산의취득"
    assert DARTClient._normalize_nm("영업활동현금흐름") == "영업활동현금흐름"


# ================================================================
# 8개 표본 골든값 — 실 캐시 raw + 신규 매칭 로직
# ================================================================
def _calc_fcf_from_cache(client: DARTClient, code: str) -> tuple[int, int, int]:
    """실 캐시에서 OCF/CAPEX/FCF 계산."""
    df = pd.read_parquet(CACHE_DIR / f"{code}_2025_annual.parquet")
    ocf = client._get_account_value(
        df, "CF",
        ["영업활동현금흐름", "영업활동으로인한현금흐름"],
        account_ids=["ifrs-full_CashFlowsFromUsedInOperatingActivities"],
    )
    capex = abs(client._get_account_value(
        df, "CF",
        ["유형자산의취득", "유형자산취득", "투자활동으로인한유형자산취득"],
        account_ids=[
            "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
            "ifrs-full_PurchaseOfPropertyPlantAndEquipment",
        ],
    ))
    fcf = ocf - capex if ocf != 0 else 0
    return ocf, capex, fcf


def test_fcf_recovers_004800_효성(client: DARTClient) -> None:
    """결손 → 회복: OCF/CAPEX/FCF 모두 양수."""
    ocf, capex, fcf = _calc_fcf_from_cache(client, "004800")
    assert ocf == 421093178280
    assert capex == 158920799942
    assert fcf == ocf - capex


def test_fcf_recovers_002790_amorepacific_g(client: DARTClient) -> None:
    ocf, capex, fcf = _calc_fcf_from_cache(client, "002790")
    assert ocf == 629668209209
    assert capex == 76814037729
    assert fcf > 0


def test_fcf_recovers_001040_cj(client: DARTClient) -> None:
    ocf, capex, fcf = _calc_fcf_from_cache(client, "001040")
    assert ocf == 4987380240000
    assert capex == 2335628897000
    assert fcf > 0


def test_fcf_recovers_003550_lg(client: DARTClient) -> None:
    ocf, capex, fcf = _calc_fcf_from_cache(client, "003550")
    assert ocf == 1015114000000
    assert capex == 151375000000
    assert fcf > 0


def test_fcf_recovers_004990_lotte(client: DARTClient) -> None:
    ocf, capex, fcf = _calc_fcf_from_cache(client, "004990")
    assert ocf == 1156131183818
    assert capex > 0
    assert fcf == ocf - capex


def test_fcf_005930_samsung_capex_corrected(client: DARTClient) -> None:
    """삼성전자: 기존 CAPEX=0 (2.25× 과대) → 정확값으로 정정."""
    ocf, capex, fcf = _calc_fcf_from_cache(client, "005930")
    assert ocf == 85315148000000
    assert capex == 47522179000000  # 기존엔 매칭 실패로 0
    assert fcf == ocf - capex  # ≈ 37.8조


def test_fcf_000150_doosan_capex_corrected(client: DARTClient) -> None:
    ocf, capex, fcf = _calc_fcf_from_cache(client, "000150")
    assert ocf == 981908000000
    assert capex == 570134000000  # 기존엔 0
    assert fcf == ocf - capex


def test_fcf_005440_hyundaigf_capex_corrected(client: DARTClient) -> None:
    ocf, capex, fcf = _calc_fcf_from_cache(client, "005440")
    assert ocf == 404603485000
    assert capex == 147603916000  # 기존엔 0
    assert fcf == ocf - capex


# ================================================================
# prev_revenue sector 분기 (2026-04-30 회복 PR)
# 기존 prev_revenue 경로는 _calc_financial_revenue 호출 누락으로
# 금융주 prev_revenue=0이 47% 누적. 분기 추가로 회복.
# ================================================================
def test_extract_prev_revenue_uses_financial_path_for_bank(
    client: DARTClient, monkeypatch
) -> None:
    """sector='금융'+BANK 코드 전달 시 prev_revenue도 합산 매출 반환."""
    # 당기 + 전기 동일 라벨 (분리 비교를 위해 금액만 다르게)
    curr_df = pd.DataFrame([
        _row("BS", "자산총계", "100"),
        _row("BS", "자본총계", "10"),
        _row("CIS", "이자수익", "27988801000000"),
        _row("CIS", "수수료수익", "4564323000000"),
        _row("CIS", "당기순이익(손실)", "5084519000000"),
    ])
    prev_df = pd.DataFrame([
        _row("BS", "자산총계", "90"),
        _row("BS", "자본총계", "9"),
        _row("CIS", "이자수익", "20000000000000"),
        _row("CIS", "수수료수익", "3000000000000"),
        _row("CIS", "당기순이익(손실)", "4000000000000"),
    ])

    def fake_get(code, y):
        return curr_df if y == 2025 else prev_df

    monkeypatch.setattr(client, "get_financial_statements", fake_get)
    metrics = client.extract_financial_metrics(
        "055550", year=2025, sector="금융",
    )
    # 당기: 27.99T + 4.56T = 32.55T
    assert metrics["revenue"] == 27988801000000 + 4564323000000
    # 전기: 20T + 3T = 23T (sector 분기로 합산 — 핵심 검증)
    assert metrics["prev_revenue"] == 20000000000000 + 3000000000000


def test_extract_prev_revenue_uses_general_path_for_normal(
    client: DARTClient, monkeypatch
) -> None:
    """일반 종목은 _calc_financial_revenue가 None을 반환해 일반 라벨로 fallback."""
    curr_df = pd.DataFrame([
        _row("CIS", "매출액", "100000"),
    ])
    prev_df = pd.DataFrame([
        _row("CIS", "매출액", "80000"),
    ])
    curr_df.loc[0, "account_id"] = "ifrs-full_Revenue"
    prev_df.loc[0, "account_id"] = "ifrs-full_Revenue"

    def fake_get(code, y):
        return curr_df if y == 2025 else prev_df

    monkeypatch.setattr(client, "get_financial_statements", fake_get)
    metrics = client.extract_financial_metrics(
        "005930", year=2025, sector="전기·전자",
    )
    assert metrics["revenue"] == 100000
    assert metrics["prev_revenue"] == 80000


def test_extract_prev_revenue_handles_missing_sector(
    client: DARTClient, monkeypatch
) -> None:
    """sector=None일 때도 일반 라벨로 prev_revenue 정상 추출 (회귀 방지)."""
    curr_df = pd.DataFrame([_row("CIS", "매출액", "100000")])
    prev_df = pd.DataFrame([_row("CIS", "매출액", "80000")])
    curr_df.loc[0, "account_id"] = "ifrs-full_Revenue"
    prev_df.loc[0, "account_id"] = "ifrs-full_Revenue"

    def fake_get(code, y):
        return curr_df if y == 2025 else prev_df

    monkeypatch.setattr(client, "get_financial_statements", fake_get)
    metrics = client.extract_financial_metrics("005930", year=2025)
    assert metrics["revenue"] == 100000
    assert metrics["prev_revenue"] == 80000


# ================================================================
# E1 Phase 2: BS account_id fallback (4-30 진단 그룹 A 8종 회복)
# 배경: revenue/op/net에는 86ec996에서 account_ids fallback 추가됐으나
#       BS (assets/equity 등)에는 미적용. account_nm 변형
#       ("총자산", "자산 합계", "기말자본", "자본") 매칭 실패 → 결손 8종.
# ================================================================
def test_extract_total_assets_uses_account_id_fallback(
    client: DARTClient, monkeypatch
) -> None:
    """account_nm이 비표준이어도 ifrs-full_Assets로 매칭."""
    df = pd.DataFrame([
        _row("BS", "총자산", "18753010588000"),  # 047050/004170 패턴
        _row("BS", "자본총계", "7800000000000"),
        _row("CIS", "매출액", "32000000000000"),
    ])
    df.loc[0, "account_id"] = "ifrs-full_Assets"
    df.loc[1, "account_id"] = "ifrs-full_Equity"
    df.loc[2, "account_id"] = "ifrs-full_Revenue"

    monkeypatch.setattr(client, "get_financial_statements", lambda c, y: df)
    m = client.extract_financial_metrics("047050", year=2025)
    assert m["total_assets"] == 18753010588000


def test_extract_total_equity_uses_account_id_fallback(
    client: DARTClient, monkeypatch
) -> None:
    """기말자본/자본 등 비표준 account_nm도 ifrs-full_Equity로 매칭."""
    df = pd.DataFrame([
        _row("BS", "자산총계", "3134104265112"),
        _row("BS", "기말자본", "676823890646"),  # 066970 패턴
        _row("CIS", "매출액", "2154938965103"),
    ])
    df.loc[0, "account_id"] = "ifrs-full_Assets"
    df.loc[1, "account_id"] = "ifrs-full_Equity"
    df.loc[2, "account_id"] = "ifrs-full_Revenue"

    monkeypatch.setattr(client, "get_financial_statements", lambda c, y: df)
    m = client.extract_financial_metrics("066970", year=2025)
    assert m["total_equity"] == 676823890646


def test_extract_handles_korean_label_variants(
    client: DARTClient, monkeypatch
) -> None:
    """account_id 없이도 label 후보 확장(`자산`, `자본`, `자산 합계`)으로 매칭."""
    df = pd.DataFrame([
        _row("BS", "자산", "10490130570000"),  # 005440 패턴
        _row("BS", "자본", "7306462632000"),
        _row("BS", "부채", "3183667938000"),
        _row("CIS", "매출액", "8091605017000"),
    ])
    # account_id 컬럼 없이도 nm 매칭 가능해야 함
    monkeypatch.setattr(client, "get_financial_statements", lambda c, y: df)
    m = client.extract_financial_metrics("005440", year=2025)
    assert m["total_assets"] == 10490130570000
    assert m["total_equity"] == 7306462632000
    assert m["total_liabilities"] == 3183667938000


def test_extract_total_assets_label_only_still_works(
    client: DARTClient, monkeypatch
) -> None:
    """기존 표준 label `자산총계` 회귀 방지."""
    df = pd.DataFrame([
        _row("BS", "자산총계", "1000000"),
        _row("BS", "자본총계", "600000"),
    ])
    monkeypatch.setattr(client, "get_financial_statements", lambda c, y: df)
    m = client.extract_financial_metrics("005930", year=2025)
    assert m["total_assets"] == 1000000
    assert m["total_equity"] == 600000


def test_extract_real_cache_047050_posco_int_assets_recovered(
    client: DARTClient,
) -> None:
    """4-30 진단 그룹 A: 047050 포스코인터내셔널 (account_nm='총자산')."""
    pq = CACHE_DIR / "047050_2025_annual.parquet"
    if not pq.exists():
        pytest.skip("cache not present")
    df = pd.read_parquet(pq)
    val = client._get_account_value(
        df, "BS", ["자산총계", "총자산", "자산 합계", "자산"],
        account_ids=["ifrs-full_Assets"],
    )
    assert val == 18753010588000


def test_extract_real_cache_066970_lnf_equity_recovered(
    client: DARTClient,
) -> None:
    """4-30 진단 그룹 A: 066970 엘앤에프 (account_nm='기말자본')."""
    pq = CACHE_DIR / "066970_2025_annual.parquet"
    if not pq.exists():
        pytest.skip("cache not present")
    df = pd.read_parquet(pq)
    val = client._get_account_value(
        df, "BS", ["자본총계", "기말자본", "자본"],
        account_ids=["ifrs-full_Equity"],
    )
    assert val == 676823890646

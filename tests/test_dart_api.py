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

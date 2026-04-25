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
# 7) 빈값/"-" 안전 처리 (회귀)
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

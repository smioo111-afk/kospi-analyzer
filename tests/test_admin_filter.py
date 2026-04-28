"""analysis.admin_filter 회귀 테스트."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.admin_filter import (  # noqa: E402
    filter_admin_stocks,
    is_admin_or_suspended,
)


def _stock(code: str, **overrides) -> dict:
    """테스트용 normalized price dict."""
    base = {
        "stock_code": code,
        "stock_name": f"S{code}",
        "iscd_stat_cls_code": "55",  # 기본: 정상
        "mang_issu_cls_code": "N",
        "temp_stop_yn": "N",
        "sltr_yn": "N",
    }
    base.update(overrides)
    return base


# ----------------------------------------------------------------------
# is_admin_or_suspended (단일 판정)
# ----------------------------------------------------------------------
def test_normal_stock_passes():
    excluded, reason = is_admin_or_suspended(_stock("005930"))
    assert excluded is False
    assert reason == ""


def test_iscd_51_admin():
    excluded, reason = is_admin_or_suspended(_stock("X", iscd_stat_cls_code="51"))
    assert excluded is True
    assert reason == "관리종목"


def test_iscd_58_suspended():
    excluded, reason = is_admin_or_suspended(_stock("X", iscd_stat_cls_code="58"))
    assert excluded is True
    assert reason == "거래정지"


def test_mang_issu_y_admin():
    excluded, reason = is_admin_or_suspended(_stock("X", mang_issu_cls_code="Y"))
    assert excluded is True
    assert reason == "관리종목"


def test_temp_stop_y():
    excluded, reason = is_admin_or_suspended(_stock("X", temp_stop_yn="Y"))
    assert excluded is True
    assert reason == "임시정지"


def test_sltr_y():
    excluded, reason = is_admin_or_suspended(_stock("X", sltr_yn="Y"))
    assert excluded is True
    assert reason == "정리매매"


def test_lowercase_y_handled():
    """API가 소문자 'y'를 반환해도 처리."""
    excluded, reason = is_admin_or_suspended(_stock("X", temp_stop_yn="y"))
    assert excluded is True


def test_warning_codes_pass():
    """투자주의/경고/위험은 분석 유지 (false positive 위험)."""
    for code in ("52", "53", "54"):
        excluded, _ = is_admin_or_suspended(
            _stock("X", iscd_stat_cls_code=code)
        )
        assert excluded is False, f"iscd={code}는 통과해야 함"


def test_missing_admin_fields_passes():
    """admin 필드가 없으면 정상으로 간주 (fail-open)."""
    minimal = {"stock_code": "X"}
    excluded, _ = is_admin_or_suspended(minimal)
    assert excluded is False


# ----------------------------------------------------------------------
# filter_admin_stocks (배치 필터)
# ----------------------------------------------------------------------
def test_filter_excludes_admin_keeps_normal():
    stocks = [
        _stock("A"),                                  # 정상
        _stock("B", iscd_stat_cls_code="51"),         # 관리
        _stock("C"),                                  # 정상
        _stock("D", iscd_stat_cls_code="58"),         # 거래정지
        _stock("E", temp_stop_yn="Y"),                # 임시정지
        _stock("F", sltr_yn="Y"),                     # 정리매매
        _stock("G"),                                  # 정상
    ]
    filtered, excluded_map = filter_admin_stocks(stocks)
    codes = [s["stock_code"] for s in filtered]
    assert codes == ["A", "C", "G"]
    assert "관리종목" in excluded_map
    assert "B" in excluded_map["관리종목"]
    assert "D" in excluded_map["거래정지"]
    assert "E" in excluded_map["임시정지"]
    assert "F" in excluded_map["정리매매"]


def test_filter_no_exclusions_returns_all():
    stocks = [_stock(f"S{i}") for i in range(5)]
    filtered, excluded_map = filter_admin_stocks(stocks)
    assert len(filtered) == 5
    assert excluded_map == {}


def test_filter_empty_input():
    filtered, excluded_map = filter_admin_stocks([])
    assert filtered == []
    assert excluded_map == {}


def test_filter_does_not_mutate_input():
    """필터는 부수효과가 없어야 함 (입력 리스트 보존)."""
    stocks = [_stock("A"), _stock("B", iscd_stat_cls_code="51")]
    original_len = len(stocks)
    filter_admin_stocks(stocks)
    assert len(stocks) == original_len


# ----------------------------------------------------------------------
# 정상 종목 회귀: KIS API 실제 응답 패턴
# ----------------------------------------------------------------------
def test_real_kis_response_pattern_passes():
    """삼성전자 실제 응답 형태 (모두 정상값)는 통과해야 함."""
    samsung = _stock(
        "005930",
        iscd_stat_cls_code="55",
        mang_issu_cls_code="N",
        temp_stop_yn="N",
        sltr_yn="N",
    )
    excluded, _ = is_admin_or_suspended(samsung)
    assert excluded is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

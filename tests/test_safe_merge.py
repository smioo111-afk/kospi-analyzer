"""B-MERGE-PROC: _merge_keep_nonempty 회귀 테스트."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.telegram_bot import _merge_keep_nonempty  # noqa: E402


def test_empty_values_do_not_override_meaningful_values():
    base = {"per": 12.5, "pbr": 1.3, "name": "삼성전자"}
    overlay = {"per": 0, "pbr": 0.0, "name": ""}  # 모두 빈 값
    out = _merge_keep_nonempty(base, overlay)
    assert out["per"] == 12.5
    assert out["pbr"] == 1.3
    assert out["name"] == "삼성전자"


def test_non_empty_overlay_does_override():
    base = {"per": 0, "pbr": 0.0, "name": ""}
    overlay = {"per": 12.5, "pbr": 1.3, "name": "삼성전자"}
    out = _merge_keep_nonempty(base, overlay)
    assert out["per"] == 12.5
    assert out["pbr"] == 1.3
    assert out["name"] == "삼성전자"


def test_priority_ordering_when_both_non_empty():
    """뒤에 오는 dict가 우선."""
    a = {"x": 1, "y": 1}
    b = {"x": 2}
    c = {"x": 3, "y": 0}  # y=0은 빈 값
    out = _merge_keep_nonempty(a, b, c)
    assert out["x"] == 3      # c가 우선
    assert out["y"] == 1      # c.y=0은 빈 값이라 a.y=1 보존


def test_first_occurrence_keeps_empty_value():
    """키가 처음 등장한 dict의 값이 빈 값이어도 채택."""
    out = _merge_keep_nonempty({"x": 0})
    assert out["x"] == 0


def test_none_treated_as_empty():
    base = {"x": 5}
    overlay = {"x": None}
    out = _merge_keep_nonempty(base, overlay)
    assert out["x"] == 5


def test_empty_string_treated_as_empty():
    base = {"name": "삼성전자"}
    overlay = {"name": ""}
    out = _merge_keep_nonempty(base, overlay)
    assert out["name"] == "삼성전자"


def test_handles_none_or_empty_dict_inputs():
    out = _merge_keep_nonempty({"x": 1}, None, {})
    assert out == {"x": 1}


def test_does_not_drop_keys_present_only_in_one_dict():
    a = {"a_only": 1}
    b = {"b_only": 2}
    out = _merge_keep_nonempty(a, b)
    assert out == {"a_only": 1, "b_only": 2}


def test_realistic_stock_score_merge():
    """실제 use-case: stock_scores 행이 일부 v3 컬럼이 0인 경우.

    v3_log: 성장/퀄리티 점수, 적정주가 등 nuanced 값 포함
    score (stock_scores): v1 컬럼은 채워져 있으나 v3 컬럼은 0
    기대: 두 dict의 비어있지 않은 값이 모두 살아남음
    """
    v3_log = {
        "growth_score": 12,
        "quality_score": 7,
        "fair_value_low": 70000,
        "fair_value_high": 90000,
        "per": 0,        # v3_log엔 PER 데이터 없음
        "pbr": 0.0,
    }
    score = {
        "per": 13.5,     # stock_scores의 PER
        "pbr": 1.2,
        "growth_score": 0,    # 빈 값 — v3_log를 덮으면 안됨
        "quality_score": 0,
        "fair_value_low": 0,
        "fair_value_high": 0,
        "total_score": 80,
    }
    merged = _merge_keep_nonempty(v3_log, score)
    assert merged["per"] == 13.5
    assert merged["pbr"] == 1.2
    assert merged["growth_score"] == 12   # v3_log 보존
    assert merged["quality_score"] == 7
    assert merged["fair_value_low"] == 70000
    assert merged["fair_value_high"] == 90000
    assert merged["total_score"] == 80


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

"""trigger_score_recalculation 손절가/ATR 보존 테스트.

자정 disclosure_monitor는 chart 데이터 없이 scorer를 호출하므로
stoploss/ATR이 0으로 산출된다. save_stock_scores UPSERT가 그 0으로
직전 정상값을 silent하게 덮어쓰는 regression이 발생했다 (2026-05-01).

수정안: trigger_score_recalculation에서 직전 stoploss/ATR이 양수일 때
이를 보존해 save_stock_scores에 stoploss_map으로 전달.
(momentum 보존 패턴과 동일)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.disclosure_impact import trigger_score_recalculation  # noqa: E402


def _full_score_row(**kw) -> dict:
    base = {
        "stock_code": "004800", "stock_name": "효성",
        "total_score": 50, "value_score": 15, "financial_score": 10,
        "growth_score": 8, "momentum_score": 12, "quality_score": 5,
        "signal": "hold", "signal_label": "HOLD",
        "current_price": 70000, "market_cap": 1_400_000_000_000,
        "per": 12.0, "pbr": 1.1, "roe": 8.5,
        "operating_margin": 7.0, "debt_ratio": 100.0,
        "dividend_yield": 2.5,
        "stoploss_price": 65100,
        "stoploss_pct": -7.0,
        "atr": 1500.0,
    }
    base.update(kw)
    return base


def _mock_db_with_score(prev_score: dict) -> MagicMock:
    db = MagicMock()
    db.get_stock_score.return_value = prev_score
    return db


def _scorer_returning(values: dict) -> MagicMock:
    scorer = MagicMock()
    base = {
        "stock_code": "004800",
        "value_score": 18, "financial_score": 12,
        "growth_score": 10, "momentum_score": 0, "quality_score": 9,
        "penalties": 0,
    }
    base.update(values)
    scorer.calculate_score.return_value = base
    return scorer


def _dart_client_ok() -> MagicMock:
    dart = MagicMock()
    dart.extract_financial_metrics.return_value = {
        "stock_code": "004800", "rcept_no": "NEW",
        "year": 2025, "sector": "화학",
    }
    return dart


def test_recalculation_preserves_stoploss_when_zero():
    """scorer가 stoploss=0 반환(=chart 없음) → 직전 stoploss로 보존."""
    db = _mock_db_with_score(_full_score_row(stoploss_price=65100, stoploss_pct=-7.0))
    scorer = _scorer_returning({"stoploss_price": 0, "stoploss_pct": 0, "atr": 0})
    trigger_score_recalculation(
        db=db, dart_client=_dart_client_ok(), scorer=scorer, stock_code="004800",
        save_to_db=True,
    )
    # save_stock_scores 호출 시 stoploss_map에 보존된 값 전달
    db.save_stock_scores.assert_called_once()
    kwargs = db.save_stock_scores.call_args.kwargs
    sl_map = kwargs.get("stoploss_map") or {}
    assert "004800" in sl_map
    assert sl_map["004800"]["effective_stoploss"] == 65100
    assert sl_map["004800"]["effective_stoploss_pct"] == -7.0


def test_recalculation_preserves_atr_when_zero():
    """scorer가 atr=0 반환 → 직전 atr 보존."""
    db = _mock_db_with_score(_full_score_row(stoploss_price=65100, atr=1500.0))
    scorer = _scorer_returning({"stoploss_price": 0, "stoploss_pct": 0, "atr": 0})
    trigger_score_recalculation(
        db=db, dart_client=_dart_client_ok(), scorer=scorer, stock_code="004800",
        save_to_db=True,
    )
    sl_map = db.save_stock_scores.call_args.kwargs.get("stoploss_map") or {}
    assert sl_map["004800"]["atr"] == 1500.0


def test_recalculation_uses_new_stoploss_when_provided():
    """scorer가 양수 stoploss 반환 → 신규 값을 사용 (직전 무시)."""
    db = _mock_db_with_score(_full_score_row(stoploss_price=65100))
    scorer = _scorer_returning({
        "stoploss_price": 70000, "stoploss_pct": -6.5, "atr": 2100.0,
    })
    trigger_score_recalculation(
        db=db, dart_client=_dart_client_ok(), scorer=scorer, stock_code="004800",
        save_to_db=True,
    )
    sl_map = db.save_stock_scores.call_args.kwargs.get("stoploss_map") or {}
    assert sl_map["004800"]["effective_stoploss"] == 70000
    assert sl_map["004800"]["effective_stoploss_pct"] == -6.5
    assert sl_map["004800"]["atr"] == 2100.0


def test_recalculation_no_stoploss_when_both_zero():
    """직전도 0, 신규도 0 → stoploss_map은 비어있음(None) 전달."""
    db = _mock_db_with_score(
        _full_score_row(stoploss_price=0, stoploss_pct=0, atr=0)
    )
    scorer = _scorer_returning({"stoploss_price": 0, "stoploss_pct": 0, "atr": 0})
    trigger_score_recalculation(
        db=db, dart_client=_dart_client_ok(), scorer=scorer, stock_code="004800",
        save_to_db=True,
    )
    sl_map = db.save_stock_scores.call_args.kwargs.get("stoploss_map")
    # 보존할 값이 없으면 None 또는 빈 dict 모두 허용
    assert not sl_map or "004800" not in sl_map

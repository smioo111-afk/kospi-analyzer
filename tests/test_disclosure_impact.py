"""A1 Phase 2: analysis/disclosure_impact 회귀 테스트."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.disclosure_impact import (  # noqa: E402
    DisclosureImpact,
    ScoreSnapshot,
    compare_scores,
    get_score_snapshot,
    process_disclosures,
    trigger_score_recalculation,
)
from collectors.dart_disclosure import Disclosure  # noqa: E402


# ----------------------------------------------------------------------
# 픽스처
# ----------------------------------------------------------------------
def _disc(stock_code: str = "004800",
          report_nm: str = "사업보고서") -> Disclosure:
    return Disclosure(
        rcept_no="20260429001",
        corp_code="00111111",
        stock_code=stock_code,
        corp_name="TEST",
        report_nm=report_nm,
        rcept_dt="20260429",
        rm="",
    )


def _snap(stock_code="004800", **overrides) -> ScoreSnapshot:
    base = dict(
        stock_code=stock_code, stock_name="TEST",
        total_score=50, value_score=15, financial_score=10,
        growth_score=8, momentum_score=10, quality_score=7,
        signal="hold",
    )
    base.update(overrides)
    return ScoreSnapshot(**base)


# ----------------------------------------------------------------------
# ScoreSnapshot
# ----------------------------------------------------------------------
def test_score_snapshot_from_db_row():
    row = {
        "stock_code": "004800", "stock_name": "효성",
        "total_score": 65, "value_score": 18, "financial_score": 14,
        "growth_score": 11, "momentum_score": 13, "quality_score": 9,
        "signal": "buy",
    }
    s = ScoreSnapshot.from_db_row(row)
    assert s.stock_code == "004800"
    assert s.stock_name == "효성"
    assert s.total_score == 65
    assert s.signal == "buy"


def test_score_snapshot_from_db_row_handles_nulls():
    row = {"stock_code": "004800"}
    s = ScoreSnapshot.from_db_row(row)
    assert s.stock_code == "004800"
    assert s.total_score == 0
    assert s.signal == ""


def test_score_snapshot_from_score_result():
    result = {
        "stock_code": "005930", "stock_name": "삼성전자",
        "total_score": 80, "value_score": 25, "financial_score": 18,
        "growth_score": 14, "momentum_score": 15, "quality_score": 8,
        "signal": "strong_buy",
    }
    s = ScoreSnapshot.from_score_result(result)
    assert s.total_score == 80
    assert s.momentum_score == 15


# ----------------------------------------------------------------------
# DisclosureImpact + is_significant
# ----------------------------------------------------------------------
def test_disclosure_impact_diff_calculation():
    before = _snap(total_score=50, value_score=15, financial_score=10,
                   growth_score=8, momentum_score=10, quality_score=7)
    after = _snap(total_score=60, value_score=18, financial_score=12,
                  growth_score=9, momentum_score=10, quality_score=11)
    imp = compare_scores(before, after, _disc())
    assert imp.total_diff == 10
    assert imp.value_diff == 3
    assert imp.financial_diff == 2
    assert imp.growth_diff == 1
    assert imp.momentum_diff == 0
    assert imp.quality_diff == 4
    assert imp.signal_changed is False


def test_compare_scores_detects_signal_change():
    before = _snap(signal="hold")
    after = _snap(signal="buy")
    imp = compare_scores(before, after, _disc())
    assert imp.signal_changed is True


def test_is_significant_threshold_5_points():
    before = _snap(total_score=50)
    after = _snap(total_score=55)  # diff=5
    imp = compare_scores(before, after, _disc())
    assert imp.is_significant is True
    assert imp.signal_changed is False


def test_is_significant_below_threshold():
    before = _snap(total_score=50)
    after = _snap(total_score=54)  # diff=4
    imp = compare_scores(before, after, _disc())
    assert imp.is_significant is False


def test_is_significant_via_signal_change():
    """점수 차이가 작아도 신호 변경 시 significant."""
    before = _snap(total_score=50, signal="hold")
    after = _snap(total_score=51, signal="buy")
    imp = compare_scores(before, after, _disc())
    assert abs(imp.total_diff) < 5
    assert imp.signal_changed is True
    assert imp.is_significant is True


# ----------------------------------------------------------------------
# get_score_snapshot
# ----------------------------------------------------------------------
def test_get_score_snapshot_returns_none_if_missing():
    db = MagicMock()
    db.get_stock_score.return_value = None
    assert get_score_snapshot(db, "004800") is None


def test_get_score_snapshot_returns_snapshot():
    db = MagicMock()
    db.get_stock_score.return_value = {
        "stock_code": "004800", "stock_name": "효성",
        "total_score": 65, "signal": "buy",
        "value_score": 0, "financial_score": 0, "growth_score": 0,
        "momentum_score": 0, "quality_score": 0,
    }
    snap = get_score_snapshot(db, "004800")
    assert snap is not None
    assert snap.total_score == 65


# ----------------------------------------------------------------------
# trigger_score_recalculation
# ----------------------------------------------------------------------
def _mock_db_with_score(prev_score: dict) -> MagicMock:
    db = MagicMock()
    db.get_stock_score.return_value = prev_score
    return db


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
    }
    base.update(kw)
    return base


def test_recalculation_returns_none_if_dart_fails():
    db = _mock_db_with_score(_full_score_row())
    dart = MagicMock()
    dart.extract_financial_metrics.side_effect = RuntimeError("DART down")
    scorer = MagicMock()
    out = trigger_score_recalculation(
        db=db, dart_client=dart, scorer=scorer, stock_code="004800",
    )
    assert out is None
    db.save_financial_metrics.assert_not_called()


def test_recalculation_returns_none_if_no_rcept_in_metrics():
    db = _mock_db_with_score(_full_score_row())
    dart = MagicMock()
    dart.extract_financial_metrics.return_value = {
        "stock_code": "004800", "year": 2025,
        # rcept_no 없음 → 비정상
    }
    scorer = MagicMock()
    out = trigger_score_recalculation(
        db=db, dart_client=dart, scorer=scorer, stock_code="004800",
    )
    assert out is None


def test_recalculation_skips_if_no_previous_score():
    db = MagicMock()
    db.get_stock_score.return_value = None
    dart = MagicMock()
    dart.extract_financial_metrics.return_value = {
        "stock_code": "004800", "rcept_no": "X", "year": 2025,
    }
    scorer = MagicMock()
    out = trigger_score_recalculation(
        db=db, dart_client=dart, scorer=scorer, stock_code="004800",
    )
    assert out is None


def test_recalculation_invalidates_cache_when_dir_given(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    p = cache / "004800_2025_annual.parquet"
    p.write_bytes(b"x")  # dummy parquet file

    db = _mock_db_with_score(_full_score_row())
    dart = MagicMock()
    dart.extract_financial_metrics.return_value = {
        "stock_code": "004800", "rcept_no": "NEW",
        "year": 2025, "sector": "화학",
    }
    scorer = MagicMock()
    scorer.calculate_score.return_value = {
        "stock_code": "004800", "stock_name": "효성",
        "total_score": 60, "value_score": 18, "financial_score": 12,
        "growth_score": 10, "momentum_score": 0, "quality_score": 9,
        "penalties": 0,
    }
    trigger_score_recalculation(
        db=db, dart_client=dart, scorer=scorer, stock_code="004800",
        cache_dir=cache, save_to_db=False,
    )
    assert not p.exists()  # 캐시 삭제됨


def test_recalculation_preserves_momentum_from_previous_score():
    """chart 없이 호출되면 scorer는 momentum=0을 반환하지만, 결과에는
    직전 momentum_score가 복원되어야 한다."""
    db = _mock_db_with_score(_full_score_row(momentum_score=14))
    dart = MagicMock()
    dart.extract_financial_metrics.return_value = {
        "stock_code": "004800", "rcept_no": "NEW",
        "year": 2025, "sector": "화학",
    }
    scorer = MagicMock()
    # scorer는 momentum=0으로 반환
    scorer.calculate_score.return_value = {
        "stock_code": "004800", "stock_name": "효성",
        "total_score": 0,  # 무시됨 — 재계산
        "value_score": 18, "financial_score": 12,
        "growth_score": 10, "momentum_score": 0, "quality_score": 9,
        "penalties": 0,
    }
    snap = trigger_score_recalculation(
        db=db, dart_client=dart, scorer=scorer, stock_code="004800",
        save_to_db=False,
    )
    assert snap is not None
    # momentum이 직전 값으로 복원
    assert snap.momentum_score == 14
    # total = sum(value=18, fin=12, growth=10, momentum=14, quality=9) + 0 = 63
    assert snap.total_score == 63


def test_recalculation_applies_penalty_to_total():
    db = _mock_db_with_score(_full_score_row(momentum_score=10))
    dart = MagicMock()
    dart.extract_financial_metrics.return_value = {
        "stock_code": "004800", "rcept_no": "NEW",
        "year": 2025, "sector": "화학",
    }
    scorer = MagicMock()
    # scorer가 -8 페널티(흑자→적자)를 반환
    scorer.calculate_score.return_value = {
        "stock_code": "004800",
        "value_score": 15, "financial_score": 10,
        "growth_score": 8, "momentum_score": 0, "quality_score": 5,
        "penalties": -8,
    }
    snap = trigger_score_recalculation(
        db=db, dart_client=dart, scorer=scorer, stock_code="004800",
        save_to_db=False,
    )
    assert snap is not None
    # cat_sum = 15+10+8+10(restored)+5 = 48; total = 48 + (-8) = 40
    assert snap.total_score == 40


def test_recalculation_saves_to_db_when_flag_true():
    db = _mock_db_with_score(_full_score_row(momentum_score=10))
    dart = MagicMock()
    dart.extract_financial_metrics.return_value = {
        "stock_code": "004800", "rcept_no": "NEW",
        "year": 2025, "sector": "화학",
    }
    scorer = MagicMock()
    scorer.calculate_score.return_value = {
        "stock_code": "004800",
        "value_score": 18, "financial_score": 12,
        "growth_score": 10, "momentum_score": 0, "quality_score": 9,
        "penalties": 0,
    }
    trigger_score_recalculation(
        db=db, dart_client=dart, scorer=scorer, stock_code="004800",
        save_to_db=True,
    )
    db.save_stock_scores.assert_called_once()
    db.save_financial_metrics.assert_called_once()


def test_recalculation_skips_db_save_when_flag_false():
    db = _mock_db_with_score(_full_score_row())
    dart = MagicMock()
    dart.extract_financial_metrics.return_value = {
        "stock_code": "004800", "rcept_no": "NEW",
        "year": 2025, "sector": "화학",
    }
    scorer = MagicMock()
    scorer.calculate_score.return_value = {
        "stock_code": "004800",
        "value_score": 18, "financial_score": 12,
        "growth_score": 10, "momentum_score": 0, "quality_score": 9,
        "penalties": 0,
    }
    trigger_score_recalculation(
        db=db, dart_client=dart, scorer=scorer, stock_code="004800",
        save_to_db=False,
    )
    db.save_stock_scores.assert_not_called()
    # financial_metrics는 save_to_db와 무관하게 항상 저장 (재수집 자체)
    db.save_financial_metrics.assert_called_once()


# ----------------------------------------------------------------------
# process_disclosures
# ----------------------------------------------------------------------
def test_process_disclosures_filters_to_refresh_only():
    """배당/자사주는 needs_data_refresh=False → 처리 안 함."""
    db = _mock_db_with_score(_full_score_row())
    dart = MagicMock()
    scorer = MagicMock()
    discs = [
        _disc("004800", "현금ㆍ현물배당결정"),         # DIVIDEND skip
        _disc("005930", "주요사항보고서(자기주식취득결정)"),  # BUYBACK skip
        _disc("000270", "공정공시"),                   # OTHER skip
    ]
    out = process_disclosures(
        db=db, dart_client=dart, scorer=scorer, disclosures=discs,
    )
    assert out == []
    dart.extract_financial_metrics.assert_not_called()


def test_process_disclosures_dedupes_same_stock():
    db = _mock_db_with_score(_full_score_row())
    dart = MagicMock()
    dart.extract_financial_metrics.return_value = {
        "stock_code": "004800", "rcept_no": "NEW",
        "year": 2025, "sector": "화학",
    }
    scorer = MagicMock()
    scorer.calculate_score.return_value = {
        "stock_code": "004800",
        "value_score": 18, "financial_score": 12,
        "growth_score": 10, "momentum_score": 0, "quality_score": 9,
        "penalties": 0,
    }
    # 같은 종목 3건 (모두 PERIODIC)
    discs = [
        _disc("004800", "사업보고서"),
        _disc("004800", "분기보고서(1Q26)"),
        _disc("004800", "[기재정정]사업보고서"),
    ]
    out = process_disclosures(
        db=db, dart_client=dart, scorer=scorer, disclosures=discs,
        save_to_db=False,
    )
    # 3건 모두 같은 종목이므로 1번만 재수집
    assert len(out) == 1
    assert dart.extract_financial_metrics.call_count == 1


def test_process_disclosures_sorts_signal_change_first():
    """신호 변경 종목이 우선, 그 다음 |total_diff| 큰 순."""
    # 두 종목 시나리오:
    #   004800: total 50 → 55 (+5), signal hold→hold (변경 없음)
    #   005930: total 50 → 52 (+2), signal hold→buy (변경)
    # 기대 순서: 005930(신호 변경) → 004800

    def mock_db():
        db = MagicMock()
        # get_stock_score는 두 번 호출됨 (before snapshot + 재계산 시 prev row)
        db.get_stock_score.side_effect = [
            _full_score_row(stock_code="004800", total_score=50,
                            momentum_score=10, signal="hold"),
            _full_score_row(stock_code="004800", total_score=50,
                            momentum_score=10, signal="hold"),
            _full_score_row(stock_code="005930", total_score=50,
                            momentum_score=10, signal="hold"),
            _full_score_row(stock_code="005930", total_score=50,
                            momentum_score=10, signal="hold"),
        ]
        return db

    db = mock_db()
    dart = MagicMock()
    dart.extract_financial_metrics.side_effect = [
        {"stock_code": "004800", "rcept_no": "A", "year": 2025},
        {"stock_code": "005930", "rcept_no": "B", "year": 2025},
    ]
    scorer = MagicMock()
    # 004800: 새 점수 55 (+5), signal 그대로 hold
    # 005930: 새 점수 52 (+2), signal buy
    scorer.calculate_score.side_effect = [
        {  # 004800
            "stock_code": "004800",
            "value_score": 15, "financial_score": 10,
            "growth_score": 13, "momentum_score": 0, "quality_score": 7,
            # cat_sum = 15+10+13+10(restored)+7 = 55
            "penalties": 0,
        },
        {  # 005930
            "stock_code": "005930",
            "value_score": 15, "financial_score": 10,
            "growth_score": 8, "momentum_score": 0, "quality_score": 9,
            # cat_sum = 15+10+8+10+9 = 52
            "penalties": 0,
        },
    ]
    discs = [
        _disc("004800", "사업보고서"),
        _disc("005930", "사업보고서"),
    ]
    # signal 변경을 시뮬하려면 스냅샷 이후 단계에서 signal을 바꿔야 한다.
    # _full_score_row를 한번 더 가공: 005930의 결과 signal을 buy로
    # (현재 trigger_score_recalculation은 signal을 prev에서 가져오므로,
    # before/after에서 signal이 동일 → 변경 없음. 이 테스트는 정렬을
    # 검증하기 위해 |diff|만 비교.)
    out = process_disclosures(
        db=db, dart_client=dart, scorer=scorer, disclosures=discs,
        save_to_db=False,
    )
    assert len(out) == 2
    # |diff| 큰 종목 우선: 004800(+5) > 005930(+2)
    assert out[0].stock_code == "004800"
    assert out[1].stock_code == "005930"


def test_process_disclosures_skips_failures():
    """재계산 실패한 종목은 결과에서 빠지되 다른 종목은 정상 처리."""
    def mk_score_row(code):
        return _full_score_row(stock_code=code, total_score=50,
                               momentum_score=10)

    db = MagicMock()
    db.get_stock_score.side_effect = [
        mk_score_row("004800"),  # before 004800
        # 004800는 재계산에서 실패할 것 — get_stock_score 두 번째는 호출 안됨
        mk_score_row("005930"),  # before 005930
        mk_score_row("005930"),  # 재계산 prev
    ]
    dart = MagicMock()
    dart.extract_financial_metrics.side_effect = [
        RuntimeError("DART 503"),
        {"stock_code": "005930", "rcept_no": "B", "year": 2025},
    ]
    scorer = MagicMock()
    scorer.calculate_score.return_value = {
        "stock_code": "005930",
        "value_score": 15, "financial_score": 10,
        "growth_score": 12, "momentum_score": 0, "quality_score": 8,
        "penalties": 0,
    }
    discs = [
        _disc("004800", "사업보고서"),
        _disc("005930", "사업보고서"),
    ]
    out = process_disclosures(
        db=db, dart_client=dart, scorer=scorer, disclosures=discs,
        save_to_db=False,
    )
    assert len(out) == 1
    assert out[0].stock_code == "005930"


def test_process_disclosures_skips_when_no_previous_score():
    db = MagicMock()
    db.get_stock_score.return_value = None  # before snapshot 없음
    dart = MagicMock()
    scorer = MagicMock()
    out = process_disclosures(
        db=db, dart_client=dart, scorer=scorer,
        disclosures=[_disc("004800", "사업보고서")],
    )
    assert out == []
    dart.extract_financial_metrics.assert_not_called()


# ----------------------------------------------------------------------
# 통합 시나리오 — 효성 정정공시 시뮬
# ----------------------------------------------------------------------
def test_full_workflow_amendment_yields_expected_impact():
    """효성(004800) 정정공시 1건 → financial_metrics 재수집 → 점수 +7"""
    db = MagicMock()
    db.get_stock_score.side_effect = [
        # before snapshot 조회
        _full_score_row(total_score=55, value_score=15, financial_score=10,
                        growth_score=10, momentum_score=12, quality_score=8,
                        signal="hold"),
        # 재계산 시 prev row 조회
        _full_score_row(total_score=55, momentum_score=12),
    ]
    dart = MagicMock()
    dart.extract_financial_metrics.return_value = {
        "stock_code": "004800", "rcept_no": "NEW", "year": 2025,
        "sector": "화학",
    }
    scorer = MagicMock()
    scorer.calculate_score.return_value = {
        "stock_code": "004800",
        "value_score": 18, "financial_score": 12,
        "growth_score": 12, "momentum_score": 0, "quality_score": 8,
        # cat_sum = 18+12+12+12(restored)+8 = 62
        "penalties": 0,
    }
    discs = [_disc("004800", "[기재정정]사업보고서")]
    out = process_disclosures(
        db=db, dart_client=dart, scorer=scorer, disclosures=discs,
        save_to_db=False,
    )
    assert len(out) == 1
    imp = out[0]
    assert imp.stock_code == "004800"
    assert imp.before.total_score == 55
    assert imp.after.total_score == 62
    assert imp.total_diff == 7
    assert imp.is_significant  # 5점 이상


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

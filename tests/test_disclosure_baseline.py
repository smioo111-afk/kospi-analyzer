"""A1 Phase 0.5: init_disclosure_baseline + backfill_recent_disclosures 테스트."""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models import Database  # noqa: E402
from tools.init_disclosure_baseline import (  # noqa: E402
    _extract_rcept_from_parquet,
    init_baseline,
)
from tools.backfill_recent_disclosures import (  # noqa: E402
    backfill_recent,
    is_amendment,
    is_periodic,
)


# ----------------------------------------------------------------------
# 픽스처: 캐시 + DB 빌드
# ----------------------------------------------------------------------
def _make_parquet(cache_dir: Path, code: str, year: int,
                  rcept_no: str, quarter: str = "annual") -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{code}_{year}_{quarter}.parquet"
    df = pd.DataFrame([
        {
            "sj_div": "IS",
            "account_nm": "매출액",
            "thstrm_amount": "1000000",
            "rcept_no": rcept_no,
            "bsns_year": year,
        }
    ])
    df.to_parquet(path)
    return path


@pytest.fixture
def db_with_metrics(tmp_path):
    db_path = tmp_path / "init.db"
    db = Database(db_path=str(db_path))
    try:
        # 분석 종목 3건, 모두 rcept_no='' 상태로 시작
        for code in ("004800", "005930", "000100"):
            db.save_financial_metrics({
                "stock_code": code,
                "year": 2025,
                "quarter": "annual",
                "revenue": 100,
            })
    finally:
        db.close()
    return db_path


# ----------------------------------------------------------------------
# init_disclosure_baseline
# ----------------------------------------------------------------------
def test_init_baseline_dry_run_does_not_modify_db(db_with_metrics, tmp_path):
    cache = tmp_path / "cache"
    _make_parquet(cache, "004800", 2025, "20260312001236")
    stats = init_baseline(
        db_path=str(db_with_metrics), cache_dir=cache, apply=False,
    )
    # dry-run: updated 카운트는 잡히되 DB는 변경 없음
    assert stats["updated"] == 1
    assert stats["applied"] is False

    conn = sqlite3.connect(db_with_metrics)
    row = conn.execute(
        "SELECT rcept_no, rcept_dt FROM financial_metrics "
        "WHERE stock_code='004800'"
    ).fetchone()
    conn.close()
    assert row[0] == ""  # dry-run이라 갱신 안됨
    assert row[1] == ""


def test_init_baseline_apply_updates_db(db_with_metrics, tmp_path):
    cache = tmp_path / "cache"
    _make_parquet(cache, "004800", 2025, "20260312001236")
    _make_parquet(cache, "005930", 2025, "20260311000123")

    stats = init_baseline(
        db_path=str(db_with_metrics), cache_dir=cache, apply=True,
    )
    assert stats["updated"] == 2
    # 000100은 캐시 없음 → skipped_no_cache
    assert stats["skipped_no_cache"] == 1
    assert stats["applied"] is True

    conn = sqlite3.connect(db_with_metrics)
    row1 = conn.execute(
        "SELECT rcept_no, rcept_dt FROM financial_metrics "
        "WHERE stock_code='004800'"
    ).fetchone()
    row2 = conn.execute(
        "SELECT rcept_no, rcept_dt FROM financial_metrics "
        "WHERE stock_code='005930'"
    ).fetchone()
    row3 = conn.execute(
        "SELECT rcept_no, rcept_dt FROM financial_metrics "
        "WHERE stock_code='000100'"
    ).fetchone()
    conn.close()

    assert row1 == ("20260312001236", "20260312")
    assert row2 == ("20260311000123", "20260311")
    assert row3 == ("", "")  # 캐시 없어 그대로


def test_init_baseline_skips_already_set_rows(db_with_metrics, tmp_path):
    """이미 rcept_no가 채워진 행은 target에 포함되지 않는다."""
    # 한 행만 미리 채워둠
    conn = sqlite3.connect(db_with_metrics)
    conn.execute(
        "UPDATE financial_metrics SET rcept_no='OLD', rcept_dt='20260101' "
        "WHERE stock_code='004800'"
    )
    conn.commit()
    conn.close()

    cache = tmp_path / "cache"
    _make_parquet(cache, "004800", 2025, "20260312001236")
    _make_parquet(cache, "005930", 2025, "20260311000123")

    stats = init_baseline(
        db_path=str(db_with_metrics), cache_dir=cache, apply=True,
    )
    # 이미 채워진 004800은 target에서 빠짐 → 2건만 대상
    assert stats["target"] == 2
    assert stats["updated"] == 1  # 005930만

    conn = sqlite3.connect(db_with_metrics)
    row = conn.execute(
        "SELECT rcept_no FROM financial_metrics WHERE stock_code='004800'"
    ).fetchone()
    conn.close()
    assert row[0] == "OLD"  # 기존 값 보존


def test_init_baseline_handles_missing_rcept_in_parquet(db_with_metrics, tmp_path):
    """rcept_no 컬럼이 없는 구 parquet은 skipped_no_rcept로 분류."""
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    # rcept_no 컬럼 없는 parquet
    df = pd.DataFrame([
        {"sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "1"}
    ])
    df.to_parquet(cache / "004800_2025_annual.parquet")

    stats = init_baseline(
        db_path=str(db_with_metrics), cache_dir=cache, apply=True,
    )
    assert stats["skipped_no_rcept"] == 1
    assert stats["updated"] == 0


def test_init_baseline_year_filter(db_with_metrics, tmp_path):
    # 2024년 행 추가
    db = Database(db_path=str(db_with_metrics))
    try:
        db.save_financial_metrics({
            "stock_code": "004800",
            "year": 2024,
            "quarter": "annual",
            "revenue": 50,
        })
    finally:
        db.close()

    cache = tmp_path / "cache"
    _make_parquet(cache, "004800", 2024, "20250320001111")
    _make_parquet(cache, "004800", 2025, "20260312001236")

    # year=2025 필터: 2025만 처리
    stats = init_baseline(
        db_path=str(db_with_metrics), cache_dir=cache,
        year=2025, apply=True,
    )
    assert stats["updated"] == 1
    conn = sqlite3.connect(db_with_metrics)
    r2024 = conn.execute(
        "SELECT rcept_no FROM financial_metrics "
        "WHERE stock_code='004800' AND year=2024"
    ).fetchone()
    r2025 = conn.execute(
        "SELECT rcept_no FROM financial_metrics "
        "WHERE stock_code='004800' AND year=2025"
    ).fetchone()
    conn.close()
    assert r2024[0] == ""  # year 필터로 제외
    assert r2025[0] == "20260312001236"


def test_init_baseline_limit(db_with_metrics, tmp_path):
    cache = tmp_path / "cache"
    _make_parquet(cache, "004800", 2025, "20260312001236")
    _make_parquet(cache, "005930", 2025, "20260311000123")
    _make_parquet(cache, "000100", 2025, "20260310000456")

    stats = init_baseline(
        db_path=str(db_with_metrics), cache_dir=cache,
        apply=True, limit=2,
    )
    # 3개 중 2개만 처리
    assert stats["target"] == 2
    assert stats["updated"] == 2


def test_extract_rcept_from_parquet_handles_missing_file(tmp_path):
    rcept_no, rcept_dt = _extract_rcept_from_parquet(tmp_path / "nope.parquet")
    assert rcept_no == ""
    assert rcept_dt == ""


def test_extract_rcept_strips_invalid_rcept_no(tmp_path):
    """rcept_no에 'nan' 같은 값이 들어있으면 빈 값으로 처리."""
    df = pd.DataFrame([
        {"sj_div": "IS", "account_nm": "x",
         "thstrm_amount": "1", "rcept_no": "nan"}
    ])
    p = tmp_path / "bad.parquet"
    df.to_parquet(p)
    rcept_no, _ = _extract_rcept_from_parquet(p)
    assert rcept_no == ""


# ----------------------------------------------------------------------
# backfill_recent_disclosures
# ----------------------------------------------------------------------
def test_is_amendment_detects_correction_in_name():
    assert is_amendment({"report_nm": "[기재정정]사업보고서", "rm": ""})
    assert is_amendment({"report_nm": "사업보고서", "rm": "정"})
    assert not is_amendment({"report_nm": "사업보고서", "rm": "유"})


def test_is_periodic_detects_known_keywords():
    assert is_periodic({"report_nm": "사업보고서"})
    assert is_periodic({"report_nm": "분기보고서(1Q26)"})
    assert is_periodic({"report_nm": "반기보고서"})
    assert not is_periodic({"report_nm": "주요사항보고서"})
    assert not is_periodic({"report_nm": "공정공시"})


def test_backfill_filters_to_analyzed_codes_only(db_with_metrics):
    """KOSPI 전체 응답 중 분석 종목 코드만 relevant로 분류."""
    sample_items = [
        {"stock_code": "004800", "report_nm": "분기보고서(1Q26)",
         "rcept_no": "20260429001", "rcept_dt": "20260429",
         "corp_name": "효성", "rm": ""},
        {"stock_code": "999999", "report_nm": "분기보고서",
         "rcept_no": "20260429002", "rcept_dt": "20260429",
         "corp_name": "기타", "rm": ""},
        {"stock_code": "005930", "report_nm": "[기재정정]사업보고서",
         "rcept_no": "20260429003", "rcept_dt": "20260429",
         "corp_name": "삼성전자", "rm": "정"},
    ]
    fake_client = MagicMock()
    fake_client.fetch_disclosure_list = MagicMock(return_value=sample_items)

    stats = backfill_recent(
        db_path=str(db_with_metrics), days=30,
        client=fake_client, today=datetime(2026, 4, 29),
    )
    assert stats["total_disclosures"] == 3
    assert stats["relevant_disclosures"] == 2  # 004800 + 005930
    assert stats["amendments"] == 1            # 005930 (정정)
    # 정정 사업보고서는 정정이면서 정기보고서 — 둘 다 카운트 (의도된 중복).
    assert stats["periodics"] == 2             # 004800 분기 + 005930 사업
    # amendment_examples는 분석 종목 중 정정만
    codes = {ex["stock_code"] for ex in stats["amendment_examples"]}
    assert codes == {"005930"}


def test_backfill_dry_run_does_not_write_report(db_with_metrics, tmp_path):
    fake_client = MagicMock()
    fake_client.fetch_disclosure_list = MagicMock(return_value=[])
    report_dir = ROOT / "data" / "disclosure_reports"
    files_before = (
        list(report_dir.glob("*.json")) if report_dir.exists() else []
    )
    backfill_recent(
        db_path=str(db_with_metrics), days=7,
        apply=False, client=fake_client, today=datetime(2026, 4, 29),
    )
    files_after = (
        list(report_dir.glob("*.json")) if report_dir.exists() else []
    )
    # apply=False이므로 새 파일 없음
    assert len(files_after) == len(files_before)


def test_backfill_handles_dart_failure_gracefully(db_with_metrics):
    """DART API 호출이 빈 리스트를 반환해도 stats가 정상 계산."""
    fake_client = MagicMock()
    fake_client.fetch_disclosure_list = MagicMock(return_value=[])
    stats = backfill_recent(
        db_path=str(db_with_metrics), days=30,
        client=fake_client, today=datetime(2026, 4, 29),
    )
    assert stats["total_disclosures"] == 0
    assert stats["relevant_disclosures"] == 0
    assert stats["amendments"] == 0
    assert stats["periodics"] == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

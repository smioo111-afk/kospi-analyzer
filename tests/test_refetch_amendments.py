"""A1 Phase 0.7: refetch_amendments 회귀 테스트."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models import Database  # noqa: E402
from tools.refetch_amendments import (  # noqa: E402
    extract_amendment_codes,
    invalidate_cache,
    refetch_amendments,
    refetch_one,
)


def _write_report(tmp_path: Path, amendments_full: list[dict],
                  amendment_examples: list[dict] | None = None) -> Path:
    """test용 backfill 보고서 작성."""
    p = tmp_path / "backfill_test.json"
    body = {
        "amendments_full": amendments_full,
        "amendment_examples": amendment_examples or amendments_full[:10],
    }
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return p


def _make_parquet(cache_dir: Path, code: str, year: int,
                  rcept_no: str, quarter: str = "annual") -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{code}_{year}_{quarter}.parquet"
    df = pd.DataFrame([
        {"sj_div": "IS", "account_nm": "매출액",
         "thstrm_amount": "1000000",
         "rcept_no": rcept_no, "bsns_year": year}
    ])
    df.to_parquet(path)
    return path


def test_extract_amendment_codes_dedupes(tmp_path):
    rep = _write_report(tmp_path, [
        {"stock_code": "004800"},
        {"stock_code": "005930"},
        {"stock_code": "004800"},  # 중복
        {"stock_code": ""},        # 빈 코드 — 제외
    ])
    codes = extract_amendment_codes(rep)
    assert codes == ["004800", "005930"]


def test_extract_amendment_codes_falls_back_to_examples(tmp_path):
    """amendments_full이 없으면 amendment_examples 사용."""
    p = tmp_path / "old_format.json"
    body = {
        "amendment_examples": [
            {"stock_code": "004800"},
            {"stock_code": "005930"},
        ],
    }
    p.write_text(json.dumps(body), encoding="utf-8")
    codes = extract_amendment_codes(p)
    assert set(codes) == {"004800", "005930"}


def test_invalidate_cache_removes_existing(tmp_path):
    cache = tmp_path / "cache"
    p = _make_parquet(cache, "004800", 2025, "20260312001236")
    assert p.exists()
    invalidated = invalidate_cache("004800", 2025, cache_dir=cache)
    assert invalidated is True
    assert not p.exists()


def test_invalidate_cache_no_op_when_missing(tmp_path):
    invalidated = invalidate_cache("999999", 2025,
                                   cache_dir=tmp_path / "no_dir")
    assert invalidated is False


def test_refetch_dry_run_makes_no_changes(tmp_path):
    rep = _write_report(tmp_path, [
        {"stock_code": "004800"},
        {"stock_code": "005930"},
    ])
    db_path = tmp_path / "db.db"
    db = Database(db_path=str(db_path))
    db.save_financial_metrics({
        "stock_code": "004800", "year": 2025, "quarter": "annual",
        "rcept_no": "OLD",
    })
    db.close()

    cache = tmp_path / "cache"
    p1 = _make_parquet(cache, "004800", 2025, "20260312001236")

    stats = refetch_amendments(
        report_file=rep, year=2025, apply=False,
        db_path=str(db_path), cache_dir=cache,
    )
    assert stats["applied"] is False
    assert stats["target"] == 2
    # dry-run은 캐시도 유지, DB도 유지
    assert p1.exists()
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT rcept_no FROM financial_metrics WHERE stock_code='004800'"
    ).fetchone()
    conn.close()
    assert row[0] == "OLD"


def test_refetch_one_invalidates_and_updates(tmp_path):
    """refetch_one은 캐시를 지우고 DB를 갱신해야 한다."""
    db_path = tmp_path / "one.db"
    db = Database(db_path=str(db_path))
    db.save_financial_metrics({
        "stock_code": "004800", "year": 2025, "quarter": "annual",
        "rcept_no": "OLD_RCEPT",
    })

    cache = tmp_path / "cache"
    cached = _make_parquet(cache, "004800", 2025, "OLD_PARQUET_RCEPT")

    fake_client = MagicMock()
    fake_client.extract_financial_metrics = MagicMock(return_value={
        "stock_code": "004800",
        "year": 2025,
        "rcept_no": "NEW_RCEPT_999",
        "rcept_dt": "20260429",
        "revenue": 5000,
    })

    try:
        result = refetch_one(
            fake_client, db, "004800", 2025, cache_dir=cache,
        )
    finally:
        db.close()

    assert result["status"] == "ok"
    assert result["old_rcept"] == "OLD_RCEPT"
    assert result["new_rcept"] == "NEW_RCEPT_999"
    assert result["rcept_changed"] is True
    assert result["cache_invalidated"] is True
    assert not cached.exists()  # 캐시 파일 삭제됨

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT rcept_no, rcept_dt FROM financial_metrics "
        "WHERE stock_code='004800'"
    ).fetchone()
    conn.close()
    assert row[0] == "NEW_RCEPT_999"
    assert row[1] == "20260429"


def test_refetch_one_handles_no_rcept(tmp_path):
    """DART가 응답하지 않거나 rcept_no가 비어 있으면 status='no_rcept'."""
    db_path = tmp_path / "norcept.db"
    db = Database(db_path=str(db_path))
    db.save_financial_metrics({
        "stock_code": "004800", "year": 2025, "quarter": "annual",
        "rcept_no": "EXISTING",
    })
    cache = tmp_path / "cache"

    fake_client = MagicMock()
    fake_client.extract_financial_metrics = MagicMock(return_value={
        "stock_code": "004800", "year": 2025,
        # rcept_no 없음
    })
    try:
        result = refetch_one(
            fake_client, db, "004800", 2025, cache_dir=cache,
        )
    finally:
        db.close()
    assert result["status"] == "no_rcept"
    # DB는 갱신되지 않아야
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT rcept_no FROM financial_metrics WHERE stock_code='004800'"
    ).fetchone()
    conn.close()
    assert row[0] == "EXISTING"


def test_refetch_one_handles_dart_exception(tmp_path):
    """DART 호출이 예외를 던지면 status='error'로 흡수."""
    db_path = tmp_path / "err.db"
    db = Database(db_path=str(db_path))
    fake_client = MagicMock()
    fake_client.extract_financial_metrics = MagicMock(
        side_effect=RuntimeError("DART down"),
    )
    try:
        result = refetch_one(
            fake_client, db, "004800", 2025, cache_dir=tmp_path / "cache",
        )
    finally:
        db.close()
    assert result["status"] == "error"
    assert "DART down" in result["error"]


def test_refetch_apply_aggregates_results(tmp_path):
    """3종목 중 2개 성공 + 1개 실패 시 통계가 정확."""
    rep = _write_report(tmp_path, [
        {"stock_code": "004800"},
        {"stock_code": "005930"},
        {"stock_code": "000270"},
    ])
    db_path = tmp_path / "agg.db"
    db = Database(db_path=str(db_path))
    for code in ("004800", "005930", "000270"):
        db.save_financial_metrics({
            "stock_code": code, "year": 2025, "quarter": "annual",
            "rcept_no": f"OLD_{code}",
        })
    cache = tmp_path / "cache"
    _make_parquet(cache, "004800", 2025, "OLD")
    _make_parquet(cache, "005930", 2025, "OLD")

    fake_client = MagicMock()

    def fake_extract(code, year=2025):
        if code == "000270":
            raise RuntimeError("not found")
        return {
            "stock_code": code, "year": year,
            "rcept_no": f"NEW_{code}", "rcept_dt": "20260429",
        }
    fake_client.extract_financial_metrics = fake_extract

    stats = refetch_amendments(
        report_file=rep, year=2025, apply=True,
        db_path=str(db_path), cache_dir=cache,
        client=fake_client, db=db,
    )
    db.close()
    assert stats["applied"] is True
    assert stats["target"] == 3
    assert stats["success"] == 2
    assert stats["fail"] == 1
    assert stats["rcept_changed"] == 2

    conn = sqlite3.connect(db_path)
    cur = conn.execute("SELECT stock_code, rcept_no FROM financial_metrics")
    rows = dict(cur.fetchall())
    conn.close()
    assert rows["004800"] == "NEW_004800"
    assert rows["005930"] == "NEW_005930"
    assert rows["000270"] == "OLD_000270"  # 실패는 보존


def test_refetch_limit(tmp_path):
    rep = _write_report(tmp_path, [
        {"stock_code": "004800"},
        {"stock_code": "005930"},
        {"stock_code": "000270"},
    ])
    db_path = tmp_path / "lim.db"
    Database(db_path=str(db_path)).close()
    stats = refetch_amendments(
        report_file=rep, year=2025, apply=False,
        db_path=str(db_path), limit=2,
    )
    assert stats["target"] == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

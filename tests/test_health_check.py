"""monitoring.health_check 회귀 테스트.

임시 sqlite DB에 합성 데이터를 넣고 각 검증 항목이 의도대로 동작하는지
확인한다. 실제 운영 DB는 건드리지 않는다.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.health_check import (  # noqa: E402
    GROWTH_ZERO_RATE_MAX,
    KOSPI_INDEX_MAX,
    KOSPI_INDEX_MIN,
    QUALITY_ZERO_RATE_MAX,
    SCORE_SUM_MISMATCH_RATE_MAX,
    HealthCheck,
    HealthCheckReport,
    run_health_check,
)


# ----------------------------------------------------------------------
# 합성 DB 빌더
# ----------------------------------------------------------------------
def _build_db(tmp_path: Path) -> Path:
    """검사 대상 테이블이 있는 빈 DB를 만든다."""
    path = tmp_path / "health_test.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE analysis_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_date TEXT NOT NULL,
            top_10_json TEXT DEFAULT '[]',
            warnings_json TEXT DEFAULT '[]',
            stats_json TEXT DEFAULT '{}',
            kospi_index REAL DEFAULT 0,
            foreign_net_buy INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE stock_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT DEFAULT '',
            total_score INTEGER DEFAULT 0,
            value_score INTEGER DEFAULT 0,
            financial_score INTEGER DEFAULT 0,
            growth_score INTEGER DEFAULT 0,
            momentum_score INTEGER DEFAULT 0,
            quality_score INTEGER DEFAULT 0,
            signal TEXT DEFAULT ''
        );
        CREATE TABLE financial_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            year INTEGER NOT NULL,
            quarter TEXT DEFAULT 'annual',
            revenue INTEGER DEFAULT 0,
            free_cash_flow INTEGER DEFAULT 0
        );
        CREATE TABLE performance_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            return_1w REAL DEFAULT 0,
            last_updated TEXT DEFAULT ''
        );
        """
    )
    conn.commit()
    conn.close()
    return path


def _seed_healthy(path: Path, date: str = "2026-04-28") -> None:
    """모든 검증을 통과하는 합성 데이터."""
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO analysis_results "
        "(analysis_date, kospi_index, foreign_net_buy) VALUES (?,?,?)",
        (date, 6500.0, 100_000_000),
    )
    # 50종목, 합산 무결, 분포 다양
    rows = []
    for i in range(50):
        if i < 5:
            total, sig = 80, "strong_buy"
            mom, fin, growth = 12, 15, 10
            value, quality = 28, 15
        elif i < 15:
            total, sig = 65, "buy"
            mom, fin, growth = 8, 12, 8
            value, quality = 25, 12
        elif i < 35:
            total, sig = 50, "hold"
            mom, fin, growth = 6, 10, 5
            value, quality = 20, 9
        else:
            total, sig = 30, "sell"
            mom, fin, growth = 2, 6, 2
            value, quality = 15, 5
        # 합 == total로 만든다
        actual_sum = value + fin + growth + mom + quality
        # 강제로 total = actual_sum
        rows.append((
            date, f"{i:06d}", "TST",
            actual_sum, value, fin, growth, mom, quality, sig,
        ))
    conn.executemany(
        "INSERT INTO stock_scores (analysis_date, stock_code, stock_name, "
        "total_score, value_score, financial_score, growth_score, "
        "momentum_score, quality_score, signal) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    # 재무: 결손율 낮게
    fin_rows = []
    for i in range(100):
        rev = 0 if i < 3 else 1_000_000_000
        fcf = 0 if i < 5 else 100_000_000
        fin_rows.append((f"{i:06d}", 2025, "annual", rev, fcf))
    conn.executemany(
        "INSERT INTO financial_metrics (stock_code, year, quarter, "
        "revenue, free_cash_flow) VALUES (?,?,?,?,?)",
        fin_rows,
    )
    # 성과 추적: 최신 갱신, cascade 없음
    perf_rows = []
    for i in range(20):
        perf_rows.append((date, f"{i:06d}", -3.0, date))
    conn.executemany(
        "INSERT INTO performance_tracking (report_date, stock_code, "
        "return_1w, last_updated) VALUES (?,?,?,?)",
        perf_rows,
    )
    conn.commit()
    conn.close()


# ----------------------------------------------------------------------
# 진입점 + 데이터클래스
# ----------------------------------------------------------------------
def test_run_health_check_returns_report(tmp_path):
    db = _build_db(tmp_path)
    _seed_healthy(db)
    rep = run_health_check("2026-04-28", db_path=str(db))
    assert isinstance(rep, HealthCheckReport)
    assert rep.date == "2026-04-28"
    assert rep.overall == "pass"
    # 11개 + T1-4 분리 = 총 12개 (T1-4a, T1-4b)
    assert len(rep.checks) == 12


def test_health_check_pass_on_healthy_data(tmp_path):
    db = _build_db(tmp_path)
    _seed_healthy(db)
    rep = run_health_check("2026-04-28", db_path=str(db))
    assert rep.overall == "pass"
    assert rep.alerts == []


# ----------------------------------------------------------------------
# T1-2 KOSPI 범위
# ----------------------------------------------------------------------
def test_kospi_index_range_check_fails_on_zero(tmp_path):
    db = _build_db(tmp_path)
    _seed_healthy(db)
    # KOSPI=0으로 덮어씀
    conn = sqlite3.connect(db)
    conn.execute("UPDATE analysis_results SET kospi_index=0")
    conn.commit()
    conn.close()
    rep = run_health_check("2026-04-28", db_path=str(db))
    t12 = next(c for c in rep.checks if c.name == "T1-2")
    assert t12.status == "fail"
    assert rep.overall == "fail"


def test_kospi_index_range_check_fails_on_huge(tmp_path):
    db = _build_db(tmp_path)
    _seed_healthy(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE analysis_results SET kospi_index=?",
        (KOSPI_INDEX_MAX + 1,),
    )
    conn.commit()
    conn.close()
    rep = run_health_check("2026-04-28", db_path=str(db))
    t12 = next(c for c in rep.checks if c.name == "T1-2")
    assert t12.status == "fail"


def test_kospi_index_range_check_passes_in_range(tmp_path):
    db = _build_db(tmp_path)
    _seed_healthy(db)
    rep = run_health_check("2026-04-28", db_path=str(db))
    t12 = next(c for c in rep.checks if c.name == "T1-2")
    assert t12.status == "pass"


# ----------------------------------------------------------------------
# T1-4 결손율
# ----------------------------------------------------------------------
def test_growth_loss_rate_warning(tmp_path):
    db = _build_db(tmp_path)
    _seed_healthy(db)
    # T1-4a만 격리 검증: growth=0으로 만들면서 total/signal 정합성 유지.
    # 모든 종목을 hold 구간(50점)으로 통일 후 growth 0으로 설정.
    conn = sqlite3.connect(db)
    conn.execute(
        """UPDATE stock_scores
              SET total_score=50, value_score=20, financial_score=10,
                  growth_score=0, momentum_score=10, quality_score=10,
                  signal='hold'
            WHERE analysis_date='2026-04-28'""",
    )
    conn.commit()
    conn.close()
    rep = run_health_check("2026-04-28", db_path=str(db))
    t14a = next(c for c in rep.checks if c.name == "T1-4a")
    assert t14a.status == "warning"
    # T2-1, T2-2가 통과해야 overall이 warning 그대로 유지됨
    t21 = next(c for c in rep.checks if c.name == "T2-1")
    t22 = next(c for c in rep.checks if c.name == "T2-2")
    assert t21.status == "pass"
    assert t22.status == "pass"


# ----------------------------------------------------------------------
# T2-1 점수 합산
# ----------------------------------------------------------------------
def test_total_score_consistency_check_passes(tmp_path):
    db = _build_db(tmp_path)
    _seed_healthy(db)
    rep = run_health_check("2026-04-28", db_path=str(db))
    t21 = next(c for c in rep.checks if c.name == "T2-1")
    assert t21.status == "pass"


def test_total_score_consistency_check_fails(tmp_path):
    db = _build_db(tmp_path)
    _seed_healthy(db)
    # 모든 행 total_score를 999로 → 100% 불일치
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE stock_scores SET total_score=999 "
        "WHERE analysis_date='2026-04-28'",
    )
    conn.commit()
    conn.close()
    rep = run_health_check("2026-04-28", db_path=str(db))
    t21 = next(c for c in rep.checks if c.name == "T2-1")
    assert t21.status == "fail"
    assert rep.overall == "fail"


# ----------------------------------------------------------------------
# T2-2 신호 임계값
# ----------------------------------------------------------------------
def test_signal_threshold_consistency_passes(tmp_path):
    db = _build_db(tmp_path)
    _seed_healthy(db)
    rep = run_health_check("2026-04-28", db_path=str(db))
    t22 = next(c for c in rep.checks if c.name == "T2-2")
    assert t22.status == "pass"


def test_signal_threshold_low_score_must_be_sell(tmp_path):
    db = _build_db(tmp_path)
    _seed_healthy(db)
    # total=20 (< 45)인데 signal='hold' → 위반
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE stock_scores SET signal='hold', total_score=20 "
        "WHERE stock_code='000045'",
    )
    conn.commit()
    conn.close()
    rep = run_health_check("2026-04-28", db_path=str(db))
    t22 = next(c for c in rep.checks if c.name == "T2-2")
    assert t22.status == "fail"
    assert "000045" in t22.detail


# ----------------------------------------------------------------------
# T1-5 FCF 결손율
# ----------------------------------------------------------------------
def test_fcf_loss_rate_threshold_warns(tmp_path):
    db = _build_db(tmp_path)
    _seed_healthy(db)
    # FCF=0을 100건 중 50건으로 → 50% > 10% 임계
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE financial_metrics SET free_cash_flow=0 "
        "WHERE stock_code IN (SELECT stock_code FROM financial_metrics LIMIT 50)",
    )
    conn.commit()
    conn.close()
    rep = run_health_check("2026-04-28", db_path=str(db))
    t15 = next(c for c in rep.checks if c.name == "T1-5")
    assert t15.status == "warning"


# ----------------------------------------------------------------------
# 알림/포맷
# ----------------------------------------------------------------------
def test_alert_triggers_on_violation(tmp_path):
    db = _build_db(tmp_path)
    _seed_healthy(db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE analysis_results SET kospi_index=0")
    conn.commit()
    conn.close()
    rep = run_health_check("2026-04-28", db_path=str(db))
    assert rep.overall == "fail"
    assert rep.alerts
    assert any("T1-2" in a for a in rep.alerts)


def test_format_text_contains_all_checks(tmp_path):
    db = _build_db(tmp_path)
    _seed_healthy(db)
    rep = run_health_check("2026-04-28", db_path=str(db))
    text = rep.format_text()
    for name in ("T1-1", "T1-2", "T1-3", "T1-4a", "T1-4b", "T1-5",
                 "T1-6", "T1-7", "T1-8", "T2-1", "T2-2", "T2-3"):
        assert name in text


def test_dataclass_round_trip():
    rep = HealthCheckReport(date="2026-04-28")
    rep.add(HealthCheck(name="X", title="t", status="pass"))
    d = rep.to_dict()
    assert d["date"] == "2026-04-28"
    assert d["overall"] == "pass"
    assert d["checks"][0]["name"] == "X"


# ----------------------------------------------------------------------
# 임계값 sanity (regression: 누가 임계값을 실수로 0/100으로 바꾸지 않게)
# ----------------------------------------------------------------------
def test_thresholds_are_sane():
    assert 0 < SCORE_SUM_MISMATCH_RATE_MAX < 1
    assert 0 < GROWTH_ZERO_RATE_MAX < 1
    assert 0 < QUALITY_ZERO_RATE_MAX < 1
    assert KOSPI_INDEX_MIN < KOSPI_INDEX_MAX


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

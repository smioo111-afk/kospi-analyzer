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
            operating_income INTEGER DEFAULT 0,
            net_income INTEGER DEFAULT 0,
            prev_net_income INTEGER DEFAULT 0,
            free_cash_flow INTEGER DEFAULT 0,
            consecutive_loss_years INTEGER DEFAULT 0,
            consecutive_revenue_decline_years INTEGER DEFAULT 0
        );
        CREATE TABLE performance_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            return_1w REAL DEFAULT 0,
            last_updated TEXT DEFAULT ''
        );
        CREATE TABLE disclosure_impacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            rcept_no TEXT,
            impact_json TEXT
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
    # 재무: 결손율 낮게. T2-1 페널티 조건은 모두 0(미발동)으로 두어
    # _seed_healthy 데이터는 카테고리 합 == total_score를 유지한다.
    fin_rows = []
    for i in range(100):
        rev = 0 if i < 3 else 1_000_000_000
        fcf = 0 if i < 5 else 100_000_000
        op_inc = 0 if rev == 0 else 100_000_000
        net_inc = 0 if rev == 0 else 80_000_000
        fin_rows.append((
            f"{i:06d}", 2025, "annual",
            rev, op_inc, net_inc, 50_000_000,
            fcf, 0, 0,
        ))
    conn.executemany(
        "INSERT INTO financial_metrics (stock_code, year, quarter, "
        "revenue, operating_income, net_income, prev_net_income, "
        "free_cash_flow, consecutive_loss_years, "
        "consecutive_revenue_decline_years) VALUES (?,?,?,?,?,?,?,?,?,?)",
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
    # T1-9: disclosure_monitor 실행 흔적 (오늘자 1건). 0건이어도 PASS
    # 하려면 로그가 있어야 하므로 seed 단계에서는 행 1건으로 단순화.
    conn.execute(
        "INSERT INTO disclosure_impacts "
        "(analysis_date, stock_code, rcept_no, impact_json) "
        "VALUES (?, ?, ?, ?)",
        (date, "000001", "20260427001234", "{}"),
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
    # 11개 + T1-4 분리 + T1-2b + T1-9 = 총 14개
    assert len(rep.checks) == 14


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
# T1-2b KOSPI change vs change_rate 정합
# ----------------------------------------------------------------------
def test_t1_2b_skipped_when_stats_missing(tmp_path):
    """stats_json에 change 키가 없으면 skip."""
    db = _build_db(tmp_path)
    _seed_healthy(db)
    rep = run_health_check("2026-04-28", db_path=str(db))
    t12b = next(c for c in rep.checks if c.name == "T1-2b")
    assert t12b.status == "skip"


def test_t1_2b_passes_when_both_consistent(tmp_path):
    db = _build_db(tmp_path)
    _seed_healthy(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE analysis_results SET stats_json=? WHERE analysis_date=?",
        ('{"kospi_change": 12.5, "kospi_change_rate": 0.45}', "2026-04-28"),
    )
    conn.commit()
    conn.close()
    rep = run_health_check("2026-04-28", db_path=str(db))
    t12b = next(c for c in rep.checks if c.name == "T1-2b")
    assert t12b.status == "pass"


def test_t1_2b_passes_when_both_zero(tmp_path):
    """장 마감 무변동 케이스 (둘 다 0)는 pass."""
    db = _build_db(tmp_path)
    _seed_healthy(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE analysis_results SET stats_json=? WHERE analysis_date=?",
        ('{"kospi_change": 0.0, "kospi_change_rate": 0.0}', "2026-04-28"),
    )
    conn.commit()
    conn.close()
    rep = run_health_check("2026-04-28", db_path=str(db))
    t12b = next(c for c in rep.checks if c.name == "T1-2b")
    assert t12b.status == "pass"


def test_t1_2b_detects_zero_rate_with_nonzero_change(tmp_path):
    """N4 silent fail 패턴: change != 0 인데 rate == 0."""
    db = _build_db(tmp_path)
    _seed_healthy(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE analysis_results SET stats_json=? WHERE analysis_date=?",
        ('{"kospi_change": 12.5, "kospi_change_rate": 0.0}', "2026-04-28"),
    )
    conn.commit()
    conn.close()
    rep = run_health_check("2026-04-28", db_path=str(db))
    t12b = next(c for c in rep.checks if c.name == "T1-2b")
    assert t12b.status == "fail"
    assert "한쪽 0" in t12b.detail


def test_t1_2b_detects_zero_change_with_nonzero_rate(tmp_path):
    """반대 방향 silent fail (rate만 채워짐)도 fail."""
    db = _build_db(tmp_path)
    _seed_healthy(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE analysis_results SET stats_json=? WHERE analysis_date=?",
        ('{"kospi_change": 0.0, "kospi_change_rate": 0.45}', "2026-04-28"),
    )
    conn.commit()
    conn.close()
    rep = run_health_check("2026-04-28", db_path=str(db))
    t12b = next(c for c in rep.checks if c.name == "T1-2b")
    assert t12b.status == "fail"


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
    for name in ("T1-1", "T1-2", "T1-2b", "T1-3", "T1-4a", "T1-4b",
                 "T1-5", "T1-6", "T1-7", "T1-8", "T1-9",
                 "T2-1", "T2-2", "T2-3"):
        assert name in text


def test_dataclass_round_trip():
    rep = HealthCheckReport(date="2026-04-28")
    rep.add(HealthCheck(name="X", title="t", status="pass"))
    d = rep.to_dict()
    assert d["date"] == "2026-04-28"
    assert d["overall"] == "pass"
    assert d["checks"][0]["name"] == "X"


# ----------------------------------------------------------------------
# T1-4b 의미 변경: FCF 음수는 정상, 데이터 결손만 카운트
# ----------------------------------------------------------------------
def test_t14b_excludes_genuine_negative_fcf(tmp_path):
    """quality=0 + FCF<0 종목이 많아도 결손 비율은 낮아야 한다."""
    db = _build_db(tmp_path)
    _seed_healthy(db)
    conn = sqlite3.connect(db)
    # 50종목 모두 quality=0으로 만들면서 FCF는 음수로 (정상 시그널).
    # 합산 정합성 유지를 위해 quality 빠진 만큼 다른 카테고리 보정.
    conn.execute(
        """UPDATE stock_scores
              SET quality_score=0,
                  total_score=value_score+financial_score+growth_score
                              +momentum_score
            WHERE analysis_date='2026-04-28'""",
    )
    # 모든 financial_metrics에 매칭되는 행을 두고 FCF만 음수.
    conn.execute(
        "UPDATE financial_metrics SET free_cash_flow=-100000000 "
        "WHERE year=2025",
    )
    conn.commit()
    conn.close()
    rep = run_health_check("2026-04-28", db_path=str(db))
    t14b = next(c for c in rep.checks if c.name == "T1-4b")
    # 전체 q=0이지만 결손 0건 → pass
    assert t14b.status == "pass", f"detail: {t14b.detail}"


def test_t14b_detects_data_loss_only(tmp_path):
    """financial_metrics row가 비어 있는 q=0 종목만 결손으로 카운트."""
    db = _build_db(tmp_path)
    _seed_healthy(db)
    conn = sqlite3.connect(db)
    # 모든 종목 q=0으로 만들고, 30/50종목은 fm row가 비어있는 상태(rev=0,fcf=0)
    # 로 남기되, 나머지 20종목은 fm에 값이 있는(FCF<0) 상태로 둔다.
    conn.execute(
        """UPDATE stock_scores
              SET quality_score=0,
                  total_score=value_score+financial_score+growth_score
                              +momentum_score
            WHERE analysis_date='2026-04-28'""",
    )
    # 50종목 중 30종목의 fm을 결손(rev=0, fcf=0)으로 남기고, 나머지 20종목은
    # 음수 FCF(정상)으로. 30/50 = 60% 결손율 → 임계 10% 초과 → warning
    conn.execute(
        "UPDATE financial_metrics SET revenue=0, free_cash_flow=0, "
        "operating_income=0, net_income=0, prev_net_income=0 "
        "WHERE year=2025 AND CAST(stock_code AS INTEGER) < 30",
    )
    conn.execute(
        "UPDATE financial_metrics SET revenue=1000000000, "
        "free_cash_flow=-100000000 "
        "WHERE year=2025 AND CAST(stock_code AS INTEGER) >= 30",
    )
    conn.commit()
    conn.close()
    rep = run_health_check("2026-04-28", db_path=str(db))
    t14b = next(c for c in rep.checks if c.name == "T1-4b")
    assert t14b.status == "warning", f"detail: {t14b.detail}"
    assert "결손" in t14b.detail


# ----------------------------------------------------------------------
# T2-1 페널티 인지: 의도된 페널티는 위반이 아님
# ----------------------------------------------------------------------
def test_t21_includes_penalty_3yr_revenue_decline(tmp_path):
    """3년 연속 매출 감소 페널티(-5)가 적용된 종목은 위반 아님."""
    db = _build_db(tmp_path)
    _seed_healthy(db)
    conn = sqlite3.connect(db)
    # stock_code=000010: total을 sum-5로 (페널티 인식).
    conn.execute(
        """UPDATE stock_scores
              SET total_score=value_score+financial_score+growth_score
                              +momentum_score+quality_score-5
            WHERE stock_code='000010'""",
    )
    # fm에 페널티 조건 주입 + PL 정상값 (결손 분류 회피).
    conn.execute(
        """UPDATE financial_metrics
              SET consecutive_revenue_decline_years=3,
                  revenue=1000000000,
                  operating_income=100000000,
                  net_income=80000000,
                  prev_net_income=80000000
            WHERE stock_code='000010' AND year=2025""",
    )
    conn.commit()
    conn.close()
    rep = run_health_check("2026-04-28", db_path=str(db))
    t21 = next(c for c in rep.checks if c.name == "T2-1")
    assert t21.status == "pass", f"detail: {t21.detail}"


def test_t21_includes_penalty_profit_to_loss(tmp_path):
    """흑자→적자 페널티(-8)가 적용된 종목은 위반 아님."""
    db = _build_db(tmp_path)
    _seed_healthy(db)
    conn = sqlite3.connect(db)
    conn.execute(
        """UPDATE stock_scores
              SET total_score=value_score+financial_score+growth_score
                              +momentum_score+quality_score-8
            WHERE stock_code='000020'""",
    )
    conn.execute(
        """UPDATE financial_metrics
              SET prev_net_income=100000000,
                  net_income=-50000000,
                  revenue=1000000000,
                  operating_income=10000000
            WHERE stock_code='000020' AND year=2025""",
    )
    conn.commit()
    conn.close()
    rep = run_health_check("2026-04-28", db_path=str(db))
    t21 = next(c for c in rep.checks if c.name == "T2-1")
    assert t21.status == "pass", f"detail: {t21.detail}"


# ----------------------------------------------------------------------
# T1-9 disclosure_monitor 실행 검증
# ----------------------------------------------------------------------
def test_t1_9_passes_when_monitor_ran(tmp_path):
    """오늘자 disclosure_impacts 행이 있으면 PASS."""
    db = _build_db(tmp_path)
    _seed_healthy(db)
    rep = run_health_check("2026-04-28", db_path=str(db))
    t19 = next(c for c in rep.checks if c.name == "T1-9")
    assert t19.status == "pass"


def test_t1_9_warning_when_today_empty_but_recent_history(tmp_path):
    """오늘자 0건 + 로그 없음이지만 최근 7일 중 다른 날 행 있음 → warning."""
    db = _build_db(tmp_path)
    _seed_healthy(db)
    # _seed_healthy가 today=2026-04-28에 1건 넣음 → 다른 날 검사일로 호출
    # 검사일 2026-04-30에는 행 없음, 2026-04-28에 1건이 7일 윈도 안.
    rep = run_health_check("2026-04-30", db_path=str(db))
    t19 = next(c for c in rep.checks if c.name == "T1-9")
    assert t19.status == "warning"


def test_t1_9_fail_when_7_days_empty(tmp_path):
    """7일 연속 0건 + 로그 없음 → fail (영구 결함)."""
    db = _build_db(tmp_path)
    _seed_healthy(db)
    # 모든 disclosure_impacts 행 제거
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM disclosure_impacts")
    conn.commit()
    conn.close()
    rep = run_health_check("2026-04-28", db_path=str(db))
    t19 = next(c for c in rep.checks if c.name == "T1-9")
    assert t19.status == "fail"


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

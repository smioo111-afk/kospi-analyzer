"""분석 사이클 자가 진단 (Tier 1 + Tier 2).

매 분석 사이클 종료 후 자동 실행되어 silent fail을 즉시 노출한다.
검증 결과는 logs/health_check.log에 매일 기록되며, 위반 발생 시
텔레그램 ERROR 채널로 알림을 발송한다.

검증 항목 (총 11건):
  Tier 1 (데이터 무결성, 8건)
    T1-1  analysis_results 행 존재
    T1-2  kospi_index 합리적 범위 (3000 ~ 10000)
    T1-3  foreign_net_buy 합리적 범위 (절대값 10조 미만)
    T1-4  stock_scores growth=0 비율 < 30% / quality=0 데이터 결손율 < 10%
    T1-5  financial_metrics FCF 결손율 < 10%
    T1-6  financial_metrics revenue 결손율 < 5%
    T1-7  performance_tracking 갱신 (last_updated >= 분석일)
    T1-8  cascade 재발 감지 (return_1w == -100% 최근 5건+)

  Tier 2 (로직 정합성, 3건)
    T2-1  total_score == 5 카테고리 합 + 페널티 (불일치 5% 미만)
    T2-2  신호 vs 점수 임계값 일치
    T2-3  cascade circuit-breaker 발동 (오늘 로그)

T1-4b / T2-1 의미 보정 (2026-04-29):
  T1-4b는 quality_score=0 전체 비율을 보던 것을, "데이터 결손" 종목
  비율로 좁혔다. scorer.py의 quality 산식이 FCF<=0을 0점 처리하기 때문에
  FCF 음수 자체는 정상 신호이며, 결손은 financial_metrics row가 없거나
  핵심 필드(revenue, free_cash_flow)가 모두 0인 경우만 포함한다.
  T2-1은 scorer.py:145-147의 페널티(`raw_total + penalties`)를 인지하지
  못해 의도된 차이를 위반으로 오인했던 것을, financial_metrics 페널티
  조건을 재계산하여 expected_total과 비교하도록 수정했다.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from config.settings import DBConfig, LogConfig, ScoringConfig, SignalConfig

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 임계값
# ----------------------------------------------------------------------
KOSPI_INDEX_MIN = 3000.0
KOSPI_INDEX_MAX = 10000.0
FOREIGN_NET_BUY_MAX_ABS = 10_000_000_000_000  # 10조 (단위: 원)

GROWTH_ZERO_RATE_MAX = 0.30   # 30%
# T1-4b 임계: quality=0 종목 중 "데이터 결손"만 카운트.
# FCF<0(진짜 음수)은 산식상 정상이므로 제외하고, financial_metrics row가
# 없거나 revenue=0 AND free_cash_flow=0인 경우만 결손으로 본다.
QUALITY_ZERO_RATE_MAX = 0.10  # 10%
FCF_ZERO_RATE_MAX = 0.10      # 10%
REVENUE_ZERO_RATE_MAX = 0.05  # 5%

SCORE_SUM_MISMATCH_RATE_MAX = 0.05  # 5%
CASCADE_RECENT_THRESHOLD = 5
CASCADE_LOOKBACK_DAYS = 7


# ----------------------------------------------------------------------
# 결과 스키마
# ----------------------------------------------------------------------
@dataclass
class HealthCheck:
    """단일 검증 항목 결과."""

    name: str           # T1-1
    title: str          # 사람이 읽는 제목
    status: str         # pass | warning | fail | skip
    detail: str = ""    # 측정값/결과 요약
    threshold: str = ""  # 임계 기준 (참고용)


@dataclass
class HealthCheckReport:
    """전체 검증 리포트."""

    date: str
    overall: str = "pass"   # pass | warning | fail
    checks: list[HealthCheck] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def add(self, check: HealthCheck) -> None:
        self.checks.append(check)
        if check.status == "fail":
            self.overall = "fail"
            self.alerts.append(f"{check.name}: {check.detail}")
        elif check.status == "warning" and self.overall == "pass":
            self.overall = "warning"
            self.alerts.append(f"{check.name}: {check.detail}")

    def format_text(self) -> str:
        """텔레그램 알림 또는 로그용 요약 문자열."""
        emoji = {"pass": "✅", "warning": "⚠️", "fail": "🚨"}.get(self.overall, "?")
        lines = [
            f"{emoji} health check {self.date} - {self.overall.upper()}",
            "",
        ]
        for c in self.checks:
            mark = {"pass": "✓", "warning": "⚠", "fail": "✗", "skip": "-"}.get(
                c.status, "?"
            )
            lines.append(f"{mark} {c.name} {c.title}: {c.detail or 'OK'}")
        if self.alerts:
            lines.append("")
            lines.append("ALERT:")
            for a in self.alerts:
                lines.append(f"  - {a}")
        return "\n".join(lines)


# ----------------------------------------------------------------------
# 검증 로직
# ----------------------------------------------------------------------
def _open_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or DBConfig.DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _check_analysis_results(conn: sqlite3.Connection, date: str) -> HealthCheck:
    """T1-1: 해당일 analysis_results 행 존재."""
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM analysis_results WHERE analysis_date=?",
        (date,),
    ).fetchone()
    cnt = row["c"]
    if cnt == 0:
        return HealthCheck(
            name="T1-1",
            title="analysis_results 행 존재",
            status="fail",
            detail=f"{date} 분석 결과 0건",
            threshold="cnt >= 1",
        )
    return HealthCheck(
        name="T1-1",
        title="analysis_results 행 존재",
        status="pass",
        detail=f"{cnt}건",
    )


def _check_kospi_index(conn: sqlite3.Connection, date: str) -> HealthCheck:
    """T1-2: kospi_index가 합리적 범위 (3000 ~ 10000)."""
    row = conn.execute(
        "SELECT kospi_index FROM analysis_results "
        "WHERE analysis_date=? ORDER BY id DESC LIMIT 1",
        (date,),
    ).fetchone()
    if row is None:
        return HealthCheck(
            name="T1-2",
            title="kospi_index 범위",
            status="skip",
            detail="해당일 분석 결과 없음",
        )
    idx = float(row["kospi_index"] or 0)
    threshold = f"{KOSPI_INDEX_MIN:.0f} ~ {KOSPI_INDEX_MAX:.0f}"
    if not (KOSPI_INDEX_MIN <= idx <= KOSPI_INDEX_MAX):
        return HealthCheck(
            name="T1-2",
            title="kospi_index 범위",
            status="fail",
            detail=f"{idx:.2f} (범위 이탈)",
            threshold=threshold,
        )
    return HealthCheck(
        name="T1-2",
        title="kospi_index 범위",
        status="pass",
        detail=f"{idx:.2f}",
        threshold=threshold,
    )


def _check_foreign_net_buy(conn: sqlite3.Connection, date: str) -> HealthCheck:
    """T1-3: foreign_net_buy 합리적 범위."""
    row = conn.execute(
        "SELECT foreign_net_buy FROM analysis_results "
        "WHERE analysis_date=? ORDER BY id DESC LIMIT 1",
        (date,),
    ).fetchone()
    if row is None:
        return HealthCheck(
            name="T1-3",
            title="foreign_net_buy 범위",
            status="skip",
            detail="해당일 분석 결과 없음",
        )
    val = int(row["foreign_net_buy"] or 0)
    threshold = f"|x| < {FOREIGN_NET_BUY_MAX_ABS:,}"
    if abs(val) >= FOREIGN_NET_BUY_MAX_ABS:
        return HealthCheck(
            name="T1-3",
            title="foreign_net_buy 범위",
            status="fail",
            detail=f"{val:,} (10조 이상)",
            threshold=threshold,
        )
    return HealthCheck(
        name="T1-3",
        title="foreign_net_buy 범위",
        status="pass",
        detail=f"{val:,}",
        threshold=threshold,
    )


def _check_score_loss_rates(conn: sqlite3.Connection, date: str) -> list[HealthCheck]:
    """T1-4: stock_scores growth=0 비율 + quality=0 데이터 결손율.

    T1-4b는 quality_score=0 전체가 아니라, 그 원인이 "데이터 결손"인
    종목만 카운트한다. scorer.py의 quality 산식은 FCF<=0을 0점으로
    처리하므로, FCF 음수(진짜 적자)는 산식상 정상 결과이며 알람이
    아니다. financial_metrics row가 없거나 revenue=0 AND free_cash_flow=0
    인 경우만 결손으로 분류한다.
    """
    year = datetime.strptime(date, "%Y-%m-%d").year - 1
    row = conn.execute(
        """SELECT COUNT(*) AS cnt,
                  SUM(CASE WHEN s.growth_score=0 THEN 1 ELSE 0 END) AS g0,
                  SUM(CASE WHEN s.quality_score=0 THEN 1 ELSE 0 END) AS q0_total,
                  SUM(CASE WHEN s.quality_score=0
                            AND (fm.stock_code IS NULL
                                 OR (COALESCE(fm.revenue, 0) = 0
                                     AND COALESCE(fm.free_cash_flow, 0) = 0))
                           THEN 1 ELSE 0 END) AS q0_dataloss
             FROM stock_scores s
             LEFT JOIN financial_metrics fm
               ON s.stock_code = fm.stock_code
              AND fm.year = ? AND fm.quarter = 'annual'
            WHERE s.analysis_date = ?""",
        (year, date),
    ).fetchone()
    cnt = row["cnt"] or 0
    if cnt == 0:
        skip = HealthCheck(
            name="T1-4",
            title="growth/quality 결손율",
            status="skip",
            detail="해당일 stock_scores 0건",
        )
        return [skip]

    g_rate = (row["g0"] or 0) / cnt
    q_total = row["q0_total"] or 0
    q_dataloss = row["q0_dataloss"] or 0
    q_rate = q_dataloss / cnt

    g = HealthCheck(
        name="T1-4a",
        title="growth_score=0 비율",
        status="pass" if g_rate < GROWTH_ZERO_RATE_MAX else "warning",
        detail=f"{row['g0']}/{cnt} ({g_rate * 100:.1f}%)",
        threshold=f"< {GROWTH_ZERO_RATE_MAX * 100:.0f}%",
    )
    if g.status == "warning":
        g.detail += " (임계 초과)"

    q_detail = (
        f"{q_dataloss}/{cnt} ({q_rate * 100:.1f}%) 결손"
        f" [전체 q=0: {q_total}건, FCF 음수 등 정상 분류 제외]"
    )
    q = HealthCheck(
        name="T1-4b",
        title="quality_score=0 데이터 결손율",
        status="pass" if q_rate < QUALITY_ZERO_RATE_MAX else "warning",
        detail=q_detail,
        threshold=f"< {QUALITY_ZERO_RATE_MAX * 100:.0f}%",
    )
    if q.status == "warning":
        q.detail += " (임계 초과)"

    return [g, q]


def _check_fcf_loss(conn: sqlite3.Connection, date: str) -> HealthCheck:
    """T1-5: financial_metrics FCF 결손율 (가장 최근 연도)."""
    year = datetime.strptime(date, "%Y-%m-%d").year - 1
    row = conn.execute(
        """SELECT COUNT(*) AS cnt,
                  SUM(CASE WHEN free_cash_flow=0 THEN 1 ELSE 0 END) AS z
             FROM financial_metrics WHERE year=?""",
        (year,),
    ).fetchone()
    cnt = row["cnt"] or 0
    if cnt == 0:
        return HealthCheck(
            name="T1-5",
            title="FCF 결손율",
            status="skip",
            detail=f"year={year} 재무 데이터 0건",
        )
    rate = (row["z"] or 0) / cnt
    return HealthCheck(
        name="T1-5",
        title="FCF 결손율",
        status="pass" if rate < FCF_ZERO_RATE_MAX else "warning",
        detail=f"{row['z']}/{cnt} ({rate * 100:.1f}%, year={year})",
        threshold=f"< {FCF_ZERO_RATE_MAX * 100:.0f}%",
    )


def _check_revenue_loss(conn: sqlite3.Connection, date: str) -> HealthCheck:
    """T1-6: financial_metrics revenue 결손율 (가장 최근 연도)."""
    year = datetime.strptime(date, "%Y-%m-%d").year - 1
    row = conn.execute(
        """SELECT COUNT(*) AS cnt,
                  SUM(CASE WHEN revenue=0 THEN 1 ELSE 0 END) AS z
             FROM financial_metrics WHERE year=?""",
        (year,),
    ).fetchone()
    cnt = row["cnt"] or 0
    if cnt == 0:
        return HealthCheck(
            name="T1-6",
            title="revenue 결손율",
            status="skip",
            detail=f"year={year} 재무 데이터 0건",
        )
    rate = (row["z"] or 0) / cnt
    return HealthCheck(
        name="T1-6",
        title="revenue 결손율",
        status="pass" if rate < REVENUE_ZERO_RATE_MAX else "warning",
        detail=f"{row['z']}/{cnt} ({rate * 100:.1f}%, year={year})",
        threshold=f"< {REVENUE_ZERO_RATE_MAX * 100:.0f}%",
    )


def _check_perf_tracking(conn: sqlite3.Connection, date: str) -> HealthCheck:
    """T1-7: performance_tracking last_updated >= 분석일."""
    row = conn.execute(
        "SELECT MAX(last_updated) AS m, COUNT(*) AS c FROM performance_tracking",
    ).fetchone()
    if row is None or (row["c"] or 0) == 0:
        return HealthCheck(
            name="T1-7",
            title="perf_tracking 갱신",
            status="skip",
            detail="performance_tracking 0건",
        )
    last = row["m"] or ""
    if last < date:
        return HealthCheck(
            name="T1-7",
            title="perf_tracking 갱신",
            status="warning",
            detail=f"last_updated={last} < {date}",
            threshold=f">= {date}",
        )
    return HealthCheck(
        name="T1-7",
        title="perf_tracking 갱신",
        status="pass",
        detail=f"last_updated={last}, rows={row['c']}",
    )


def _check_cascade_recent(conn: sqlite3.Connection) -> HealthCheck:
    """T1-8: 최근 N일 return_1w == -100% (cascade 재발) 카운트."""
    row = conn.execute(
        f"""SELECT COUNT(*) AS c FROM performance_tracking
              WHERE return_1w = -100
                AND last_updated >= date('now', '-{CASCADE_LOOKBACK_DAYS} days')""",
    ).fetchone()
    cnt = row["c"] or 0
    threshold = f"< {CASCADE_RECENT_THRESHOLD}건 / {CASCADE_LOOKBACK_DAYS}일"
    if cnt >= CASCADE_RECENT_THRESHOLD:
        return HealthCheck(
            name="T1-8",
            title="cascade 재발",
            status="fail",
            detail=f"{cnt}건 (return_1w=-100%, 최근 {CASCADE_LOOKBACK_DAYS}일)",
            threshold=threshold,
        )
    return HealthCheck(
        name="T1-8",
        title="cascade 재발",
        status="pass",
        detail=f"{cnt}건",
        threshold=threshold,
    )


def _check_score_sum_consistency(
    conn: sqlite3.Connection, date: str
) -> HealthCheck:
    """T2-1: total_score == sum(카테고리) + 페널티 일치 비율.

    scorer.py:145-147은 5개 카테고리 합(raw_total)에 ScoringConfig.
    TOTAL_SCORE_PENALTIES(흑자→적자 -8, 3년연속매출감소 -5, 3년연속적자
    -15)를 더한 뒤 [0,100]으로 클램프하여 total_score를 산출한다.
    여기서는 financial_metrics에서 동일 조건을 재계산해 expected를
    얻고, total_score와 비교한다. PL 핵심 필드(revenue, op_income,
    net_income)가 모두 0이면 결손으로 간주하여 페널티 없이 검사한다.
    """
    year = datetime.strptime(date, "%Y-%m-%d").year - 1
    rows = conn.execute(
        """SELECT s.stock_code, s.total_score,
                  s.value_score, s.financial_score, s.growth_score,
                  s.momentum_score, s.quality_score,
                  COALESCE(fm.revenue, 0) AS revenue,
                  COALESCE(fm.operating_income, 0) AS op_income,
                  COALESCE(fm.net_income, 0) AS net_income,
                  COALESCE(fm.prev_net_income, 0) AS prev_net_income,
                  COALESCE(fm.consecutive_revenue_decline_years, 0) AS rev_dec,
                  COALESCE(fm.consecutive_loss_years, 0) AS loss_yrs
             FROM stock_scores s
             LEFT JOIN financial_metrics fm
               ON s.stock_code = fm.stock_code
              AND fm.year = ? AND fm.quarter = 'annual'
            WHERE s.analysis_date = ?""",
        (year, date),
    ).fetchall()
    cnt = len(rows)
    if cnt == 0:
        return HealthCheck(
            name="T2-1",
            title="total_score == 카테고리 합 + 페널티",
            status="skip",
            detail="해당일 stock_scores 0건",
        )

    pen_cfg = ScoringConfig.TOTAL_SCORE_PENALTIES
    bad = 0
    examples: list[str] = []
    for r in rows:
        cat_sum = (
            (r["value_score"] or 0)
            + (r["financial_score"] or 0)
            + (r["growth_score"] or 0)
            + (r["momentum_score"] or 0)
            + (r["quality_score"] or 0)
        )
        is_pl_missing = (
            r["revenue"] == 0 and r["op_income"] == 0 and r["net_income"] == 0
        )
        penalty = 0
        if not is_pl_missing:
            if r["rev_dec"] >= 3:
                penalty += pen_cfg.get("3yr_revenue_decline", -5)
            if r["prev_net_income"] > 0 and r["net_income"] < 0:
                penalty += pen_cfg.get("profit_to_loss", -8)
            if r["loss_yrs"] >= 3:
                penalty += pen_cfg.get("3yr_consecutive_loss", -15)
        expected = max(0, min(100, cat_sum + penalty))
        if (r["total_score"] or 0) != expected:
            bad += 1
            if len(examples) < 3:
                examples.append(
                    f"{r['stock_code']} t={r['total_score']} sum={cat_sum} "
                    f"pen={penalty} exp={expected}"
                )

    rate = bad / cnt
    threshold = f"< {SCORE_SUM_MISMATCH_RATE_MAX * 100:.0f}%"
    detail_base = f"{bad}/{cnt} ({rate * 100:.1f}%) 불일치"
    if examples:
        detail_base += " | " + "; ".join(examples)
    if rate >= SCORE_SUM_MISMATCH_RATE_MAX:
        return HealthCheck(
            name="T2-1",
            title="total_score == 카테고리 합 + 페널티",
            status="fail",
            detail=detail_base,
            threshold=threshold,
        )
    return HealthCheck(
        name="T2-1",
        title="total_score == 카테고리 합 + 페널티",
        status="pass",
        detail=detail_base,
        threshold=threshold,
    )


def _check_signal_threshold(conn: sqlite3.Connection, date: str) -> HealthCheck:
    """T2-2: 신호 vs 점수 임계값 일치 (확정 위반만 검출).

    `analysis/signals.py::_judge` 의 우선순위 체인을 그대로 따르되,
    stoploss 미상이므로 stoploss 무관 조건만 위반으로 판정한다.
    """
    cfg = SignalConfig
    rows = conn.execute(
        """SELECT stock_code, signal, total_score, momentum_score,
                  financial_score, growth_score
             FROM stock_scores WHERE analysis_date=?""",
        (date,),
    ).fetchall()
    if not rows:
        return HealthCheck(
            name="T2-2",
            title="신호 임계값 일치",
            status="skip",
            detail="해당일 stock_scores 0건",
        )

    violations: list[str] = []
    for r in rows:
        total = int(r["total_score"] or 0)
        mom = int(r["momentum_score"] or 0)
        fin = int(r["financial_score"] or 0)
        growth = int(r["growth_score"] or 0)
        sig = (r["signal"] or "").lower()

        # SELL 강제 조건: total < SELL_SCORE → SELL 이어야 함.
        # stoploss 도달 시도 SELL이지만, total < SELL_SCORE 이면 어쨌든 SELL.
        if total < cfg.SELL_SCORE and sig != "sell":
            violations.append(
                f"{r['stock_code']} total={total}<{cfg.SELL_SCORE} → "
                f"SELL여야 하나 {sig}"
            )
            continue

        # STRONG_BUY 강제 조건:
        # total>=75 + mom>=10 + fin>=12 + growth>=10 + (stoploss 무관)
        # → strong_buy 이어야 함.
        # stoploss 도달 시 SELL로 덮어씌워질 수 있어 sell도 허용.
        if (
            total >= cfg.STRONG_BUY_SCORE
            and mom >= cfg.STRONG_BUY_MOMENTUM
            and fin >= cfg.STRONG_BUY_FINANCIAL
            and growth >= getattr(cfg, "STRONG_BUY_GROWTH", 0)
        ):
            if sig not in ("strong_buy", "sell"):
                violations.append(
                    f"{r['stock_code']} total={total}/m={mom}/f={fin}/"
                    f"g={growth} → STRONG_BUY여야 하나 {sig}"
                )
                continue

        # HOLD 구간 (45~59): SELL이 아닌 한 HOLD여야 함 (mom<4 시 SELL 허용).
        if cfg.HOLD_SCORE_MIN <= total <= cfg.HOLD_SCORE_MAX:
            if mom >= cfg.SELL_MOMENTUM_MIN and sig not in ("hold", "sell"):
                violations.append(
                    f"{r['stock_code']} total={total} → HOLD여야 하나 {sig}"
                )

    threshold = "0건"
    if violations:
        detail = f"{len(violations)}건 위반: " + "; ".join(violations[:3])
        if len(violations) > 3:
            detail += f" ... ({len(violations) - 3}건 추가)"
        return HealthCheck(
            name="T2-2",
            title="신호 임계값 일치",
            status="fail",
            detail=detail,
            threshold=threshold,
        )
    return HealthCheck(
        name="T2-2",
        title="신호 임계값 일치",
        status="pass",
        detail=f"검사 {len(rows)}건",
        threshold=threshold,
    )


_LOG_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _check_circuit_breaker(date: str) -> HealthCheck:
    """T2-3: 오늘 로그에 cascade circuit-breaker 발동 발견 여부."""
    log_path = Path(LogConfig.LOG_DIR) / "kospi_analyzer.log"
    if not log_path.exists():
        return HealthCheck(
            name="T2-3",
            title="circuit-breaker 발동",
            status="skip",
            detail=f"{log_path} 없음",
        )

    fired = 0
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = _LOG_DATE_RE.match(line)
                if not m or m.group(1) != date:
                    continue
                if "cascade circuit-breaker 발동" in line:
                    fired += 1
    except Exception as e:
        return HealthCheck(
            name="T2-3",
            title="circuit-breaker 발동",
            status="skip",
            detail=f"로그 읽기 실패: {e}",
        )

    if fired > 0:
        return HealthCheck(
            name="T2-3",
            title="circuit-breaker 발동",
            status="warning",
            detail=f"{date} {fired}회 발동",
            threshold="0회",
        )
    return HealthCheck(
        name="T2-3",
        title="circuit-breaker 발동",
        status="pass",
        detail="0회",
    )


# ----------------------------------------------------------------------
# 진입점
# ----------------------------------------------------------------------
def run_health_check(
    date: Optional[str] = None,
    db_path: Optional[str] = None,
) -> HealthCheckReport:
    """모든 검증을 실행하고 리포트를 반환한다.

    Args:
        date: 분석일 (YYYY-MM-DD). None이면 오늘.
        db_path: DB 경로. None이면 DBConfig 기본값.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    report = HealthCheckReport(date=date)

    conn = _open_conn(db_path)
    try:
        report.add(_check_analysis_results(conn, date))
        report.add(_check_kospi_index(conn, date))
        report.add(_check_foreign_net_buy(conn, date))
        for c in _check_score_loss_rates(conn, date):
            report.add(c)
        report.add(_check_fcf_loss(conn, date))
        report.add(_check_revenue_loss(conn, date))
        report.add(_check_perf_tracking(conn, date))
        report.add(_check_cascade_recent(conn))
        report.add(_check_score_sum_consistency(conn, date))
        report.add(_check_signal_threshold(conn, date))
    finally:
        conn.close()

    report.add(_check_circuit_breaker(date))

    _write_log(report)
    return report


def _write_log(report: HealthCheckReport) -> None:
    """logs/health_check.log에 결과를 추가한다."""
    log_dir = Path(LogConfig.LOG_DIR)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "health_check.log"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n===== {ts} health_check date={report.date} =====\n")
            f.write(report.format_text())
            f.write("\n")
    except Exception as e:
        logger.warning("health_check.log 기록 실패: %s", e)


# ----------------------------------------------------------------------
# 텔레그램 발송 + 스케줄러 진입점
# ----------------------------------------------------------------------
async def scheduled_health_check() -> None:
    """스케줄러용 진입점. 위반(warning/fail) 시 텔레그램 발송."""
    from bot.telegram_bot import KOSPIBot
    from database.models import Database

    report = run_health_check()
    logger.info(
        "health_check %s overall=%s alerts=%d",
        report.date, report.overall, len(report.alerts),
    )

    if report.overall == "pass":
        return

    db = Database()
    try:
        bot = KOSPIBot(db)
        try:
            await bot.send_health_alert(report)
        except Exception as e:
            logger.error("health_check 알림 발송 실패: %s", e)
    finally:
        db.close()

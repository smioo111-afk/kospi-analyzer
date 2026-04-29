"""
KOSPI 저평가 기업 분석 시스템 - 데이터베이스 모델

SQLite 데이터베이스의 테이블 스키마 및 CRUD 연산을 정의한다:
  - analysis_results: 일별 분석 결과 (TOP 10 + 전 종목 스코어)
  - stock_scores: 종목별 스코어 상세
  - watchlist: 개인 관심종목
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from config.settings import DBConfig

logger = logging.getLogger(__name__)


# ================================================================
# cascade 안전장치 상수
# ================================================================
# consecutive_fetch_failures >= 3이어도 아래 조건이면 cascade를 건너뛴다.
# - 마지막 예외 타입이 환경/런타임 문제 (실제 상장폐지 신호 아님)
# - 시총이 임계값 이상 (대형주는 수동 검증 요구)
#
# 2026-04-24 사건에서 async 리팩터링 후 _run_sync가 RuntimeError를 던져
# 삼성전자 등 대형주 80건이 오판정된 사례를 재발 방지하기 위함.
_CASCADE_SKIP_EXCEPTION_NAMES: frozenset[str] = frozenset({
    "RuntimeError",
    "CancelledError",
    "ValueError",
    "TypeError",
})
_LARGE_CAP_THRESHOLD_KRW: int = 500_000_000_000  # 5000억원

# 사이클 단위 cascade 발화 상한. 초과 시 잔여 cascade 차단 + ERROR 로그.
# 4-22~24 80건 폭주를 다층 방어하기 위한 추가 안전장치.
_CASCADE_PER_CYCLE_LIMIT: int = 5


class Database:
    """SQLite 데이터베이스 관리 클래스.

    연결 풀링, 테이블 생성, 기본 CRUD를 제공한다.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or DBConfig.DB_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        # 가장 최근 update_performance_tracking 호출에서 발생한 cascade
        # skip 이벤트. main.py가 사이클 종료 시 텔레그램 WARN으로 발송한다.
        # 호출 시작 시점에 빈 리스트로 리셋된다.
        self.last_cascade_skip_events: list[dict[str, Any]] = []
        self._init_tables()

    def _get_conn(self) -> sqlite3.Connection:
        """DB 연결을 반환한다 (없으면 생성).

        check_same_thread=False: main.py가 update_performance_tracking을
        asyncio.to_thread로 워커 스레드에서 돌리므로 스레드 경계를 허용한다.
        동시 쓰기 경합은 없도록 상위에서 직렬화된다 (WAL + 사이클당 1회).
        """
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path, check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            # WAL 누적 방지 — 1000 page(약 4MB)마다 자동 체크포인트.
            # 분석 사이클 끝에서 명시적 PASSIVE도 한 번 추가 호출됨
            # (main.py 파이프라인 종료부).
            self._conn.execute("PRAGMA wal_autocheckpoint=1000")
        return self._conn

    def checkpoint_wal(self, mode: str = "PASSIVE") -> None:
        """수동 WAL 체크포인트. 분석 사이클 종료 시 호출 권장.

        - PASSIVE: 다른 reader/writer 차단 안 함 (안전, 부분 체크포인트 가능).
        - TRUNCATE: WAL 파일 비움 (백업 직전용, 다른 연결 있으면 실패 가능).
        """
        try:
            self._get_conn().execute(f"PRAGMA wal_checkpoint({mode})")
        except sqlite3.Error as e:
            logger.warning("WAL 체크포인트 실패 (%s): %s", mode, e)

    def _init_tables(self) -> None:
        """테이블을 생성한다 (없으면)."""
        conn = self._get_conn()

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS analysis_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_date TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                top_10_json TEXT NOT NULL,
                warnings_json TEXT DEFAULT '[]',
                stats_json TEXT DEFAULT '{}',
                kospi_index REAL DEFAULT 0,
                foreign_net_buy INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS stock_scores (
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
                signal TEXT DEFAULT '',
                signal_label TEXT DEFAULT '',
                reason TEXT DEFAULT '',
                current_price INTEGER DEFAULT 0,
                market_cap INTEGER DEFAULT 0,
                per REAL DEFAULT 0,
                pbr REAL DEFAULT 0,
                roe REAL DEFAULT 0,
                operating_margin REAL DEFAULT 0,
                debt_ratio REAL DEFAULT 0,
                dividend_yield REAL DEFAULT 0,
                stoploss_price INTEGER DEFAULT 0,
                stoploss_pct REAL DEFAULT 0,
                atr REAL DEFAULT 0,
                UNIQUE(analysis_date, stock_code)
            );

            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL UNIQUE,
                stock_name TEXT DEFAULT '',
                added_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                atr_multiplier REAL DEFAULT 2.0,
                memo TEXT DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_scores_date
                ON stock_scores(analysis_date);
            CREATE INDEX IF NOT EXISTS idx_scores_code
                ON stock_scores(stock_code);
            CREATE INDEX IF NOT EXISTS idx_results_date
                ON analysis_results(analysis_date);

            CREATE TABLE IF NOT EXISTS portfolio (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL,
                stock_name TEXT DEFAULT '',
                buy_price INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                buy_date TEXT NOT NULL DEFAULT (date('now', 'localtime')),
                memo TEXT DEFAULT '',
                is_sold INTEGER DEFAULT 0,
                sold_price INTEGER DEFAULT 0,
                sold_date TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_portfolio_code
                ON portfolio(stock_code);
            CREATE INDEX IF NOT EXISTS idx_portfolio_active
                ON portfolio(is_sold);

            CREATE TABLE IF NOT EXISTS daily_report_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                stock_name TEXT DEFAULT '',
                rank INTEGER DEFAULT 0,
                total_score INTEGER DEFAULT 0,
                signal TEXT DEFAULT '',
                signal_label TEXT DEFAULT '',
                current_price INTEGER DEFAULT 0,
                fair_value_low INTEGER DEFAULT 0,
                fair_value_high INTEGER DEFAULT 0,
                fair_value_gap REAL DEFAULT 0,
                value_score INTEGER DEFAULT 0,
                financial_score INTEGER DEFAULT 0,
                growth_score INTEGER DEFAULT 0,
                momentum_score INTEGER DEFAULT 0,
                quality_score INTEGER DEFAULT 0,
                per REAL DEFAULT 0,
                pbr REAL DEFAULT 0,
                roe REAL DEFAULT 0,
                revenue_growth REAL DEFAULT 0,
                op_income_growth REAL DEFAULT 0,
                foreign_net_buy_days INTEGER DEFAULT 0,
                stoploss_price INTEGER DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                UNIQUE(report_date, stock_code)
            );

            CREATE INDEX IF NOT EXISTS idx_report_log_date
                ON daily_report_log(report_date);

            CREATE TABLE IF NOT EXISTS performance_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                stock_name TEXT DEFAULT '',
                signal_at_report TEXT DEFAULT '',
                score_at_report INTEGER DEFAULT 0,
                price_at_report INTEGER DEFAULT 0,
                price_after_1w INTEGER DEFAULT 0,
                price_after_1m INTEGER DEFAULT 0,
                price_after_3m INTEGER DEFAULT 0,
                price_after_6m INTEGER DEFAULT 0,
                price_after_1y INTEGER DEFAULT 0,
                return_1w REAL DEFAULT 0,
                return_1m REAL DEFAULT 0,
                return_3m REAL DEFAULT 0,
                return_6m REAL DEFAULT 0,
                return_1y REAL DEFAULT 0,
                signal_correct INTEGER DEFAULT 0,
                last_updated TEXT DEFAULT '',
                is_delisted INTEGER DEFAULT 0,
                delisted_detected_at TEXT DEFAULT '',
                consecutive_fetch_failures INTEGER DEFAULT 0,
                UNIQUE(report_date, stock_code)
            );

            CREATE INDEX IF NOT EXISTS idx_perf_date
                ON performance_tracking(report_date);
            CREATE INDEX IF NOT EXISTS idx_perf_code
                ON performance_tracking(stock_code);

            CREATE TABLE IF NOT EXISTS financial_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL,
                year INTEGER NOT NULL,
                quarter TEXT NOT NULL DEFAULT 'annual',
                revenue INTEGER DEFAULT 0,
                operating_income INTEGER DEFAULT 0,
                net_income INTEGER DEFAULT 0,
                total_assets INTEGER DEFAULT 0,
                total_liabilities INTEGER DEFAULT 0,
                total_equity INTEGER DEFAULT 0,
                current_assets INTEGER DEFAULT 0,
                current_liabilities INTEGER DEFAULT 0,
                roe REAL DEFAULT 0,
                operating_margin REAL DEFAULT 0,
                debt_ratio REAL DEFAULT 0,
                current_ratio REAL DEFAULT 0,
                dividend_yield REAL DEFAULT 0,
                revenue_growth_yoy REAL DEFAULT 0,
                op_income_growth_yoy REAL DEFAULT 0,
                ebitda INTEGER DEFAULT 0,
                free_cash_flow INTEGER DEFAULT 0,
                cash_equivalents INTEGER DEFAULT 0,
                depreciation INTEGER DEFAULT 0,
                prev_revenue INTEGER DEFAULT 0,
                prev_operating_income INTEGER DEFAULT 0,
                prev_net_income INTEGER DEFAULT 0,
                consecutive_loss_years INTEGER DEFAULT 0,
                consecutive_op_decline_years INTEGER DEFAULT 0,
                consecutive_revenue_decline_years INTEGER DEFAULT 0,
                sector TEXT DEFAULT '기타',
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                UNIQUE(stock_code, year, quarter)
            );

            CREATE INDEX IF NOT EXISTS idx_fin_code
                ON financial_metrics(stock_code);
            CREATE INDEX IF NOT EXISTS idx_fin_year
                ON financial_metrics(year);

            -- 종목 마스터: 종목코드 → 종목명 매핑.
            -- 월요일 전종목 스캔(get_kospi_stock_list)에서 자동 갱신되며,
            -- 화~금 단일 시세 조회 시 종목명 폴백용으로 main.py가 lookup한다.
            CREATE TABLE IF NOT EXISTS stock_master (
                stock_code TEXT PRIMARY KEY,
                stock_name TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            -- 업종 평균: 월요일 전종목 스캔에서 동적 계산되어 저장된다.
            -- scorer.py가 _calc_fair_value/_score_sector_per 호출 시
            -- settings.py 고정값보다 우선 조회한다.
            CREATE TABLE IF NOT EXISTS sector_averages (
                sector TEXT PRIMARY KEY,
                avg_per REAL DEFAULT 0,
                avg_pbr REAL DEFAULT 0,
                avg_ev_ebitda REAL DEFAULT 0,
                sample_count INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
        """)

        # 기존 DB에 대한 마이그레이션 (생존편향 제거 컬럼).
        # 컬럼이 이미 있으면 sqlite3.OperationalError가 발생하므로 조용히 넘긴다.
        for ddl in (
            "ALTER TABLE performance_tracking "
            "ADD COLUMN is_delisted INTEGER DEFAULT 0",
            "ALTER TABLE performance_tracking "
            "ADD COLUMN delisted_detected_at TEXT DEFAULT ''",
            "ALTER TABLE performance_tracking "
            "ADD COLUMN consecutive_fetch_failures INTEGER DEFAULT 0",
            "ALTER TABLE stock_scores "
            "ADD COLUMN growth_score INTEGER DEFAULT 0",
            "ALTER TABLE stock_scores "
            "ADD COLUMN quality_score INTEGER DEFAULT 0",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass

        conn.commit()
        logger.info("데이터베이스 초기화 완료: %s", self.db_path)

    # ================================================================
    # 분석 결과 저장/조회
    # ================================================================
    def save_analysis_result(
        self,
        analysis_date: str,
        top_10: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
        stats: dict[str, Any],
        kospi_index: float = 0.0,
        foreign_net_buy: int = 0,
    ) -> int:
        """일별 분석 결과를 저장한다.

        Args:
            analysis_date: 분석일 (YYYY-MM-DD)
            top_10: TOP 10 종목 리스트
            warnings: 경고 종목 리스트
            stats: 통계 정보
            kospi_index: KOSPI 지수
            foreign_net_buy: 외국인 순매수 (억원)

        Returns:
            int: 저장된 레코드 ID
        """
        conn = self._get_conn()
        cursor = conn.execute(
            """INSERT INTO analysis_results
               (analysis_date, top_10_json, warnings_json, stats_json,
                kospi_index, foreign_net_buy)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                analysis_date,
                json.dumps(top_10, ensure_ascii=False, default=str),
                json.dumps(warnings, ensure_ascii=False, default=str),
                json.dumps(stats, ensure_ascii=False, default=str),
                kospi_index,
                foreign_net_buy,
            ),
        )
        conn.commit()
        logger.info("분석 결과 저장: %s (ID: %d)", analysis_date, cursor.lastrowid)
        return cursor.lastrowid

    def get_latest_result(self) -> Optional[dict[str, Any]]:
        """최신 분석 결과를 조회한다."""
        conn = self._get_conn()
        row = conn.execute(
            """SELECT * FROM analysis_results
               ORDER BY analysis_date DESC, id DESC LIMIT 1"""
        ).fetchone()

        if row is None:
            return None

        return self._parse_result_row(row)

    def get_results_by_date(
        self, start_date: str, end_date: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """기간별 분석 결과를 조회한다."""
        conn = self._get_conn()
        if end_date:
            rows = conn.execute(
                """SELECT * FROM analysis_results
                   WHERE analysis_date BETWEEN ? AND ?
                   ORDER BY analysis_date DESC""",
                (start_date, end_date),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM analysis_results
                   WHERE analysis_date >= ?
                   ORDER BY analysis_date DESC""",
                (start_date,),
            ).fetchall()

        return [self._parse_result_row(r) for r in rows]

    def _parse_result_row(self, row: sqlite3.Row) -> dict[str, Any]:
        """분석 결과 Row를 dict로 변환한다."""
        return {
            "id": row["id"],
            "analysis_date": row["analysis_date"],
            "created_at": row["created_at"],
            "top_10": json.loads(row["top_10_json"]),
            "warnings": json.loads(row["warnings_json"]),
            "stats": json.loads(row["stats_json"]),
            "kospi_index": row["kospi_index"],
            "foreign_net_buy": row["foreign_net_buy"],
        }

    # ================================================================
    # 종목별 스코어 저장/조회
    # ================================================================
    def save_stock_scores(
        self,
        analysis_date: str,
        signals: list[dict[str, Any]],
        stoploss_map: Optional[dict[str, dict[str, Any]]] = None,
    ) -> int:
        """종목별 스코어를 일괄 저장한다.

        Args:
            analysis_date: 분석일
            signals: 신호 판정 결과 리스트
            stoploss_map: {종목코드: 손절 정보}

        Returns:
            int: 저장된 레코드 수
        """
        if stoploss_map is None:
            stoploss_map = {}

        conn = self._get_conn()
        count = 0

        for sig in signals:
            code = sig.get("stock_code", "")
            sl = stoploss_map.get(code, {})

            try:
                conn.execute(
                    """INSERT OR REPLACE INTO stock_scores
                       (analysis_date, stock_code, stock_name,
                        total_score, value_score, financial_score,
                        growth_score, momentum_score, quality_score,
                        signal, signal_label, reason,
                        current_price, market_cap, per, pbr,
                        roe, operating_margin, debt_ratio, dividend_yield,
                        stoploss_price, stoploss_pct, atr)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        analysis_date,
                        code,
                        sig.get("stock_name", ""),
                        sig.get("total_score", 0),
                        sig.get("value_score", 0),
                        sig.get("financial_score", 0),
                        sig.get("growth_score", 0),
                        sig.get("momentum_score", 0),
                        sig.get("quality_score", 0),
                        sig.get("signal", ""),
                        sig.get("signal_label", ""),
                        sig.get("reason", ""),
                        sig.get("current_price", 0),
                        sig.get("market_cap", 0),
                        sig.get("per", 0),
                        sig.get("pbr", 0),
                        sig.get("roe", 0),
                        sig.get("operating_margin", 0),
                        sig.get("debt_ratio", 0),
                        sig.get("dividend_yield", 0),
                        sl.get("effective_stoploss", 0),
                        sl.get("effective_stoploss_pct", 0),
                        sl.get("atr", 0),
                    ),
                )
                count += 1
            except sqlite3.Error as e:
                logger.warning("종목 %s 스코어 저장 실패: %s", code, e)

        conn.commit()
        logger.info("종목 스코어 %d건 저장 완료 (%s)", count, analysis_date)
        return count

    def get_stock_score(
        self, stock_code: str, analysis_date: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        """특정 종목의 스코어를 조회한다."""
        conn = self._get_conn()
        if analysis_date:
            row = conn.execute(
                """SELECT * FROM stock_scores
                   WHERE stock_code = ? AND analysis_date = ?""",
                (stock_code, analysis_date),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM stock_scores
                   WHERE stock_code = ?
                   ORDER BY analysis_date DESC LIMIT 1""",
                (stock_code,),
            ).fetchone()

        return dict(row) if row else None

    def get_stock_history(
        self, stock_code: str, days: int = 7
    ) -> list[dict[str, Any]]:
        """종목의 최근 N일 스코어 이력을 조회한다."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM stock_scores
               WHERE stock_code = ?
               ORDER BY analysis_date DESC LIMIT ?""",
            (stock_code, days),
        ).fetchall()
        return [dict(r) for r in rows]

    # ================================================================
    # 재무 지표 캐시 (financial_metrics)
    # ================================================================
    def save_financial_metrics(self, metrics: dict[str, Any]) -> None:
        """재무 지표를 DB에 저장(upsert)한다."""
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO financial_metrics
               (stock_code, year, quarter,
                revenue, operating_income, net_income,
                total_assets, total_liabilities, total_equity,
                current_assets, current_liabilities,
                roe, operating_margin, debt_ratio, current_ratio,
                dividend_yield, revenue_growth_yoy, op_income_growth_yoy,
                ebitda, free_cash_flow, cash_equivalents, depreciation,
                prev_revenue, prev_operating_income, prev_net_income,
                consecutive_loss_years, consecutive_op_decline_years,
                consecutive_revenue_decline_years, sector, updated_at)
               VALUES (?,?,?, ?,?,?, ?,?,?, ?,?, ?,?,?,?, ?,?,?, ?,?,?,?, ?,?,?, ?,?,?, ?,?)""",
            (
                metrics.get("stock_code", ""),
                metrics.get("year", 0),
                metrics.get("quarter", "annual"),
                metrics.get("revenue", 0),
                metrics.get("operating_income", 0),
                metrics.get("net_income", 0),
                metrics.get("total_assets", 0),
                metrics.get("total_liabilities", 0),
                metrics.get("total_equity", 0),
                metrics.get("current_assets", 0),
                metrics.get("current_liabilities", 0),
                metrics.get("roe", 0.0),
                metrics.get("operating_margin", 0.0),
                metrics.get("debt_ratio", 0.0),
                metrics.get("current_ratio", 0.0),
                metrics.get("dividend_yield", 0.0),
                metrics.get("revenue_growth_yoy", 0.0),
                metrics.get("op_income_growth_yoy", 0.0),
                metrics.get("ebitda", 0),
                metrics.get("free_cash_flow", 0),
                metrics.get("cash_equivalents", 0),
                metrics.get("depreciation", 0),
                metrics.get("prev_revenue", 0),
                metrics.get("prev_operating_income", 0),
                metrics.get("prev_net_income", 0),
                metrics.get("consecutive_loss_years", 0),
                metrics.get("consecutive_op_decline_years", 0),
                metrics.get("consecutive_revenue_decline_years", 0),
                metrics.get("sector", "기타"),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()

    def get_financial_metrics(
        self, stock_code: str, year: int, quarter: str = "annual"
    ) -> Optional[dict[str, Any]]:
        """DB에서 재무 지표를 조회한다.

        Returns:
            dict or None: 재무 지표 (없거나 90일 초과 시 None)
        """
        conn = self._get_conn()
        row = conn.execute(
            """SELECT * FROM financial_metrics
               WHERE stock_code = ? AND year = ? AND quarter = ?""",
            (stock_code, year, quarter),
        ).fetchone()

        if row is None:
            return None

        # 90일 이상 지난 데이터는 만료 처리
        updated_at = row["updated_at"]
        try:
            updated_dt = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
            age_days = (datetime.now() - updated_dt).days
            if age_days > 90:
                return None
        except (ValueError, TypeError):
            return None

        return dict(row)

    def update_financial_sectors(
        self, sector_map: dict[str, str]
    ) -> int:
        """financial_metrics 테이블의 sector 컬럼을 일괄 갱신한다.

        DART는 sector를 제공하지 않으므로 KIS API에서 받은 값으로
        DB 캐시 row의 sector를 사후 보정한다. (캐시 히트 시에도 sector가
        최신 KIS 분류로 유지되도록 함)

        Args:
            sector_map: {stock_code: sector_name}

        Returns:
            int: 업데이트된 row 수
        """
        if not sector_map:
            return 0
        try:
            conn = self._get_conn()
            params = [(s, c, s) for c, s in sector_map.items() if s]
            cursor = conn.executemany(
                """UPDATE financial_metrics SET sector = ?
                       WHERE stock_code = ? AND sector != ?""",
                params,
            )
            conn.commit()
            return cursor.rowcount
        except sqlite3.Error as e:
            logger.warning("financial_metrics sector 갱신 실패: %s", e)
            return 0

    def get_op_income(
        self, stock_code: str, year: int, quarter: str = "annual"
    ) -> Optional[int]:
        """특정 연도의 영업이익만 조회한다 (freshness 체크 없음).

        턴어라운드 점수 계산용 — 과거 연도 데이터를 조회해야 하므로
        get_financial_metrics의 90일 만료 검사를 우회한다.

        Returns:
            int: 영업이익. 행이 없으면 None.
        """
        try:
            conn = self._get_conn()
            row = conn.execute(
                """SELECT operating_income FROM financial_metrics
                   WHERE stock_code = ? AND year = ? AND quarter = ?""",
                (stock_code, year, quarter),
            ).fetchone()
        except sqlite3.Error as e:
            logger.debug("get_op_income 실패 %s/%d: %s", stock_code, year, e)
            return None
        if row is None:
            return None
        return int(row["operating_income"] or 0)

    def save_financial_metrics_batch(
        self, metrics_list: list[dict[str, Any]]
    ) -> int:
        """재무 지표를 일괄 저장한다."""
        count = 0
        for m in metrics_list:
            try:
                self.save_financial_metrics(m)
                count += 1
            except Exception as e:
                logger.debug("재무 지표 저장 실패 %s: %s", m.get("stock_code"), e)
        return count

    # ================================================================
    # 업종 평균 (sector_averages)
    # ================================================================
    def save_sector_averages(
        self, averages: dict[str, dict[str, float]]
    ) -> int:
        """업종 평균 PER/PBR/EV-EBITDA를 truncate-insert 방식으로 저장한다.

        매 스캔마다 sector_averages 테이블 전체를 비우고 새 결과로 채운다.
        이전 스캔의 stale row(예: 더 이상 존재하지 않는 업종, 잘못된 분류 시절의
        '기타' 누적값 등)가 남지 않도록 보장한다.

        Args:
            averages: ScoringEngine.calculate_sector_averages 결과
                {업종명: {"avg_per": float, "avg_pbr": float,
                          "avg_ev_ebitda": float (optional),
                          "sample_count": int}}

        Returns:
            int: insert된 row 수
        """
        if not averages:
            return 0

        conn = self._get_conn()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            (
                sector,
                float(vals.get("avg_per", 0) or 0),
                float(vals.get("avg_pbr", 0) or 0),
                float(vals.get("avg_ev_ebitda", 0) or 0),
                int(vals.get("sample_count", 0) or 0),
                now,
            )
            for sector, vals in averages.items()
        ]
        # truncate-insert: 이전 스캔의 stale row 완전 제거
        conn.execute("DELETE FROM sector_averages")
        conn.executemany(
            """INSERT INTO sector_averages
               (sector, avg_per, avg_pbr, avg_ev_ebitda, sample_count, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        logger.info("업종 평균 %d건 저장 완료 (truncate-insert)", len(rows))
        return len(rows)

    def get_sector_averages(self) -> dict[str, dict[str, float]]:
        """저장된 업종 평균 전체를 dict로 조회한다.

        Returns:
            dict: {업종명: {"avg_per", "avg_pbr", "avg_ev_ebitda",
                            "sample_count", "updated_at"}}
        """
        try:
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT sector, avg_per, avg_pbr, avg_ev_ebitda,
                          sample_count, updated_at
                     FROM sector_averages"""
            ).fetchall()
        except sqlite3.Error as e:
            logger.warning("sector_averages 조회 실패: %s", e)
            return {}

        result: dict[str, dict[str, float]] = {}
        for r in rows:
            result[r["sector"]] = {
                "avg_per": r["avg_per"],
                "avg_pbr": r["avg_pbr"],
                "avg_ev_ebitda": r["avg_ev_ebitda"],
                "sample_count": r["sample_count"],
                "updated_at": r["updated_at"],
            }
        return result

    # ================================================================
    # 종목 마스터 (stock_master)
    # ================================================================
    def save_stock_master_batch(
        self, stocks: list[dict[str, Any]]
    ) -> int:
        """종목 마스터(코드 → 종목명)를 일괄 저장한다.

        UPSERT: 같은 stock_code가 있으면 stock_name과 updated_at을 갱신한다.
        stock_name이 비어 있는 항목은 건너뛴다.

        Args:
            stocks: 종목 리스트. 각 항목은 stock_code/stock_name 키를 가진 dict.

        Returns:
            int: 저장(insert+update)된 row 수.
        """
        rows = [
            (s["stock_code"], s["stock_name"])
            for s in stocks
            if s.get("stock_code") and s.get("stock_name")
        ]
        if not rows:
            return 0

        conn = self._get_conn()
        conn.executemany(
            """
            INSERT INTO stock_master (stock_code, stock_name)
            VALUES (?, ?)
            ON CONFLICT(stock_code) DO UPDATE SET
                stock_name = excluded.stock_name,
                updated_at = datetime('now', 'localtime')
            """,
            rows,
        )
        conn.commit()
        return len(rows)

    def get_stock_name(self, stock_code: str) -> str:
        """stock_master에서 종목명을 조회한다.

        Args:
            stock_code: 종목코드

        Returns:
            str: 종목명. 없거나 오류 시 빈 문자열.
        """
        try:
            conn = self._get_conn()
            cursor = conn.execute(
                "SELECT stock_name FROM stock_master WHERE stock_code = ?",
                (stock_code,),
            )
            row = cursor.fetchone()
            return row[0] if row else ""
        except Exception as e:
            logger.debug("stock_master 조회 실패 %s: %s", stock_code, e)
            return ""

    # ================================================================
    # 종목 검색
    # ================================================================
    def search_stock_by_name(self, name: str) -> list[dict[str, Any]]:
        """종목명으로 검색한다.

        가장 최근 분석일의 stock_scores에서 LIKE 검색한다.

        Args:
            name: 검색어 (부분 일치)

        Returns:
            list[dict]: [{stock_code, stock_name, total_score, signal_label}, ...]
        """
        conn = self._get_conn()
        # 최신 분석일 조회
        row = conn.execute(
            "SELECT MAX(analysis_date) as d FROM stock_scores"
        ).fetchone()

        if not row or not row["d"]:
            return []

        latest_date = row["d"]
        rows = conn.execute(
            """SELECT stock_code, stock_name, total_score, signal_label
               FROM stock_scores
               WHERE analysis_date = ? AND stock_name LIKE ?
               ORDER BY total_score DESC
               LIMIT 20""",
            (latest_date, f"%{name}%"),
        ).fetchall()

        return [dict(r) for r in rows]

    # ================================================================
    # 관심종목
    # ================================================================
    def add_watchlist(
        self, stock_code: str, stock_name: str = "", memo: str = ""
    ) -> bool:
        """관심종목을 추가한다."""
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO watchlist
                   (stock_code, stock_name, memo)
                   VALUES (?, ?, ?)""",
                (stock_code, stock_name, memo),
            )
            conn.commit()
            return True
        except sqlite3.Error as e:
            logger.warning("관심종목 추가 실패: %s", e)
            return False

    def remove_watchlist(self, stock_code: str) -> bool:
        """관심종목을 삭제한다."""
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM watchlist WHERE stock_code = ?", (stock_code,)
        )
        conn.commit()
        return cursor.rowcount > 0

    def get_watchlist(self) -> list[dict[str, Any]]:
        """관심종목 리스트를 조회한다."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM watchlist ORDER BY added_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ================================================================
    # 포트폴리오 (매수/매도/추가매수 관리)
    # ================================================================
    def add_portfolio(
        self,
        stock_code: str,
        buy_price: int,
        quantity: int,
        stock_name: str = "",
        memo: str = "",
    ) -> int:
        """매수 기록을 추가한다 (추가 매수 시 새 레코드 생성).

        Args:
            stock_code: 종목코드
            buy_price: 매수 단가
            quantity: 매수 수량
            stock_name: 종목명
            memo: 메모

        Returns:
            int: 레코드 ID
        """
        conn = self._get_conn()
        cursor = conn.execute(
            """INSERT INTO portfolio
               (stock_code, stock_name, buy_price, quantity, memo)
               VALUES (?, ?, ?, ?, ?)""",
            (stock_code, stock_name, buy_price, quantity, memo),
        )
        conn.commit()
        logger.info(
            "매수 기록: %s %s %d원 × %d주",
            stock_code, stock_name, buy_price, quantity
        )
        return cursor.lastrowid

    def sell_portfolio(
        self,
        stock_code: str,
        quantity: int,
        sold_price: int = 0,
    ) -> int:
        """매도 기록을 처리한다.

        FIFO 방식: 가장 먼저 매수한 것부터 매도 처리.
        부분 매도 시 해당 레코드의 수량을 분할한다.

        Args:
            stock_code: 종목코드
            quantity: 매도 수량 (0이면 전량)
            sold_price: 매도 단가 (0이면 기록 안 함)

        Returns:
            int: 매도 처리된 수량
        """
        conn = self._get_conn()
        sold_date = datetime.now().strftime("%Y-%m-%d")

        # 보유 중인 매수 기록 (FIFO 순서)
        rows = conn.execute(
            """SELECT id, quantity, buy_price FROM portfolio
               WHERE stock_code = ? AND is_sold = 0
               ORDER BY buy_date ASC, id ASC""",
            (stock_code,),
        ).fetchall()

        if not rows:
            return 0

        # 전량 매도
        total_holding = sum(r["quantity"] for r in rows)
        if quantity == 0:
            quantity = total_holding

        remaining = min(quantity, total_holding)
        sold_total = 0

        for row in rows:
            if remaining <= 0:
                break

            row_qty = row["quantity"]

            if remaining >= row_qty:
                # 이 레코드 전량 매도
                conn.execute(
                    """UPDATE portfolio
                       SET is_sold = 1, sold_price = ?, sold_date = ?
                       WHERE id = ?""",
                    (sold_price, sold_date, row["id"]),
                )
                remaining -= row_qty
                sold_total += row_qty
            else:
                # 부분 매도: 기존 레코드 수량 줄이고, 매도분 새 레코드 생성
                conn.execute(
                    """UPDATE portfolio SET quantity = ? WHERE id = ?""",
                    (row_qty - remaining, row["id"]),
                )
                conn.execute(
                    """INSERT INTO portfolio
                       (stock_code, stock_name, buy_price, quantity,
                        buy_date, is_sold, sold_price, sold_date)
                       SELECT stock_code, stock_name, buy_price, ?,
                              buy_date, 1, ?, ?
                       FROM portfolio WHERE id = ?""",
                    (remaining, sold_price, sold_date, row["id"]),
                )
                sold_total += remaining
                remaining = 0

        conn.commit()
        logger.info("매도 처리: %s %d주", stock_code, sold_total)
        return sold_total

    def get_portfolio(self) -> list[dict[str, Any]]:
        """보유 포트폴리오를 종목별로 집계하여 조회한다.

        추가 매수분을 평균 매수가로 합산한다.

        Returns:
            list[dict]: 종목별 포트폴리오
                [{
                    "stock_code": str,
                    "stock_name": str,
                    "avg_buy_price": int,     # 평균 매수 단가
                    "total_quantity": int,     # 총 보유 수량
                    "total_invested": int,     # 총 투자 금액
                    "buy_count": int,          # 매수 횟수
                    "first_buy_date": str,     # 최초 매수일
                    "last_buy_date": str,      # 최근 매수일
                    "lots": list,              # 개별 매수 내역
                }, ...]
        """
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM portfolio
               WHERE is_sold = 0
               ORDER BY stock_code, buy_date ASC""",
        ).fetchall()

        # 종목별 집계
        portfolio_map: dict[str, dict[str, Any]] = {}

        for row in rows:
            code = row["stock_code"]
            if code not in portfolio_map:
                portfolio_map[code] = {
                    "stock_code": code,
                    "stock_name": row["stock_name"],
                    "total_quantity": 0,
                    "total_invested": 0,
                    "buy_count": 0,
                    "first_buy_date": row["buy_date"],
                    "last_buy_date": row["buy_date"],
                    "lots": [],
                }

            entry = portfolio_map[code]
            qty = row["quantity"]
            price = row["buy_price"]

            entry["total_quantity"] += qty
            entry["total_invested"] += price * qty
            entry["buy_count"] += 1
            entry["last_buy_date"] = row["buy_date"]
            entry["lots"].append({
                "id": row["id"],
                "buy_price": price,
                "quantity": qty,
                "buy_date": row["buy_date"],
                "memo": row["memo"],
            })

        # 평균 매수가 계산
        result = []
        for entry in portfolio_map.values():
            if entry["total_quantity"] > 0:
                entry["avg_buy_price"] = int(
                    entry["total_invested"] / entry["total_quantity"]
                )
            else:
                entry["avg_buy_price"] = 0
            result.append(entry)

        return result

    def get_portfolio_stock(self, stock_code: str) -> Optional[dict[str, Any]]:
        """특정 종목의 포트폴리오 정보를 조회한다."""
        portfolio = self.get_portfolio()
        for p in portfolio:
            if p["stock_code"] == stock_code:
                return p
        return None

    def clear_portfolio(self) -> int:
        """포트폴리오를 전체 초기화한다."""
        conn = self._get_conn()
        count = conn.execute("DELETE FROM portfolio").rowcount
        conn.commit()
        logger.info("포트폴리오 초기화: %d건 삭제", count)
        return count

    # ================================================================
    # 리포트 로그
    # ================================================================
    def save_daily_report_log(
        self,
        report_date: str,
        top_10_list: list[dict[str, Any]],
    ) -> int:
        """TOP 10 리포트 스냅샷을 저장한다.

        Args:
            report_date: 분석일 (YYYY-MM-DD)
            top_10_list: TOP 10 종목 리스트 (rank 필드 포함)

        Returns:
            int: 저장된 레코드 수
        """
        conn = self._get_conn()
        count = 0

        for stock in top_10_list:
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO daily_report_log
                       (report_date, stock_code, stock_name, rank,
                        total_score, signal, signal_label, current_price,
                        fair_value_low, fair_value_high, fair_value_gap,
                        value_score, financial_score, growth_score,
                        momentum_score, quality_score,
                        per, pbr, roe, revenue_growth, op_income_growth,
                        foreign_net_buy_days, stoploss_price)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        report_date,
                        stock.get("stock_code", ""),
                        stock.get("stock_name", ""),
                        stock.get("rank", 0),
                        stock.get("total_score", 0),
                        stock.get("signal", ""),
                        stock.get("signal_label", ""),
                        stock.get("current_price", 0),
                        stock.get("fair_value_low", 0),
                        stock.get("fair_value_high", 0),
                        stock.get("fair_value_gap", 0),
                        stock.get("value_score", 0),
                        stock.get("financial_score", 0),
                        stock.get("growth_score", 0),
                        stock.get("momentum_score", 0),
                        stock.get("quality_score", 0),
                        stock.get("per", 0),
                        stock.get("pbr", 0),
                        stock.get("roe", 0),
                        stock.get("revenue_growth", 0),
                        stock.get("op_income_growth", 0),
                        stock.get("foreign_net_buy_days", 0),
                        stock.get("stoploss_price", 0),
                    ),
                )
                count += 1
            except sqlite3.Error as e:
                logger.warning("리포트 로그 저장 실패 %s: %s",
                               stock.get("stock_code", ""), e)

        conn.commit()
        logger.info("리포트 로그 %d건 저장 (%s)", count, report_date)
        return count

    def get_latest_report_log_for_stock(
        self, stock_code: str,
    ) -> Optional[dict[str, Any]]:
        """daily_report_log에서 특정 종목의 가장 최근 행을 조회한다.

        TOP 10에 들었던 종목에 한해 v3 카테고리 점수, 적정주가, 수급 등
        확장 필드를 보강하기 위해 사용된다. 없으면 None.
        """
        conn = self._get_conn()
        row = conn.execute(
            """SELECT * FROM daily_report_log
               WHERE stock_code = ?
               ORDER BY report_date DESC LIMIT 1""",
            (stock_code,),
        ).fetchone()
        return dict(row) if row else None

    def get_report_log(
        self, report_date: Optional[str] = None, days: int = 30,
    ) -> list[dict[str, Any]]:
        """리포트 로그를 조회한다."""
        conn = self._get_conn()
        if report_date:
            rows = conn.execute(
                "SELECT * FROM daily_report_log WHERE report_date = ? ORDER BY rank",
                (report_date,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM daily_report_log
                   WHERE report_date >= date('now', 'localtime', '-' || ? || ' days')
                   ORDER BY report_date DESC, rank""",
                (days,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ================================================================
    # 성과 추적
    # ================================================================
    def update_performance_tracking(
        self,
        kis_client: Any,
    ) -> int:
        """과거 추천 종목의 현재가를 조회하여 수익률을 업데이트한다.

        daily_report_log에서 아직 추적 완료되지 않은 종목을 찾아
        KIS API로 현재가를 조회하고 경과 기간에 맞는 수익률을 계산한다.

        Args:
            kis_client: KISClient 인스턴스

        Returns:
            int: 업데이트된 레코드 수
        """
        conn = self._get_conn()
        today = datetime.now()
        today_str = today.strftime("%Y-%m-%d")
        updated = 0
        # 호출 단위 cascade skip 이벤트 리셋 — 매 사이클 시작 시 비움.
        self.last_cascade_skip_events = []

        # 기간 기준 (일수)
        periods = {
            "1w": 7, "1m": 30, "3m": 90, "6m": 180, "1y": 365,
        }

        # 과거 1년 이내 리포트 로그에서 고유 종목 조회
        rows = conn.execute(
            """SELECT DISTINCT report_date, stock_code, stock_name,
                      signal, total_score, current_price
               FROM daily_report_log
               WHERE report_date >= date('now', 'localtime', '-366 days')
               ORDER BY report_date""",
        ).fetchall()

        if not rows:
            return 0

        # 기존 추적 상태 (생존편향 제거용).
        # (report_date, stock_code) → {is_delisted, consecutive_fetch_failures}
        existing_status: dict[tuple[str, str], dict[str, int]] = {}
        delisted_codes: set[str] = set()
        for r in conn.execute(
            """SELECT report_date, stock_code, is_delisted,
                      consecutive_fetch_failures
               FROM performance_tracking""",
        ).fetchall():
            existing_status[(r["report_date"], r["stock_code"])] = {
                "is_delisted": r["is_delisted"] or 0,
                "consecutive_fetch_failures":
                    r["consecutive_fetch_failures"] or 0,
            }
            if r["is_delisted"]:
                delisted_codes.add(r["stock_code"])

        # 종목별 현재가 캐시
        price_cache: dict[str, int] = {}
        # 종목별 마지막 예외 타입명 (cascade 안전장치 판단용)
        last_exception_by_code: dict[str, str] = {}
        # 사이클 단위 cascade 발화 카운터 (폭주 방지 다층 방어)
        cascade_fired_this_cycle: int = 0
        cascade_circuit_open: bool = False

        for row in rows:
            report_date = row["report_date"]
            code = row["stock_code"]
            price_at_report = row["current_price"]

            if price_at_report <= 0:
                continue

            # 이미 상장폐지로 판정된 종목은 조회 자체를 스킵.
            if code in delisted_codes:
                continue

            # 경과 일수 계산
            try:
                report_dt = datetime.strptime(report_date, "%Y-%m-%d")
            except ValueError:
                continue
            elapsed_days = (today - report_dt).days

            if elapsed_days < 7:
                continue  # 최소 1주 경과 후부터 추적

            # 현재가 조회 (캐시)
            if code not in price_cache:
                try:
                    price_data = kis_client.get_stock_price(code)
                    price_cache[code] = price_data.get("current_price", 0)
                except Exception as e:
                    logger.warning("현재가 조회 예외 %s: %s", code, e)
                    price_cache[code] = 0
                    last_exception_by_code[code] = type(e).__name__

            current_price = price_cache.get(code, 0)

            # 조회 실패 처리 (생존편향 제거).
            if current_price <= 0:
                status = existing_status.get(
                    (report_date, code),
                    {"is_delisted": 0, "consecutive_fetch_failures": 0},
                )
                new_failures = status["consecutive_fetch_failures"] + 1

                if new_failures >= 3:
                    # 3회 연속 실패. cascade 전에 안전장치 검사.
                    last_exc = last_exception_by_code.get(code, "")
                    skip, reason = self._should_skip_cascade(code, last_exc)
                    # 사이클 단위 폭주 방지 — 임계치 초과 시 cascade 차단.
                    if (not skip and not cascade_circuit_open and
                            cascade_fired_this_cycle >= _CASCADE_PER_CYCLE_LIMIT):
                        cascade_circuit_open = True
                        logger.error(
                            "cascade circuit-breaker 발동: 사이클당 cascade %d건 "
                            "도달 → 잔여 cascade 차단. 수동 점검 필요.",
                            cascade_fired_this_cycle,
                        )
                    if cascade_circuit_open and not skip:
                        skip, reason = True, (
                            f"circuit-breaker (cycle limit "
                            f"{_CASCADE_PER_CYCLE_LIMIT})"
                        )

                    # 어떤 경로든 실패 카운터는 최신화.
                    conn.execute(
                        """INSERT INTO performance_tracking
                           (report_date, stock_code, stock_name,
                            signal_at_report, score_at_report,
                            price_at_report,
                            consecutive_fetch_failures, last_updated)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(report_date, stock_code) DO UPDATE SET
                             consecutive_fetch_failures = excluded.
                                 consecutive_fetch_failures,
                             last_updated = excluded.last_updated""",
                        (
                            report_date, code, row["stock_name"],
                            row["signal"], row["total_score"],
                            price_at_report, new_failures, today_str,
                        ),
                    )

                    if skip:
                        logger.warning(
                            "cascade 스킵 %s (누적 실패 %d회, 사유: %s). "
                            "카운터만 유지하고 상장폐지 판정은 미수행.",
                            code, new_failures, reason,
                        )
                        # 운영자 알림용 이벤트 적재. main.py가 사이클 끝에
                        # 텔레그램 WARN으로 일괄 전송한다.
                        self.last_cascade_skip_events.append({
                            "stock_code": code,
                            "stock_name": row["stock_name"],
                            "report_date": report_date,
                            "consecutive_failures": new_failures,
                            "last_exception": last_exception_by_code.get(
                                code, "n/a"
                            ),
                            "reason": reason,
                        })
                    else:
                        logger.error(
                            "자동 상장폐지 판정: %s (누적 실패 %d회, "
                            "마지막 예외=%s). 실제 상장폐지 여부는 사람이 "
                            "확인 후 필요시 mark_stock_delisted로 재확정 권장.",
                            code, new_failures, last_exc or "n/a",
                        )
                        affected = self._cascade_mark_delisted(
                            code, today_str, conn,
                        )
                        delisted_codes.add(code)
                        updated += affected
                        cascade_fired_this_cycle += 1
                else:
                    logger.warning(
                        "현재가 조회 실패 %s (report_date=%s, 누적 실패 %d회)",
                        code, report_date, new_failures,
                    )
                    # 실패 카운터만 갱신 (없으면 stub insert).
                    conn.execute(
                        """INSERT INTO performance_tracking
                           (report_date, stock_code, stock_name,
                            signal_at_report, score_at_report, price_at_report,
                            consecutive_fetch_failures, last_updated)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(report_date, stock_code) DO UPDATE SET
                             consecutive_fetch_failures = excluded.
                                 consecutive_fetch_failures,
                             last_updated = excluded.last_updated""",
                        (
                            report_date, code, row["stock_name"],
                            row["signal"], row["total_score"],
                            price_at_report, new_failures, today_str,
                        ),
                    )
                continue

            # 경과 기간별 수익률 계산
            updates: dict[str, tuple[int, float]] = {}
            for label, days_needed in periods.items():
                if elapsed_days >= days_needed:
                    ret = round(((current_price - price_at_report)
                                 / price_at_report) * 100, 2)
                    updates[label] = (current_price, ret)

            if not updates:
                continue

            # 신호 적중 여부 판단
            signal = row["signal"]
            best_return = max(v[1] for v in updates.values())
            if signal in ("strong_buy", "buy"):
                signal_correct = 1 if best_return > 0 else 0
            elif signal == "sell":
                signal_correct = 1 if best_return < 0 else 0
            else:
                signal_correct = 0

            # UPSERT
            try:
                conn.execute(
                    """INSERT INTO performance_tracking
                       (report_date, stock_code, stock_name,
                        signal_at_report, score_at_report, price_at_report,
                        price_after_1w, return_1w,
                        price_after_1m, return_1m,
                        price_after_3m, return_3m,
                        price_after_6m, return_6m,
                        price_after_1y, return_1y,
                        signal_correct, last_updated)
                       VALUES (?, ?, ?, ?, ?, ?,
                               ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?)
                       ON CONFLICT(report_date, stock_code) DO UPDATE SET
                        price_after_1w = CASE WHEN excluded.price_after_1w > 0
                                              THEN excluded.price_after_1w
                                              ELSE performance_tracking.price_after_1w END,
                        return_1w = CASE WHEN excluded.return_1w != 0
                                         THEN excluded.return_1w
                                         ELSE performance_tracking.return_1w END,
                        price_after_1m = CASE WHEN excluded.price_after_1m > 0
                                              THEN excluded.price_after_1m
                                              ELSE performance_tracking.price_after_1m END,
                        return_1m = CASE WHEN excluded.return_1m != 0
                                         THEN excluded.return_1m
                                         ELSE performance_tracking.return_1m END,
                        price_after_3m = CASE WHEN excluded.price_after_3m > 0
                                              THEN excluded.price_after_3m
                                              ELSE performance_tracking.price_after_3m END,
                        return_3m = CASE WHEN excluded.return_3m != 0
                                         THEN excluded.return_3m
                                         ELSE performance_tracking.return_3m END,
                        price_after_6m = CASE WHEN excluded.price_after_6m > 0
                                              THEN excluded.price_after_6m
                                              ELSE performance_tracking.price_after_6m END,
                        return_6m = CASE WHEN excluded.return_6m != 0
                                         THEN excluded.return_6m
                                         ELSE performance_tracking.return_6m END,
                        price_after_1y = CASE WHEN excluded.price_after_1y > 0
                                              THEN excluded.price_after_1y
                                              ELSE performance_tracking.price_after_1y END,
                        return_1y = CASE WHEN excluded.return_1y != 0
                                         THEN excluded.return_1y
                                         ELSE performance_tracking.return_1y END,
                        signal_correct = excluded.signal_correct,
                        last_updated = excluded.last_updated,
                        consecutive_fetch_failures = 0""",
                    (
                        report_date, code, row["stock_name"],
                        signal, row["total_score"], price_at_report,
                        updates.get("1w", (0, 0))[0], updates.get("1w", (0, 0))[1],
                        updates.get("1m", (0, 0))[0], updates.get("1m", (0, 0))[1],
                        updates.get("3m", (0, 0))[0], updates.get("3m", (0, 0))[1],
                        updates.get("6m", (0, 0))[0], updates.get("6m", (0, 0))[1],
                        updates.get("1y", (0, 0))[0], updates.get("1y", (0, 0))[1],
                        signal_correct, today_str,
                    ),
                )
                updated += 1
            except sqlite3.Error as e:
                logger.warning("성과 추적 업데이트 실패 %s/%s: %s",
                               report_date, code, e)

        conn.commit()
        logger.info("성과 추적 %d건 업데이트", updated)
        return updated

    def get_performance_data(
        self,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """기간별 성과 추적 데이터를 조회한다."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM performance_tracking
               WHERE report_date BETWEEN ? AND ?
               ORDER BY report_date, score_at_report DESC""",
            (start_date, end_date),
        ).fetchall()
        return [dict(r) for r in rows]

    # ================================================================
    # 생존편향 제거 (상장폐지 종목 보정)
    # ================================================================
    def _get_latest_market_cap(self, stock_code: str) -> int:
        """stock_scores에서 가장 최근 저장된 market_cap을 반환한다.

        cascade 안전장치 (대형주 화이트리스트) 용도. 값이 없으면 0.
        """
        try:
            conn = self._get_conn()
            row = conn.execute(
                """SELECT market_cap FROM stock_scores
                   WHERE stock_code = ? AND market_cap > 0
                   ORDER BY analysis_date DESC LIMIT 1""",
                (stock_code,),
            ).fetchone()
            return int(row["market_cap"]) if row else 0
        except sqlite3.Error:
            return 0

    def _should_skip_cascade(
        self,
        stock_code: str,
        last_exception_name: str,
    ) -> tuple[bool, str]:
        """cascade를 건너뛰어야 하는지 판단.

        Returns:
            (skip, reason). skip=True면 cascade 금지.
        """
        if last_exception_name in _CASCADE_SKIP_EXCEPTION_NAMES:
            return True, (
                f"런타임 예외 {last_exception_name} — 실제 상장폐지 신호 아님"
            )
        market_cap = self._get_latest_market_cap(stock_code)
        if market_cap >= _LARGE_CAP_THRESHOLD_KRW:
            return True, (
                f"대형주 화이트리스트 (시총 {market_cap:,}원 "
                f"≥ {_LARGE_CAP_THRESHOLD_KRW:,}원)"
            )
        return False, ""

    def _cascade_mark_delisted(
        self,
        stock_code: str,
        detected_at: str,
        conn: sqlite3.Connection,
    ) -> int:
        """종목의 모든 performance_tracking 레코드를 상장폐지로 표시.

        is_delisted=1, delisted_detected_at, 모든 return_*=-100.0,
        signal_correct는 signal 기준으로 재계산 (strong_buy/buy는 오답,
        sell는 정답).
        """
        cur = conn.execute(
            """UPDATE performance_tracking
               SET is_delisted = 1,
                   delisted_detected_at = ?,
                   return_1w = -100.0,
                   return_1m = -100.0,
                   return_3m = -100.0,
                   return_6m = -100.0,
                   return_1y = -100.0,
                   signal_correct = CASE
                       WHEN signal_at_report IN ('strong_buy', 'buy') THEN 0
                       WHEN signal_at_report = 'sell' THEN 1
                       ELSE 0 END,
                   last_updated = ?
               WHERE stock_code = ?""",
            (detected_at, detected_at, stock_code),
        )
        return cur.rowcount

    def mark_stock_delisted(self, stock_code: str) -> int:
        """수동 상장폐지 표시.

        해당 종목의 모든 performance_tracking 레코드를
        is_delisted=1, 각 return_*=-100.0으로 갱신한다.

        Returns:
            영향받은 행 수.
        """
        conn = self._get_conn()
        today_str = datetime.now().strftime("%Y-%m-%d")
        affected = self._cascade_mark_delisted(stock_code, today_str, conn)
        conn.commit()
        logger.info("수동 상장폐지 표시: %s (%d행 영향)", stock_code, affected)
        return affected

    def get_delisted_stocks(self) -> list[dict[str, Any]]:
        """is_delisted=1인 종목 고유 목록."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT stock_code,
                      MAX(stock_name) AS stock_name,
                      MIN(delisted_detected_at) AS first_detected,
                      COUNT(*) AS affected_rows
               FROM performance_tracking
               WHERE is_delisted = 1
               GROUP BY stock_code
               ORDER BY first_detected DESC""",
        ).fetchall()
        return [dict(r) for r in rows]

    def get_fetch_failure_candidates(
        self,
        threshold: int = 3,
    ) -> list[dict[str, Any]]:
        """consecutive_fetch_failures >= threshold 종목.

        실제 상장폐지인지 사람이 확인 후 mark_stock_delisted 수동 호출 용도.
        자동 판정은 오탐 위험이 있어 이 리스트로 운영자가 재검토할 수 있다.
        """
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT stock_code,
                      MAX(stock_name) AS stock_name,
                      MAX(consecutive_fetch_failures) AS failures,
                      MAX(is_delisted) AS is_delisted,
                      MAX(last_updated) AS last_updated
               FROM performance_tracking
               WHERE consecutive_fetch_failures >= ?
               GROUP BY stock_code
               ORDER BY failures DESC""",
            (threshold,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ================================================================
    # 정리
    # ================================================================
    def cleanup_old_data(self, retention_days: Optional[int] = None) -> int:
        """오래된 데이터를 정리한다.

        Args:
            retention_days: 보관 기간 (일)

        Returns:
            int: 삭제된 레코드 수
        """
        if retention_days is None:
            retention_days = DBConfig.HISTORY_RETENTION_DAYS

        conn = self._get_conn()
        cutoff = datetime.now().strftime("%Y-%m-%d")

        count1 = conn.execute(
            """DELETE FROM analysis_results
               WHERE analysis_date < date(?, '-' || ? || ' days')""",
            (cutoff, retention_days),
        ).rowcount

        count2 = conn.execute(
            """DELETE FROM stock_scores
               WHERE analysis_date < date(?, '-' || ? || ' days')""",
            (cutoff, retention_days),
        ).rowcount

        conn.commit()
        total = count1 + count2
        if total > 0:
            logger.info("오래된 데이터 %d건 정리 완료", total)
        return total

    def close(self) -> None:
        """DB 연결을 닫는다."""
        if self._conn:
            self._conn.close()
            self._conn = None


# ================================================================
# 테스트
# ================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    db = Database(db_path="data/test_kospi.db")

    # 분석 결과 저장
    db.save_analysis_result(
        analysis_date="2026-04-05",
        top_10=[{"stock_code": "005930", "stock_name": "삼성전자", "total_score": 87}],
        warnings=[],
        stats={"total_analyzed": 800},
        kospi_index=2680.5,
    )

    # 조회
    latest = db.get_latest_result()
    print(f"최신 분석: {latest['analysis_date']}, TOP1: {latest['top_10'][0]['stock_name']}")

    # 관심종목
    db.add_watchlist("005930", "삼성전자", "반도체 회복")
    print(f"관심종목: {db.get_watchlist()}")

    db.close()
    print("✅ DB 테스트 완료")

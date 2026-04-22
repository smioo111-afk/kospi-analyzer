"""
KOSPI 저평가 기업 분석 시스템 - 분석 이력 관리 모듈

분석 결과의 이력 조회 및 통계를 제공한다:
  - 최근 N일 분석 이력 조회
  - 종목별 스코어 변화 추적
  - 신호 변경 감지 (매수→매도 등)
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from database.models import Database

logger = logging.getLogger(__name__)


class AnalysisHistory:
    """분석 이력 관리.

    DB를 통해 과거 분석 결과를 조회하고 추세를 분석한다.
    """

    def __init__(self, db: Optional[Database] = None) -> None:
        self.db = db or Database()

    def get_recent_reports(self, days: int = 7) -> list[dict[str, Any]]:
        """최근 N일 분석 리포트를 조회한다.

        Args:
            days: 조회 일수

        Returns:
            list[dict]: 날짜별 분석 결과
        """
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return self.db.get_results_by_date(start_date)

    def get_stock_trend(
        self, stock_code: str, days: int = 7
    ) -> list[dict[str, Any]]:
        """종목의 스코어 변화 추세를 조회한다.

        Args:
            stock_code: 종목코드
            days: 조회 일수

        Returns:
            list[dict]: 날짜별 스코어 이력
        """
        return self.db.get_stock_history(stock_code, days)

    def detect_signal_changes(
        self,
        current_signals: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """전일 대비 신호 변경을 감지한다.

        매수→매도 등 신호가 바뀐 종목을 찾아낸다.

        Args:
            current_signals: 오늘의 신호 리스트

        Returns:
            list[dict]: 신호 변경된 종목 리스트
                [{
                    "stock_code": str,
                    "stock_name": str,
                    "prev_signal": str,
                    "new_signal": str,
                    "score_change": int,
                }, ...]
        """
        changes: list[dict[str, Any]] = []

        for sig in current_signals:
            code = sig.get("stock_code", "")
            prev = self.db.get_stock_score(code)

            if prev is None:
                continue

            prev_signal = prev.get("signal", "")
            new_signal = sig.get("signal", "")

            if prev_signal and new_signal and prev_signal != new_signal:
                changes.append({
                    "stock_code": code,
                    "stock_name": sig.get("stock_name", ""),
                    "prev_signal": prev_signal,
                    "prev_signal_label": prev.get("signal_label", ""),
                    "new_signal": new_signal,
                    "new_signal_label": sig.get("signal_label", ""),
                    "prev_score": prev.get("total_score", 0),
                    "new_score": sig.get("total_score", 0),
                    "score_change": sig.get("total_score", 0) - prev.get("total_score", 0),
                })

        if changes:
            logger.info("신호 변경 %d건 감지", len(changes))

        return changes

    def save_daily_result(
        self,
        analysis_date: str,
        top_10: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
        all_signals: list[dict[str, Any]],
        stats: dict[str, Any],
        stoploss_map: Optional[dict[str, dict[str, Any]]] = None,
        kospi_index: float = 0.0,
        foreign_net_buy: int = 0,
    ) -> None:
        """일일 분석 결과 전체를 저장한다.

        Args:
            analysis_date: 분석일
            top_10: TOP 10 종목
            warnings: 경고 종목
            all_signals: 전 종목 신호
            stats: 통계
            stoploss_map: 손절 정보
            kospi_index: KOSPI 지수
            foreign_net_buy: 외국인 순매수
        """
        # 분석 결과 저장
        self.db.save_analysis_result(
            analysis_date=analysis_date,
            top_10=top_10,
            warnings=warnings,
            stats=stats,
            kospi_index=kospi_index,
            foreign_net_buy=foreign_net_buy,
        )

        # 종목별 스코어 저장
        self.db.save_stock_scores(
            analysis_date=analysis_date,
            signals=all_signals,
            stoploss_map=stoploss_map,
        )

        # 오래된 데이터 정리
        self.db.cleanup_old_data()

        logger.info("일일 결과 저장 완료: %s", analysis_date)

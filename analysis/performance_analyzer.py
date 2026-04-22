"""
KOSPI 저평가 기업 분석 시스템 - 성과 분석 모듈

추천 종목의 실제 수익률을 분석하여 성과 리포트를 생성한다:
  - 신호별 적중률
  - 평균 수익률
  - 점수 구간별 수익률
  - 적정주가 괴리율 대비 실제 수익률
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from database.models import Database

logger = logging.getLogger(__name__)

# 기간 설정 (월수)
PERIOD_MONTHS = {
    "monthly": 1,
    "quarterly": 3,
    "half_yearly": 6,
    "yearly": 12,
}

PERIOD_LABELS = {
    "monthly": "월간",
    "quarterly": "분기",
    "half_yearly": "반기",
    "yearly": "연간",
}

# 기간별 수익률 필드 매핑
RETURN_FIELD_BY_PERIOD = {
    "monthly": "return_1m",
    "quarterly": "return_3m",
    "half_yearly": "return_6m",
    "yearly": "return_1y",
}


class PerformanceAnalyzer:
    """추천 종목 성과 분석기."""

    def __init__(self, db: Optional[Database] = None) -> None:
        self.db = db or Database()

    def generate_performance_report(
        self,
        period: str = "monthly",
    ) -> dict[str, Any]:
        """기간별 성과 리포트를 생성한다.

        Args:
            period: "monthly", "quarterly", "half_yearly", "yearly"

        Returns:
            dict: 성과 리포트
        """
        if period not in PERIOD_MONTHS:
            raise ValueError(f"지원하지 않는 기간: {period}")

        months = PERIOD_MONTHS[period]
        end_date = datetime.now()
        start_date = end_date - timedelta(days=months * 31)

        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        # 성과 데이터 조회
        data = self.db.get_performance_data(start_str, end_str)

        if not data:
            return {
                "period": period,
                "period_label": PERIOD_LABELS[period],
                "start_date": start_str,
                "end_date": end_str,
                "total_stocks": 0,
                "has_data": False,
            }

        # 수익률 필드 결정
        return_field = RETURN_FIELD_BY_PERIOD[period]

        # 유효 데이터 필터 (수익률이 계산된 것만)
        valid = [d for d in data if d.get(return_field, 0) != 0
                 or d.get("price_at_report", 0) > 0]

        # 리포트 로그에서도 조회 (추천 종목 수 계산용)
        report_logs = self.db.get_report_log(days=months * 31)
        unique_dates = set(r["report_date"] for r in report_logs
                          if start_str <= r["report_date"] <= end_str)
        unique_stocks = set(
            (r["report_date"], r["stock_code"]) for r in report_logs
            if start_str <= r["report_date"] <= end_str
        )

        # 1) 총 추천 종목 수
        total_stocks = len(unique_stocks)
        total_days = len(unique_dates)

        # 2) 신호별 적중률
        signal_accuracy = self._calc_signal_accuracy(valid, return_field)

        # 3) 평균 수익률
        avg_return = self._calc_avg_return(valid, return_field)

        # 4) TOP 5 / WORST 5
        top5, worst5 = self._calc_top_worst(valid, return_field, n=5)

        # 5) 점수 구간별 수익률
        score_brackets = self._calc_score_bracket_returns(valid, return_field)

        # 6) 적정주가 괴리율 상관관계
        fair_value_corr = self._calc_fair_value_correlation(
            report_logs, valid, return_field, start_str, end_str)

        return {
            "period": period,
            "period_label": PERIOD_LABELS[period],
            "start_date": start_str,
            "end_date": end_str,
            "has_data": True,
            "total_stocks": total_stocks,
            "total_days": total_days,
            "signal_accuracy": signal_accuracy,
            "avg_return": avg_return,
            "top5": top5,
            "worst5": worst5,
            "score_brackets": score_brackets,
            "fair_value_correlation": fair_value_corr,
        }

    def _calc_signal_accuracy(
        self, data: list[dict], return_field: str,
    ) -> dict[str, Any]:
        """신호별 적중률을 계산한다."""
        buy_total = buy_correct = 0
        sell_total = sell_correct = 0

        for d in data:
            ret = d.get(return_field, 0)
            if ret == 0:
                continue
            signal = d.get("signal_at_report", "")
            if signal in ("strong_buy", "buy"):
                buy_total += 1
                if ret > 0:
                    buy_correct += 1
            elif signal == "sell":
                sell_total += 1
                if ret < 0:
                    sell_correct += 1

        buy_pct = round(buy_correct / buy_total * 100, 1) if buy_total > 0 else 0
        sell_pct = round(sell_correct / sell_total * 100, 1) if sell_total > 0 else 0

        return {
            "buy_total": buy_total,
            "buy_correct": buy_correct,
            "buy_accuracy": buy_pct,
            "sell_total": sell_total,
            "sell_correct": sell_correct,
            "sell_accuracy": sell_pct,
        }

    def _calc_avg_return(
        self, data: list[dict], return_field: str,
    ) -> dict[str, float]:
        """평균 수익률을 계산한다."""
        buy_returns = [d[return_field] for d in data
                       if d.get("signal_at_report") in ("strong_buy", "buy")
                       and d.get(return_field, 0) != 0]
        all_returns = [d[return_field] for d in data
                       if d.get(return_field, 0) != 0]

        return {
            "buy_avg": round(sum(buy_returns) / len(buy_returns), 2) if buy_returns else 0,
            "all_avg": round(sum(all_returns) / len(all_returns), 2) if all_returns else 0,
            "buy_count": len(buy_returns),
            "all_count": len(all_returns),
        }

    def _calc_top_worst(
        self, data: list[dict], return_field: str, n: int = 5,
    ) -> tuple[list[dict], list[dict]]:
        """최고/최저 수익 종목을 선별한다."""
        with_returns = [d for d in data if d.get(return_field, 0) != 0]
        sorted_data = sorted(with_returns,
                             key=lambda x: x.get(return_field, 0),
                             reverse=True)

        def fmt(d: dict) -> dict:
            return {
                "stock_code": d.get("stock_code", ""),
                "stock_name": d.get("stock_name", ""),
                "report_date": d.get("report_date", ""),
                "score": d.get("score_at_report", 0),
                "signal": d.get("signal_at_report", ""),
                "return_pct": d.get(return_field, 0),
                "price_at_report": d.get("price_at_report", 0),
            }

        top = [fmt(d) for d in sorted_data[:n]]
        worst = [fmt(d) for d in sorted_data[-n:] if d.get(return_field, 0) < 0]
        worst.reverse()  # 최악부터

        return top, worst

    def _calc_score_bracket_returns(
        self, data: list[dict], return_field: str,
    ) -> list[dict[str, Any]]:
        """점수 구간별 수익률을 계산한다."""
        brackets = [
            ("75점 이상", 75, 101),
            ("60~74점", 60, 75),
            ("45~59점", 45, 60),
        ]
        result = []

        for label, low, high in brackets:
            returns = [d[return_field] for d in data
                       if low <= d.get("score_at_report", 0) < high
                       and d.get(return_field, 0) != 0]
            avg = round(sum(returns) / len(returns), 2) if returns else 0
            result.append({
                "label": label,
                "count": len(returns),
                "avg_return": avg,
            })

        return result

    def _calc_fair_value_correlation(
        self,
        report_logs: list[dict],
        perf_data: list[dict],
        return_field: str,
        start_str: str,
        end_str: str,
    ) -> dict[str, Any]:
        """적정주가 괴리율 대비 실제 수익률 상관관계를 분석한다."""
        # 리포트 로그에서 fair_value_gap 매핑
        gap_map: dict[tuple[str, str], float] = {}
        for r in report_logs:
            if start_str <= r.get("report_date", "") <= end_str:
                gap_map[(r["report_date"], r["stock_code"])] = r.get("fair_value_gap", 0)

        pairs: list[tuple[float, float]] = []
        for d in perf_data:
            ret = d.get(return_field, 0)
            if ret == 0:
                continue
            key = (d["report_date"], d["stock_code"])
            gap = gap_map.get(key, 0)
            if gap != 0:
                pairs.append((gap, ret))

        if len(pairs) < 3:
            return {"correlation": 0, "sample_size": len(pairs),
                    "insight": "데이터 부족"}

        # 간이 상관계수 (피어슨)
        n = len(pairs)
        sum_x = sum(p[0] for p in pairs)
        sum_y = sum(p[1] for p in pairs)
        sum_xy = sum(p[0] * p[1] for p in pairs)
        sum_x2 = sum(p[0] ** 2 for p in pairs)
        sum_y2 = sum(p[1] ** 2 for p in pairs)

        denom = ((n * sum_x2 - sum_x ** 2) * (n * sum_y2 - sum_y ** 2)) ** 0.5
        if denom == 0:
            corr = 0.0
        else:
            corr = round((n * sum_xy - sum_x * sum_y) / denom, 3)

        if corr < -0.3:
            insight = "저평가 종목이 실제 수익률이 높은 경향 (역상관)"
        elif corr > 0.3:
            insight = "고평가 종목이 수익률도 높은 경향 (주의)"
        else:
            insight = "괴리율과 수익률 간 뚜렷한 상관관계 없음"

        return {
            "correlation": corr,
            "sample_size": n,
            "insight": insight,
        }

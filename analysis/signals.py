"""
KOSPI 저평가 기업 분석 시스템 - 매수/매도/보유 판정 모듈

설계서 섹션 5의 신호 판정 로직을 구현한다:
  - 🟢 강력 매수: 종합 ≥80 AND 모멘텀 ≥15 AND 재무 ≥20
  - 🟡 매수: 종합 65~79 AND 재무 ≥15
  - ⭐ 보유: 종합 50~64
  - 🔴 매도: 종합 <50 OR 모멘텀 <5 OR 손절라인 도달

  - 필터링: 시총 1000억↑, 거래대금 10억↑, 금융주 별도, 3년 연속 적자 제외
"""

import logging
from typing import Any, Optional

from config.settings import SignalConfig

logger = logging.getLogger(__name__)


# 신호 상수
class Signal:
    """매수/매도/보유 신호 상수."""

    STRONG_BUY = "strong_buy"
    BUY = "buy"
    HOLD = "hold"
    SELL = "sell"

    # 표시용
    LABELS = {
        "strong_buy": "🟢 강력매수",
        "buy": "🟡 매수",
        "hold": "⭐ 보유",
        "sell": "🔴 매도",
    }

    @classmethod
    def label(cls, signal: str) -> str:
        """신호 코드를 한글 라벨로 변환한다."""
        return cls.LABELS.get(signal, "❓ 알수없음")


class SignalGenerator:
    """매수/매도/보유 신호 생성기.

    스코어링 결과를 바탕으로 투자 신호를 판정하고,
    위험 종목을 필터링하여 TOP 10을 선별한다.
    """

    def __init__(self) -> None:
        self.cfg = SignalConfig

    # ================================================================
    # 신호 판정
    # ================================================================
    def determine_signal(
        self,
        score_data: dict[str, Any],
        stoploss_hit: bool = False,
    ) -> dict[str, Any]:
        """개별 종목의 투자 신호를 판정한다.

        설계서 5.1 판정 기준에 따라 신호를 생성한다.

        Args:
            score_data: 스코어링 결과 (scorer.py 출력)
            stoploss_hit: 손절라인 도달 여부

        Returns:
            dict: 신호 판정 결과
                {
                    "signal": str,          # 신호 코드
                    "signal_label": str,     # 한글 라벨
                    "reason": str,           # 판정 사유
                    **score_data,            # 원본 스코어 데이터 포함
                }
        """
        total = score_data.get("total_score", 0)
        momentum = score_data.get("momentum_score", 0)
        financial = score_data.get("financial_score", 0)
        growth = score_data.get("growth_score", 0)

        signal, reason = self._judge(total, momentum, financial, stoploss_hit, growth=growth)

        result = {**score_data}
        result["signal"] = signal
        result["signal_label"] = Signal.label(signal)
        result["reason"] = reason

        return result

    def _judge(
        self,
        total: int,
        momentum: int,
        financial: int,
        stoploss_hit: bool,
        **kwargs: Any,
    ) -> tuple[str, str]:
        """신호를 판정한다 (설계서 5.1).

        Returns:
            tuple: (신호 코드, 판정 사유)
        """
        cfg = self.cfg

        # 매도 조건 (우선 판정)
        if stoploss_hit:
            return Signal.SELL, "손절라인 도달"

        if total < cfg.SELL_SCORE:
            return Signal.SELL, f"종합점수 {total}점 < {cfg.SELL_SCORE}점"

        if momentum < cfg.SELL_MOMENTUM_MIN:
            return Signal.SELL, f"모멘텀 {momentum}점 < {cfg.SELL_MOMENTUM_MIN}점"

        # 성장성 점수 (v2.0 추가)
        growth = kwargs.get("growth", 0)

        # 강력 매수: 성장성 조건 추가
        growth_min = getattr(cfg, "STRONG_BUY_GROWTH", 0)
        if (
            total >= cfg.STRONG_BUY_SCORE
            and momentum >= cfg.STRONG_BUY_MOMENTUM
            and financial >= cfg.STRONG_BUY_FINANCIAL
            and growth >= growth_min
        ):
            return (
                Signal.STRONG_BUY,
                f"종합 {total}점, 모멘텀 {momentum}점, 재무 {financial}점, 성장 {growth}점",
            )

        # 매수
        if (
            cfg.BUY_SCORE_MIN <= total <= cfg.BUY_SCORE_MAX
            and financial >= cfg.BUY_FINANCIAL_MIN
        ):
            return Signal.BUY, f"종합 {total}점, 재무 {financial}점"

        # 보유
        if cfg.HOLD_SCORE_MIN <= total <= cfg.HOLD_SCORE_MAX:
            return Signal.HOLD, f"종합 {total}점 (보유 구간)"

        # 매수 점수이지만 재무 미달
        if cfg.BUY_SCORE_MIN <= total <= cfg.BUY_SCORE_MAX:
            return Signal.HOLD, f"종합 {total}점이나 재무 {financial}점 미달"

        # 80점 이상이지만 모멘텀/재무 미달
        if total >= cfg.STRONG_BUY_SCORE:
            return Signal.BUY, f"종합 {total}점이나 모멘텀/재무 조건 미충족"

        return Signal.HOLD, f"종합 {total}점"

    # ================================================================
    # 필터링 (설계서 5.2)
    # ================================================================
    def filter_stocks(
        self,
        scored_list: list[dict[str, Any]],
        financial_list: Optional[list[dict[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        """위험 종목을 필터링한다.

        설계서 5.2 조건:
        - 시가총액 1,000억 미만 제외
        - 일평균 거래대금 10억 미만 제외
        - 금융주 별도 분류
        - 3년 연속 적자 기업 제외

        Args:
            scored_list: 스코어링 결과 리스트
            financial_list: 재무 데이터 리스트 (적자 확인용)

        Returns:
            list[dict]: 필터링된 종목 리스트
        """
        fin_map = {}
        if financial_list:
            fin_map = {f["stock_code"]: f for f in financial_list}

        filtered: list[dict[str, Any]] = []
        excluded_count = {"market_cap": 0, "trading": 0, "financial": 0, "loss": 0}

        for stock in scored_list:
            code = stock.get("stock_code", "")

            # 시가총액 필터
            market_cap = stock.get("market_cap", 0)
            if market_cap < self.cfg.MIN_MARKET_CAP:
                excluded_count["market_cap"] += 1
                continue

            # 거래대금 필터
            trading_value = stock.get("trading_value", 0)
            if trading_value < self.cfg.MIN_TRADING_VALUE:
                excluded_count["trading"] += 1
                continue

            # 금융주 표시 (제외하지 않고 태그만 추가)
            stock["is_financial"] = self._is_financial_sector(code)

            # 3년 연속 적자 제외
            fin_data = fin_map.get(code, {})
            loss_years = fin_data.get("consecutive_loss_years", 0)
            if loss_years >= self.cfg.EXCLUDE_CONSECUTIVE_LOSS_YEARS:
                excluded_count["loss"] += 1
                continue

            filtered.append(stock)

        logger.info(
            "필터링 결과: %d/%d 종목 통과 "
            "(제외 - 시총: %d, 거래: %d, 금융: %d, 적자: %d)",
            len(filtered), len(scored_list),
            excluded_count["market_cap"], excluded_count["trading"],
            excluded_count["financial"], excluded_count["loss"],
        )

        return filtered

    def _is_financial_sector(self, stock_code: str) -> bool:
        """금융주 여부를 판단한다.

        TODO: 실제 업종코드 매핑 필요. 현재는 간이 판별.
        """
        # 추후 KRX 업종 데이터와 연동
        return False

    # ================================================================
    # TOP N 선별
    # ================================================================
    def select_top_n(
        self,
        signal_list: list[dict[str, Any]],
        n: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """저평가 기업 TOP N을 선별한다.

        Args:
            signal_list: 신호 판정 완료된 종목 리스트
            n: 선별 개수 (기본: settings의 TOP_N)

        Returns:
            list[dict]: TOP N 종목 (종합점수 내림차순)
        """
        if n is None:
            n = self.cfg.TOP_N

        # 매도 신호 제외, 총점 내림차순 정렬
        candidates = [
            s for s in signal_list
            if s.get("signal") != Signal.SELL
        ]

        candidates.sort(key=lambda x: x.get("total_score", 0), reverse=True)

        top_n = candidates[:n]

        logger.info(
            "TOP %d 선별 완료 (후보 %d개 중)", len(top_n), len(candidates)
        )

        return top_n

    # ================================================================
    # 전체 파이프라인
    # ================================================================
    def generate_signals(
        self,
        scored_list: list[dict[str, Any]],
        financial_list: Optional[list[dict[str, Any]]] = None,
        stoploss_map: Optional[dict[str, bool]] = None,
    ) -> dict[str, Any]:
        """전체 신호 생성 파이프라인을 실행한다.

        Args:
            scored_list: 스코어링 결과 리스트
            financial_list: 재무 데이터 (필터링용)
            stoploss_map: {종목코드: 손절 도달 여부}

        Returns:
            dict: 전체 결과
                {
                    "top_10": list,         # TOP 10 종목
                    "warnings": list,       # 경고 종목
                    "all_signals": list,    # 전 종목 신호
                    "stats": dict,          # 통계
                }
        """
        if stoploss_map is None:
            stoploss_map = {}

        # 1. 필터링
        filtered = self.filter_stocks(scored_list, financial_list)

        # 2. 신호 판정
        all_signals: list[dict[str, Any]] = []
        for stock in filtered:
            code = stock.get("stock_code", "")
            hit = stoploss_map.get(code, False)
            signal_result = self.determine_signal(stock, stoploss_hit=hit)
            all_signals.append(signal_result)

        # 3. TOP 10 선별
        top_10 = self.select_top_n(all_signals)

        # 4. 경고 종목 (손절라인 접근 등)
        warnings = [
            s for s in all_signals
            if stoploss_map.get(s.get("stock_code", ""), False)
        ]

        # 5. 통계
        signal_counts = {}
        for s in all_signals:
            sig = s.get("signal", "unknown")
            signal_counts[sig] = signal_counts.get(sig, 0) + 1

        stats = {
            "total_analyzed": len(scored_list),
            "after_filter": len(filtered),
            "signal_distribution": signal_counts,
        }

        logger.info(
            "신호 생성 완료: 분석 %d → 필터 %d → TOP %d, 경고 %d",
            stats["total_analyzed"], stats["after_filter"],
            len(top_10), len(warnings),
        )

        return {
            "top_10": top_10,
            "warnings": warnings,
            "all_signals": all_signals,
            "stats": stats,
        }


# ================================================================
# 테스트
# ================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    gen = SignalGenerator()

    # 테스트 데이터
    tests = [
        {"stock_code": "A", "stock_name": "강력매수종목", "total_score": 85,
         "value_score": 35, "financial_score": 25, "momentum_score": 25,
         "current_price": 50000, "market_cap": 500_000_000_000,
         "trading_value": 50_000_000_000, "per": 7.0, "pbr": 0.8,
         "roe": 15.0, "operating_margin": 12.0, "debt_ratio": 60.0,
         "current_ratio": 180.0, "dividend_yield": 3.0, "volume": 5000000},
        {"stock_code": "B", "stock_name": "매수종목", "total_score": 70,
         "value_score": 28, "financial_score": 22, "momentum_score": 20,
         "current_price": 30000, "market_cap": 200_000_000_000,
         "trading_value": 20_000_000_000, "per": 10.0, "pbr": 1.0,
         "roe": 10.0, "operating_margin": 8.0, "debt_ratio": 80.0,
         "current_ratio": 150.0, "dividend_yield": 2.0, "volume": 3000000},
        {"stock_code": "C", "stock_name": "매도종목", "total_score": 35,
         "value_score": 15, "financial_score": 10, "momentum_score": 10,
         "current_price": 10000, "market_cap": 50_000_000_000,
         "trading_value": 5_000_000_000, "per": 20.0, "pbr": 1.8,
         "roe": 3.0, "operating_margin": 2.0, "debt_ratio": 180.0,
         "current_ratio": 90.0, "dividend_yield": 0.0, "volume": 500000},
    ]

    for t in tests:
        result = gen.determine_signal(t)
        print(f"{result['stock_name']}: {result['signal_label']} ({result['reason']})")

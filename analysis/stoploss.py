"""
KOSPI 저평가 기업 분석 시스템 - 손절 라인 계산 모듈

설계서 섹션 6의 ATR 기반 손절 라인을 계산한다:
  - ATR(14) = 14일간 True Range의 평균
  - 손절 라인 = 현재가 - (ATR × 배수)
  - 보조: 최대 손실 -7%, 60MA 이탈 경고, 3일 연속 하락 경고
"""

import logging
from typing import Any, Optional

import numpy as np

from config.settings import StopLossConfig

logger = logging.getLogger(__name__)


class StopLossCalculator:
    """ATR 기반 손절 라인 계산기.

    종목별 변동성에 맞는 동적 손절 라인을 산출한다.
    """

    def __init__(self, multiplier: Optional[float] = None) -> None:
        """초기화.

        Args:
            multiplier: ATR 배수 (None이면 설정값 사용)
        """
        self.cfg = StopLossConfig
        self.multiplier = multiplier or self.cfg.ATR_MULTIPLIER

    # ================================================================
    # ATR 계산
    # ================================================================
    def calculate_atr(self, chart_data: list[dict[str, Any]]) -> float:
        """ATR(14)을 계산한다.

        설계서 6.1.1:
        True Range = max(고가-저가, |고가-전일종가|, |저가-전일종가|)
        ATR(14) = 14일간 True Range의 평균

        Args:
            chart_data: 일봉 데이터 (최신순, 최소 15일)

        Returns:
            float: ATR 값 (0이면 계산 불가)
        """
        period = self.cfg.ATR_PERIOD

        if len(chart_data) < period + 1:
            logger.debug("ATR 계산 데이터 부족: %d일 (필요 %d일)", len(chart_data), period + 1)
            if len(chart_data) < 2:
                return 0.0
            period = len(chart_data) - 1

        true_ranges: list[float] = []

        for i in range(period):
            high = chart_data[i].get("high", 0)
            low = chart_data[i].get("low", 0)
            prev_close = chart_data[i + 1].get("close", 0)

            if high == 0 or low == 0 or prev_close == 0:
                continue

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )
            true_ranges.append(tr)

        if not true_ranges:
            return 0.0

        return float(np.mean(true_ranges))

    # ================================================================
    # 손절 라인 계산
    # ================================================================
    def calculate_stoploss(
        self,
        current_price: int,
        chart_data: list[dict[str, Any]],
        multiplier: Optional[float] = None,
    ) -> dict[str, Any]:
        """종목의 손절 라인을 계산한다.

        설계서 6.1:
        손절 라인 = 현재가 - (ATR × 배수)

        Args:
            current_price: 현재가
            chart_data: 일봉 데이터
            multiplier: ATR 배수 (None이면 인스턴스 기본값)

        Returns:
            dict: 손절 라인 정보
                {
                    "atr": float,                # ATR 값
                    "multiplier": float,          # 적용 배수
                    "stoploss_price": int,        # 손절 라인 가격
                    "stoploss_pct": float,        # 현재가 대비 손절 비율 (%)
                    "hard_stop_price": int,       # 하드 스톱 가격 (-7%)
                    "effective_stoploss": int,     # 실제 적용 손절 가격 (둘 중 높은 값)
                    "effective_stoploss_pct": float,
                    "warnings": list[str],        # 경고 메시지
                }
        """
        if multiplier is None:
            multiplier = self.multiplier

        # 배수 범위 제한
        multiplier = max(
            self.cfg.ATR_MULTIPLIER_MIN,
            min(multiplier, self.cfg.ATR_MULTIPLIER_MAX),
        )

        atr = self.calculate_atr(chart_data)
        warnings: list[str] = []

        # ATR 기반 손절
        if atr > 0 and current_price > 0:
            atr_stoploss = int(current_price - (atr * multiplier))
            atr_stoploss_pct = round(
                ((atr_stoploss - current_price) / current_price) * 100, 2
            )
        else:
            atr_stoploss = 0
            atr_stoploss_pct = 0.0
            warnings.append("ATR 계산 불가 (차트 데이터 부족)")

        # 하드 스톱 (-7%)
        hard_stop_pct = self.cfg.HARD_STOP_LOSS_PCT
        hard_stop_price = int(current_price * (1 + hard_stop_pct / 100))

        # 실제 적용 손절 = 둘 중 높은 가격 (더 보수적인 값)
        if atr_stoploss > 0:
            effective = max(atr_stoploss, hard_stop_price)
        else:
            effective = hard_stop_price

        effective_pct = round(
            ((effective - current_price) / current_price) * 100, 2
        ) if current_price > 0 else 0.0

        # 보조 경고 확인
        warnings.extend(self._check_warnings(current_price, chart_data))

        return {
            "atr": round(atr, 2),
            "multiplier": multiplier,
            "stoploss_price": max(atr_stoploss, 0),
            "stoploss_pct": atr_stoploss_pct,
            "hard_stop_price": hard_stop_price,
            "effective_stoploss": effective,
            "effective_stoploss_pct": effective_pct,
            "warnings": warnings,
        }

    # ================================================================
    # 보조 경고 (설계서 6.2)
    # ================================================================
    def _check_warnings(
        self,
        current_price: int,
        chart_data: list[dict[str, Any]],
    ) -> list[str]:
        """보조 손절 경고를 확인한다.

        설계서 6.2:
        - 60일 이동평균선 하향 이탈 시 경고
        - 3일 연속 하락 시 경고
        """
        warnings: list[str] = []

        if not chart_data or current_price <= 0:
            return warnings

        closes = [c["close"] for c in chart_data if c.get("close", 0) > 0]

        # 60MA 이탈 경고
        if self.cfg.MA60_BREAK_WARNING and len(closes) >= 60:
            ma60 = np.mean(closes[:60])
            if current_price < ma60:
                pct = round(((current_price - ma60) / ma60) * 100, 2)
                warnings.append(f"60일 이동평균선 하향 이탈 ({pct}%)")

        # 3일 연속 하락 경고
        consecutive = self.cfg.CONSECUTIVE_DOWN_DAYS
        if len(closes) >= consecutive + 1:
            is_consecutive_down = all(
                closes[i] < closes[i + 1]
                for i in range(consecutive)
            )
            if is_consecutive_down:
                total_drop = round(
                    ((closes[0] - closes[consecutive]) / closes[consecutive]) * 100, 2
                )
                warnings.append(
                    f"{consecutive}일 연속 하락 (누적 {total_drop}%)"
                )

        return warnings

    # ================================================================
    # 손절 도달 여부 확인
    # ================================================================
    def check_stoploss_hit(
        self,
        current_price: int,
        stoploss_price: int,
    ) -> bool:
        """현재가가 손절 라인에 도달했는지 확인한다.

        Args:
            current_price: 현재가
            stoploss_price: 손절 라인 가격

        Returns:
            bool: 손절 도달 여부
        """
        if stoploss_price <= 0 or current_price <= 0:
            return False
        return current_price <= stoploss_price

    # ================================================================
    # 배치 손절 계산
    # ================================================================
    def calculate_all_stoploss(
        self,
        price_list: list[dict[str, Any]],
        chart_dict: dict[str, list[dict[str, Any]]],
    ) -> dict[str, dict[str, Any]]:
        """전 종목 손절 라인을 계산한다.

        Args:
            price_list: 종목별 현재가 데이터
            chart_dict: {종목코드: 일봉 차트}

        Returns:
            dict: {종목코드: 손절 라인 정보}
        """
        results: dict[str, dict[str, Any]] = {}

        for price in price_list:
            code = price.get("stock_code", "")
            current = price.get("current_price", 0)
            chart = chart_dict.get(code, [])

            stoploss = self.calculate_stoploss(current, chart)
            results[code] = stoploss

        logger.info("전체 %d 종목 손절 라인 계산 완료", len(results))
        return results

    # ================================================================
    # ATR 배수 변경
    # ================================================================
    def set_multiplier(self, multiplier: float) -> None:
        """ATR 배수를 변경한다.

        Args:
            multiplier: 새 ATR 배수 (1.0~3.0)

        Raises:
            ValueError: 범위 초과 시
        """
        if not self.cfg.ATR_MULTIPLIER_MIN <= multiplier <= self.cfg.ATR_MULTIPLIER_MAX:
            raise ValueError(
                f"ATR 배수는 {self.cfg.ATR_MULTIPLIER_MIN}~"
                f"{self.cfg.ATR_MULTIPLIER_MAX} 범위여야 합니다"
            )
        self.multiplier = multiplier
        logger.info("ATR 배수 변경: %.1f", multiplier)


# ================================================================
# 테스트
# ================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    calc = StopLossCalculator()

    # 가상 차트 데이터
    import random
    random.seed(42)
    chart = []
    price = 50000
    for i in range(60):
        h = price + random.randint(500, 1500)
        l = price - random.randint(500, 1500)
        c = price + random.randint(-800, 800)
        chart.append({"date": f"d{i}", "open": price, "high": h, "low": l, "close": c, "volume": 1000000})
        price = c

    result = calc.calculate_stoploss(50000, chart)
    print("=== 손절 라인 계산 결과 ===")
    for k, v in result.items():
        print(f"  {k}: {v}")

"""ShadowTrader 베이스 — 자동 매매 호환 아키텍처.

진화 경로:
  v1 (5월~6월): VirtualOrderExecutor (가상)
  v2 (7월~):   RealtimePriceProvider 추가
  v3 (8월~):   MockOrderExecutor (KIS 모의)
  v4 (검증 후): LiveOrderExecutor (KIS 실거래)

모든 단계에서 ShadowTrader 본문 변경 없음.
PriceProvider, OrderExecutor만 교체.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


# =====================================================================
# 가격 조회 인터페이스
# =====================================================================
class PriceProvider(ABC):
    @abstractmethod
    def get_current_price(self, stock_code: str) -> int:
        """현재 가격 (원). 조회 실패 시 0."""
        raise NotImplementedError


class ClosingPriceProvider(PriceProvider):
    """v1: 종가 기반 (stock_scores DB).

    섀도우 v1은 일별 사이클의 종가만 사용한다.
    """

    def __init__(self, db: Any) -> None:
        self.db = db

    def get_current_price(self, stock_code: str) -> int:
        score = self.db.get_stock_score(stock_code)
        if score is None:
            return 0
        return int(score.get("current_price", 0) or 0)


# =====================================================================
# 주문 실행 인터페이스
# =====================================================================
class OrderExecutor(ABC):
    @abstractmethod
    def buy(self, stock_code: str, quantity: int, price: int) -> dict:
        """매수 실행. {'status', 'order_id', 'message'} 반환."""
        raise NotImplementedError

    @abstractmethod
    def sell(self, stock_code: str, quantity: int, price: int) -> dict:
        """매도 실행. {'status', 'order_id', 'message'} 반환."""
        raise NotImplementedError


class VirtualOrderExecutor(OrderExecutor):
    """v1: 가상 주문 — 외부 주문 송신 없음.

    DB 업데이트만 수행 (단계 2/섀도우 가동 시 구현).
    모든 매수/매도 즉시 체결로 가정.
    """

    def __init__(self, db: Any) -> None:
        self.db = db

    def buy(self, stock_code: str, quantity: int, price: int) -> dict:
        logger.info(
            "[VIRTUAL BUY] %s %d주 @ %s원", stock_code, quantity, f"{price:,}",
        )
        return {
            "status": "success",
            "order_id": f"virtual_buy_{stock_code}",
            "message": "virtual buy placeholder",
        }

    def sell(self, stock_code: str, quantity: int, price: int) -> dict:
        logger.info(
            "[VIRTUAL SELL] %s %d주 @ %s원", stock_code, quantity, f"{price:,}",
        )
        return {
            "status": "success",
            "order_id": f"virtual_sell_{stock_code}",
            "message": "virtual sell placeholder",
        }


# =====================================================================
# ShadowTrader (전략 로직)
# =====================================================================
class ShadowTrader:
    """섀도우 트레이더 — 진화 호환 인터페이스.

    v1 ~ v4 모두 동일한 ShadowTrader 사용.
    PriceProvider, OrderExecutor만 교체해 가상→실거래로 진화.
    """

    def __init__(
        self,
        db: Any,
        price_provider: PriceProvider,
        order_executor: OrderExecutor,
        max_positions: int = 5,
        position_size: int = 2_000_000,
    ) -> None:
        self.db = db
        self.price_provider = price_provider
        self.order_executor = order_executor
        self.max_positions = max_positions
        self.position_size = position_size

    def execute_cycle(self) -> dict:
        """섀도우 트레이드 사이클.

        섀도우 가동(5/19~) 후 main.py에서 호출. 단계 1에서는 인터페이스만.

        순서:
          1. 보유 종목 매도 검사 (단계 2 이후)
          2. 신규 매수 검토 (BUY 종목 우선)
          3. 일별 스냅샷 저장
        """
        # TODO: 단계 2 + 섀도우 시작 시 구현
        return {
            "sells": 0,
            "buys": 0,
            "cash": 0,
            "value": 0,
            "note": "shadow trader not yet active",
        }

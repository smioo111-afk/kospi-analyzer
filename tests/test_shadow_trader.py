"""analysis/shadow_trader.py 단위 테스트."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.shadow_trader import (  # noqa: E402
    ClosingPriceProvider,
    OrderExecutor,
    PriceProvider,
    ShadowTrader,
    VirtualOrderExecutor,
)


# -------------------------------------------------------------------
# ClosingPriceProvider
# -------------------------------------------------------------------
def test_closing_price_provider_returns_db_price():
    db = MagicMock()
    db.get_stock_score.return_value = {"current_price": 70000}
    provider = ClosingPriceProvider(db)
    assert provider.get_current_price("005930") == 70000
    db.get_stock_score.assert_called_once_with("005930")


def test_closing_price_provider_handles_missing():
    db = MagicMock()
    db.get_stock_score.return_value = None
    provider = ClosingPriceProvider(db)
    assert provider.get_current_price("999999") == 0


def test_closing_price_provider_handles_zero_or_null():
    db = MagicMock()
    db.get_stock_score.return_value = {"current_price": None}
    provider = ClosingPriceProvider(db)
    assert provider.get_current_price("005930") == 0


# -------------------------------------------------------------------
# VirtualOrderExecutor
# -------------------------------------------------------------------
def test_virtual_executor_buy_returns_success():
    db = MagicMock()
    ex = VirtualOrderExecutor(db)
    result = ex.buy("005930", 10, 70000)
    assert result["status"] == "success"
    assert "005930" in result["order_id"]


def test_virtual_executor_sell_returns_success():
    db = MagicMock()
    ex = VirtualOrderExecutor(db)
    result = ex.sell("005930", 10, 70000)
    assert result["status"] == "success"
    assert "005930" in result["order_id"]


# -------------------------------------------------------------------
# ShadowTrader
# -------------------------------------------------------------------
def test_shadow_trader_initialization_default_params():
    db = MagicMock()
    trader = ShadowTrader(
        db, ClosingPriceProvider(db), VirtualOrderExecutor(db),
    )
    assert trader.max_positions == 5
    assert trader.position_size == 2_000_000


def test_shadow_trader_initialization_custom_params():
    db = MagicMock()
    trader = ShadowTrader(
        db, ClosingPriceProvider(db), VirtualOrderExecutor(db),
        max_positions=10, position_size=5_000_000,
    )
    assert trader.max_positions == 10
    assert trader.position_size == 5_000_000


def test_shadow_trader_execute_cycle_returns_dict():
    db = MagicMock()
    trader = ShadowTrader(
        db, ClosingPriceProvider(db), VirtualOrderExecutor(db),
    )
    result = trader.execute_cycle()
    assert isinstance(result, dict)
    assert "sells" in result and "buys" in result
    assert "cash" in result and "value" in result
    assert result["sells"] == 0 and result["buys"] == 0  # 단계 1: placeholder


def test_shadow_trader_uses_injected_dependencies():
    """주입한 provider/executor 인스턴스를 그대로 보관하는지."""
    db = MagicMock()
    custom_provider = MagicMock(spec=PriceProvider)
    custom_executor = MagicMock(spec=OrderExecutor)
    trader = ShadowTrader(db, custom_provider, custom_executor)
    assert trader.price_provider is custom_provider
    assert trader.order_executor is custom_executor
    assert trader.db is db

"""_cmd_portfolio: stoploss_map + previous_prices 전달 검증."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _reload_settings():
    import importlib
    import config.settings as settings_mod
    importlib.reload(settings_mod)
    return settings_mod


@pytest.fixture
def bot_with_db():
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_CHAT_ID": "999",
        "TELEGRAM_ERROR_CHAT_ID": "888",
    }, clear=False):
        _reload_settings()
        import importlib
        import bot.telegram_bot as bot_mod
        importlib.reload(bot_mod)
        from bot.telegram_bot import KOSPIBot
        db = MagicMock()
        bot = KOSPIBot(db)
        yield bot, db


def _update_context(args=None):
    update = MagicMock()
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = args or []
    return update, context


@pytest.mark.asyncio
async def test_cmd_portfolio_includes_stoploss(bot_with_db):
    """현재 stock_score에 stoploss_price>0 → 손절 라인 출력."""
    bot, db = bot_with_db
    db.get_portfolio.return_value = [{
        "stock_code": "005930", "stock_name": "삼성전자",
        "avg_buy_price": 70000, "total_quantity": 10,
        "total_invested": 700000, "buy_count": 1,
        "lots": [{"buy_date": "2026-01-01", "buy_price": 70000, "quantity": 10}],
    }]
    db.get_stock_score.return_value = {
        "current_price": 75000, "signal_label": "⭐", "total_score": 50,
        "stoploss_price": 65100, "stoploss_pct": -7.0,
    }
    db.get_previous_price.return_value = 74500

    update, context = _update_context()
    await bot._cmd_portfolio(update, context)

    msg = update.message.reply_text.await_args.args[0]
    # 손절 라인 포함
    assert "│ 손절: 65,100원" in msg
    # 전일대비 라인 포함
    assert "│ 전일대비: +500원" in msg


@pytest.mark.asyncio
async def test_cmd_portfolio_falls_back_to_history_when_score_stoploss_zero(bot_with_db):
    """현재 행 stoploss=0(자정 모니터 silent zero) → history에서 직전 양수값 폴백."""
    bot, db = bot_with_db
    db.get_portfolio.return_value = [{
        "stock_code": "005930", "stock_name": "삼성전자",
        "avg_buy_price": 70000, "total_quantity": 10,
        "total_invested": 700000, "buy_count": 1,
        "lots": [{"buy_date": "2026-01-01", "buy_price": 70000, "quantity": 10}],
    }]
    db.get_stock_score.return_value = {
        "current_price": 75000, "signal_label": "⭐", "total_score": 50,
        "stoploss_price": 0, "stoploss_pct": 0,  # 자정 모니터 잔재
    }
    db.get_stock_history.return_value = [
        {"analysis_date": "2026-05-01", "stoploss_price": 0, "stoploss_pct": 0},
        {"analysis_date": "2026-04-30", "stoploss_price": 65100, "stoploss_pct": -7.0},
    ]
    db.get_previous_price.return_value = 74500

    update, context = _update_context()
    await bot._cmd_portfolio(update, context)

    msg = update.message.reply_text.await_args.args[0]
    # 직전 영업일 손절가가 표시되어야 함
    assert "│ 손절: 65,100원" in msg


@pytest.mark.asyncio
async def test_cmd_portfolio_no_stoploss_line_when_unavailable(bot_with_db):
    """stock_score 없음 + history에도 stoploss 없음 → 손절 라인 미출력."""
    bot, db = bot_with_db
    db.get_portfolio.return_value = [{
        "stock_code": "005930", "stock_name": "삼성전자",
        "avg_buy_price": 70000, "total_quantity": 10,
        "total_invested": 700000, "buy_count": 1,
        "lots": [{"buy_date": "2026-01-01", "buy_price": 70000, "quantity": 10}],
    }]
    db.get_stock_score.return_value = {
        "current_price": 75000, "signal_label": "⭐", "total_score": 50,
        "stoploss_price": 0, "stoploss_pct": 0,
    }
    db.get_stock_history.return_value = []
    db.get_previous_price.return_value = 0

    update, context = _update_context()
    await bot._cmd_portfolio(update, context)

    msg = update.message.reply_text.await_args.args[0]
    assert "손절:" not in msg

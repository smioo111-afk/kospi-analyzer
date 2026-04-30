"""Tests for send_daily_report → format_portfolio_for_report previous_prices wiring."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models import Database  # noqa: E402


@pytest.fixture
def telegram_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    monkeypatch.setenv("TELEGRAM_ERROR_CHAT_ID", "888")
    import importlib
    import config.settings as settings_mod
    importlib.reload(settings_mod)
    import bot.telegram_bot as bot_mod
    importlib.reload(bot_mod)
    return bot_mod


def _seed_portfolio(db: Database, code: str = "005930") -> None:
    conn = db._get_conn()
    conn.execute(
        """INSERT INTO portfolio
           (stock_code, stock_name, buy_price, quantity, buy_date, is_sold)
           VALUES (?, ?, ?, ?, ?, 0)""",
        (code, "삼성전자", 70000, 10, "2026-01-01"),
    )
    conn.execute(
        """INSERT OR REPLACE INTO stock_scores
           (analysis_date, stock_code, current_price)
           VALUES (?, ?, ?)""",
        ("2026-04-30", code, 74500),
    )
    conn.commit()


@pytest.mark.asyncio
async def test_send_daily_report_uses_explicit_previous_prices(
    tmp_path, telegram_env,
):
    """previous_prices 명시 전달 시 포트폴리오 메시지에 전일 대비 라인 포함."""
    db_path = tmp_path / "send_report.db"
    db = Database(db_path=str(db_path))
    _seed_portfolio(db)

    bot_mod = telegram_env
    bot = bot_mod.KOSPIBot(db)

    fake_send = MagicMock()
    fake_send.send_message = AsyncMock()
    with patch("telegram.Bot", return_value=fake_send):
        await bot.send_daily_report(
            top_10=[{
                "stock_code": "005930", "stock_name": "삼성전자",
                "total_score": 70, "signal": "buy",
                "signal_label": "BUY", "current_price": 75000,
                "per": 12.0, "pbr": 1.1, "roe": 8.5,
            }],
            warnings=[], stats={"total_analyzed": 245, "after_filter": 200},
            portfolio_scores_map={
                "005930": {"current_price": 75000, "signal_label": "⭐", "total_score": 60},
            },
            previous_prices={"005930": 74500},
            disclosure_impacts=[],
        )
    db.close()

    sent_text = "\n".join(
        c.kwargs.get("text", "")
        for c in fake_send.send_message.await_args_list
    )
    # 전일대비 라인이 포트폴리오 섹션에 포함되어야 함
    assert "전일대비:" in sent_text
    assert "+500원" in sent_text  # 75000 - 74500


@pytest.mark.asyncio
async def test_send_daily_report_falls_back_to_db_lookup(
    tmp_path, telegram_env,
):
    """previous_prices 미지정 시 db.get_previous_price로 자동 조회."""
    db_path = tmp_path / "fallback.db"
    db = Database(db_path=str(db_path))
    _seed_portfolio(db)

    bot_mod = telegram_env
    bot = bot_mod.KOSPIBot(db)

    fake_send = MagicMock()
    fake_send.send_message = AsyncMock()
    with patch("telegram.Bot", return_value=fake_send):
        await bot.send_daily_report(
            top_10=[{
                "stock_code": "005930", "stock_name": "삼성전자",
                "total_score": 70, "signal": "buy",
                "signal_label": "BUY", "current_price": 75000,
                "per": 12.0, "pbr": 1.1, "roe": 8.5,
            }],
            warnings=[], stats={"total_analyzed": 245, "after_filter": 200},
            portfolio_scores_map={
                "005930": {"current_price": 75000, "signal_label": "⭐", "total_score": 60},
            },
            disclosure_impacts=[],
            # previous_prices 미지정 → DB에서 자동 조회
        )
    db.close()

    sent_text = "\n".join(
        c.kwargs.get("text", "")
        for c in fake_send.send_message.await_args_list
    )
    # stock_scores에 2026-04-30 74,500이 시드되어 있으므로 전일대비 라인이 표시됨
    assert "전일대비:" in sent_text
    assert "+500원" in sent_text

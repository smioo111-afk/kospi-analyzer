"""D2: 봇 명령어 본문 + 헬스 알림 통합 테스트.

각 명령어 핸들러를 직접 호출해 reply_text가 의도한 형식으로 발송
되는지 검증한다. 텔레그램 SDK는 mock해서 실제 네트워크는 타지 않는다.
chat_id 권한 검증은 test_bot_auth.py에 이미 있음.
"""

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
    """env + bot 재로드 후 mock DB가 주입된 KOSPIBot 인스턴스."""
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


def _make_update_context(args=None):
    update = MagicMock()
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = args or []
    return update, context


# ----------------------------------------------------------------------
# /start 와 /help: 정적 메시지 발송
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cmd_start_replies(bot_with_db):
    bot, _ = bot_with_db
    update, context = _make_update_context()
    await bot._cmd_start(update, context)
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert text  # 빈 메시지 아님


@pytest.mark.asyncio
async def test_cmd_help_lists_commands(bot_with_db):
    bot, _ = bot_with_db
    update, context = _make_update_context()
    await bot._cmd_help(update, context)
    text = update.message.reply_text.await_args.args[0]
    # 주요 명령어가 도움말에 노출돼야
    for cmd in ("/report", "/stock", "/portfolio", "/buy", "/sell"):
        assert cmd in text


# ----------------------------------------------------------------------
# /report: 분석 결과 없음 → 안내 메시지
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cmd_report_when_no_data(bot_with_db):
    bot, db = bot_with_db
    db.get_latest_result.return_value = None
    update, context = _make_update_context()
    await bot._cmd_report(update, context)
    text = update.message.reply_text.await_args.args[0]
    assert "분석 결과" in text


@pytest.mark.asyncio
async def test_cmd_report_with_data_invokes_formatter(bot_with_db):
    bot, db = bot_with_db
    db.get_latest_result.return_value = {
        "top_10": [{"stock_code": "005930", "stock_name": "삼성전자",
                    "total_score": 85, "signal_label": "STRONG_BUY"}],
        "warnings": [],
        "stats": {},
        "kospi_index": 6500.0,
    }
    update, context = _make_update_context()
    with patch.object(bot.formatter, "format_daily_report",
                      return_value=["msg1"]) as fmt:
        await bot._cmd_report(update, context)
    fmt.assert_called_once()
    update.message.reply_text.assert_awaited_with("msg1")


# ----------------------------------------------------------------------
# /stock: 인자 없음 → 사용법 안내, 못 찾음 → 에러 메시지
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cmd_stock_without_args(bot_with_db):
    bot, _ = bot_with_db
    update, context = _make_update_context(args=[])
    await bot._cmd_stock(update, context)
    text = update.message.reply_text.await_args.args[0]
    assert "/stock" in text
    assert "005930" in text  # 사용 예시


@pytest.mark.asyncio
async def test_cmd_stock_when_score_missing(bot_with_db):
    bot, db = bot_with_db
    db.get_stock_score.return_value = None
    db.get_stock_master.return_value = None
    db.search_stock_by_name.return_value = []
    update, context = _make_update_context(args=["005930"])
    with patch.object(
        bot, "_resolve_stock_code", new=AsyncMock(return_value="005930"),
    ):
        await bot._cmd_stock(update, context)
    text = update.message.reply_text.await_args.args[0]
    assert "찾을 수 없" in text


# ----------------------------------------------------------------------
# /history: 최근 7일 이력
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cmd_history_invokes_formatter(bot_with_db):
    bot, _ = bot_with_db
    bot.history = MagicMock()
    bot.history.get_recent_reports.return_value = []
    with patch.object(
        bot.formatter, "format_history_report",
        return_value="hist-text",
    ):
        update, context = _make_update_context()
        await bot._cmd_history(update, context)
    update.message.reply_text.assert_awaited_with("hist-text")


# ----------------------------------------------------------------------
# send_health_alert: 텔레그램으로 보고서 텍스트 전송
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_send_health_alert_sends_report_text(bot_with_db):
    bot, _ = bot_with_db
    report = MagicMock()
    report.format_text.return_value = "✅ health check OK"

    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()

    with patch("telegram.Bot", return_value=fake_bot):
        await bot.send_health_alert(report)

    fake_bot.send_message.assert_awaited_once()
    kwargs = fake_bot.send_message.await_args.kwargs
    assert "health check OK" in kwargs["text"]
    assert str(kwargs["chat_id"]) == "888"  # ERROR_CHAT_ID 우선


@pytest.mark.asyncio
async def test_send_health_alert_silent_without_token(bot_with_db):
    bot, _ = bot_with_db
    bot.cfg.BOT_TOKEN = ""
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    report = MagicMock()
    report.format_text.return_value = "x"
    with patch("telegram.Bot", return_value=fake_bot):
        await bot.send_health_alert(report)
    fake_bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_health_alert_swallows_format_error(bot_with_db):
    bot, _ = bot_with_db
    report = MagicMock()
    report.format_text.side_effect = ValueError("bad format")
    # 예외가 새 나오면 안됨
    await bot.send_health_alert(report)


# ----------------------------------------------------------------------
# 핸들러 등록 갯수 회귀 (D2 명세상 11개)
# ----------------------------------------------------------------------
def test_build_app_registers_at_least_11_command_handlers(bot_with_db):
    bot, _ = bot_with_db
    app = bot.build_app()
    from telegram.ext import CommandHandler
    handlers = [
        h for group in app.handlers.values() for h in group
        if isinstance(h, CommandHandler)
    ]
    assert len(handlers) >= 11


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

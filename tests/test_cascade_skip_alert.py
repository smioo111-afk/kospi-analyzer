"""bot.send_cascade_skip_alert 회귀 테스트.

cascade 안전장치(runtime/large-cap/circuit-breaker)가 발동하면
운영자에게 텔레그램 WARN을 발송한다. 본 테스트는 bot.send_message
호출 인자만 검증한다 (실제 텔레그램 호출 X).
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
def configured_bot():
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_CHAT_ID": "999",
        "TELEGRAM_ERROR_CHAT_ID": "888",
    }, clear=False):
        _reload_settings()
        # bot 모듈도 재로드해 새 settings를 잡게 한다
        import importlib
        import bot.telegram_bot as bot_mod
        importlib.reload(bot_mod)
        from bot.telegram_bot import KOSPIBot
        db = MagicMock()
        bot = KOSPIBot(db)
        yield bot


@pytest.mark.asyncio
async def test_cascade_skip_alert_sends_when_events(configured_bot):
    bot = configured_bot
    events = [
        {
            "stock_code": "111111",
            "stock_name": "TEST_A",
            "report_date": "2026-04-01",
            "consecutive_failures": 4,
            "last_exception": "RuntimeError",
            "reason": "RuntimeError safelist",
        }
    ]

    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()

    with patch("telegram.Bot", return_value=fake_bot):
        await bot.send_cascade_skip_alert(events)

    fake_bot.send_message.assert_awaited_once()
    kwargs = fake_bot.send_message.await_args.kwargs
    text = kwargs.get("text", "")
    assert "111111" in text
    assert "TEST_A" in text
    assert "RuntimeError" in text
    assert "4" in text  # consecutive_failures
    assert "888" == str(kwargs.get("chat_id"))  # ERROR_CHAT_ID 우선


@pytest.mark.asyncio
async def test_cascade_skip_alert_silent_when_no_events(configured_bot):
    bot = configured_bot
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    with patch("telegram.Bot", return_value=fake_bot):
        await bot.send_cascade_skip_alert([])
    fake_bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_cascade_skip_alert_truncates_at_10(configured_bot):
    bot = configured_bot
    events = [
        {
            "stock_code": f"{i:06d}",
            "stock_name": f"NAME{i}",
            "report_date": "2026-04-01",
            "consecutive_failures": 3,
            "last_exception": "ConnectionError",
            "reason": "circuit-breaker",
        }
        for i in range(15)
    ]
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    with patch("telegram.Bot", return_value=fake_bot):
        await bot.send_cascade_skip_alert(events)

    text = fake_bot.send_message.await_args.kwargs["text"]
    # 처음 10개만 보이고 마지막 5건은 ... 추가 메시지
    assert "000000" in text
    assert "000009" in text
    assert "000014" not in text
    assert "5건 추가" in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

"""봇 명령어 권한 검증 회귀 테스트.

CommandHandler에 filters.Chat(chat_id=allowed)을 적용해 화이트리스트
바깥 사용자는 모든 명령에서 차단되는지 확인한다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _reload_settings():
    """env 변경 후 TelegramConfig를 새 값으로 재로드."""
    import importlib
    import config.settings as settings_mod
    importlib.reload(settings_mod)
    return settings_mod


def test_allowed_chat_ids_uses_chat_id_when_unset():
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "x",
        "TELEGRAM_CHAT_ID": "123456",
        "TELEGRAM_ALLOWED_CHAT_IDS": "",
    }, clear=False):
        s = _reload_settings()
        assert s.TelegramConfig.allowed_chat_ids() == {"123456"}


def test_allowed_chat_ids_parses_comma_list():
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "x",
        "TELEGRAM_CHAT_ID": "123456",
        "TELEGRAM_ALLOWED_CHAT_IDS": "100, 200,300",
    }, clear=False):
        s = _reload_settings()
        assert s.TelegramConfig.allowed_chat_ids() == {"100", "200", "300"}


def test_allowed_chat_ids_empty_when_no_chat_id():
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "x",
        "TELEGRAM_CHAT_ID": "",
        "TELEGRAM_ALLOWED_CHAT_IDS": "",
    }, clear=False):
        s = _reload_settings()
        assert s.TelegramConfig.allowed_chat_ids() == set()


def test_build_app_attaches_chat_filter_to_all_handlers():
    """build_app가 모든 CommandHandler에 chat_filter를 적용해야 함."""
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "fake-token",
        "TELEGRAM_CHAT_ID": "999",
        "TELEGRAM_ALLOWED_CHAT_IDS": "",
    }, clear=False):
        _reload_settings()
        # 봇 모듈도 재로드 (설정 캐시)
        import importlib
        import bot.telegram_bot as bot_mod
        importlib.reload(bot_mod)

        # DB는 mock
        bot = bot_mod.KOSPIBot(db=MagicMock())
        app = bot.build_app()

        # 등록된 모든 CommandHandler가 filters를 갖는지 확인
        from telegram.ext import CommandHandler
        from telegram.ext import filters as tg_filters

        handlers = [
            h for group in app.handlers.values() for h in group
            if isinstance(h, CommandHandler)
        ]
        assert len(handlers) >= 11, f"명령어 핸들러 11개+ 등록 필요, got {len(handlers)}"

        for h in handlers:
            f = h.filters
            assert f is not None, f"{h.commands} 핸들러에 filter 없음"
            # filters.Chat 또는 그것을 포함한 BaseFilter 결합이어야 함.
            # 직접 type 검사 대신 chat_id 속성으로 대체 검증.
            chats = getattr(f, "chat_ids", None)
            assert chats is not None or "Chat" in repr(f), (
                f"{h.commands}의 filter가 Chat 검증 없음: {f!r}"
            )


def test_build_app_uses_dummy_filter_when_no_allowed():
    """화이트리스트 비어있으면 fail-closed (모든 명령 차단)."""
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "fake-token",
        "TELEGRAM_CHAT_ID": "",
        "TELEGRAM_ALLOWED_CHAT_IDS": "",
    }, clear=False):
        _reload_settings()
        import importlib
        import bot.telegram_bot as bot_mod
        importlib.reload(bot_mod)
        bot = bot_mod.KOSPIBot(db=MagicMock())
        app = bot.build_app()  # 예외 X (build는 통과)

        from telegram.ext import CommandHandler
        handlers = [
            h for group in app.handlers.values() for h in group
            if isinstance(h, CommandHandler)
        ]
        # 더미 필터 -1로 모든 chat 차단
        for h in handlers:
            chat_ids = getattr(h.filters, "chat_ids", set())
            assert -1 in chat_ids or chat_ids == {-1}, (
                f"화이트리스트 비어있을 때 fail-closed 안 됨: {h.commands}"
            )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

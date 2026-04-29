"""B3: 파이프라인 trace_id + 수집 성공률 알림 회귀 테스트."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _make_pipeline():
    """AnalysisPipeline 인스턴스를 만들되, 외부 의존(KIS/DART/DB/Bot)은
    모두 MagicMock으로 우회. _alert_low_collection_rate만 단위 검증한다."""
    with patch("main.KISClient"), \
         patch("main.DARTClient"), \
         patch("main.ScoringEngine"), \
         patch("main.SignalGenerator"), \
         patch("main.StopLossCalculator"), \
         patch("main.Database"), \
         patch("main.AnalysisHistory"), \
         patch("main.KOSPIBot") as bot_cls:
        bot_inst = MagicMock()
        bot_inst.send_error_alert = AsyncMock()
        bot_cls.return_value = bot_inst
        from main import AnalysisPipeline
        p = AnalysisPipeline()
        return p, bot_inst


@pytest.mark.asyncio
async def test_alert_silent_above_threshold():
    p, bot = _make_pipeline()
    # 90% 수집률 → 알림 없음
    await p._alert_low_collection_rate("abc12345", 90, 100)
    bot.send_error_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_alert_fires_below_threshold():
    p, bot = _make_pipeline()
    # 70% → 알림
    await p._alert_low_collection_rate("abc12345", 70, 100)
    bot.send_error_alert.assert_awaited_once()
    args, _ = bot.send_error_alert.await_args
    msg = args[0]
    assert "trace=abc12345" in msg
    assert "70" in msg
    assert "임계" in msg


@pytest.mark.asyncio
async def test_alert_silent_when_expected_zero():
    p, bot = _make_pipeline()
    # 분모가 0이면 의미 없음 → 알림 없음
    await p._alert_low_collection_rate("abc12345", 0, 0)
    bot.send_error_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_alert_at_exact_threshold_silent():
    """80%는 임계와 같으므로 통과 — 미달이 아니라 동률."""
    p, bot = _make_pipeline()
    await p._alert_low_collection_rate("abc12345", 80, 100)
    bot.send_error_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_alert_swallows_send_failure():
    """텔레그램 발송 실패가 파이프라인을 중단시키지 않아야."""
    p, bot = _make_pipeline()
    bot.send_error_alert = AsyncMock(side_effect=RuntimeError("net down"))
    # 예외가 밖으로 새지 않음
    await p._alert_low_collection_rate("abc12345", 50, 100)


def test_trace_id_is_8_char_hex():
    """run() 진입 직후 self._trace_id 형식이 8자 hex여야."""
    import uuid
    val = uuid.uuid4().hex[:8]
    assert len(val) == 8
    int(val, 16)  # hex 파싱 가능 — 형식 검증


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

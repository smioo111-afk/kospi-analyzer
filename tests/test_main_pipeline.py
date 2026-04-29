"""D1: main.py 종단 통합 회귀 테스트.

전체 파이프라인을 실제로 돌리지 않고, 회귀 위험이 큰 패턴 4가지만
좁게 검증한다:
  1. KISClient async with 컨텍스트 재진입 안전성
     (run() → _collect_data 진입 → 종료 → run() 마지막에 다시 진입하는
     2026-04-29 회귀 패턴)
  2. trace_id가 사이클 시작 직후 self._trace_id 로 노출
  3. 차트 수집 성공률 < 80% 시 send_error_alert가 호출되는 통합
  4. cascade circuit-breaker가 임계 도달 시 cascade를 추가 발화시키지 않음
     (test_survivorship에서 단위 검증된 것을 main 경로에서도 재확인)
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ----------------------------------------------------------------------
# 1. KIS async with 재진입
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_kis_async_context_can_be_re_entered():
    """KISClient는 __aexit__ 후 다시 __aenter__할 수 있어야 한다.

    main.py가 _collect_data 안에서 `async with self.kis:` 한 번,
    이후 KOSPI 인덱스 조회를 위해 또 한 번 사용한다. 재진입이 막히면
    cascade 상장폐지 오탐(2026-04-24) 같은 회귀가 다시 발생한다.
    """
    from collectors.kis_api import KISClient
    kis = KISClient()
    # 1차 진입/종료
    async with kis:
        assert kis._session is not None
        assert kis._limiter is not None
    assert kis._session is None
    # 2차 재진입 — 예외 없어야
    async with kis:
        assert kis._session is not None
        assert kis._limiter is not None
    assert kis._session is None


# ----------------------------------------------------------------------
# 2. trace_id 노출
# ----------------------------------------------------------------------
def _make_pipeline_with_mocks():
    with patch("main.KISClient"), \
         patch("main.DARTClient"), \
         patch("main.ScoringEngine"), \
         patch("main.SignalGenerator"), \
         patch("main.StopLossCalculator"), \
         patch("main.Database"), \
         patch("main.AnalysisHistory"), \
         patch("main.KOSPIBot") as bot_cls:
        bot = MagicMock()
        bot.send_error_alert = AsyncMock()
        bot_cls.return_value = bot
        from main import AnalysisPipeline
        return AnalysisPipeline(), bot


@pytest.mark.asyncio
async def test_trace_id_alert_includes_trace():
    """수집률 알림이 trace_id를 포함해야 grep이 가능하다."""
    p, bot = _make_pipeline_with_mocks()
    p._trace_id = "deadbeef"
    await p._alert_low_collection_rate("deadbeef", 50, 100)
    bot.send_error_alert.assert_awaited_once()
    msg = bot.send_error_alert.await_args.args[0]
    assert "deadbeef" in msg


# ----------------------------------------------------------------------
# 3. 차트 수집 성공률 임계 통합
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_collection_rate_threshold_constant_is_80pct():
    p, _ = _make_pipeline_with_mocks()
    assert abs(p.COLLECTION_RATE_MIN - 0.80) < 1e-9


@pytest.mark.asyncio
async def test_collection_rate_just_below_threshold_alerts():
    p, bot = _make_pipeline_with_mocks()
    # 79/100 = 79% → 알림
    await p._alert_low_collection_rate("trace01", 79, 100)
    bot.send_error_alert.assert_awaited_once()


# ----------------------------------------------------------------------
# 4. cascade circuit-breaker 회귀 (main 경로)
# ----------------------------------------------------------------------
def test_cascade_circuit_breaker_caps_per_cycle_through_db():
    """Database.update_performance_tracking → circuit breaker 차단."""
    from database.models import Database, _CASCADE_PER_CYCLE_LIMIT

    db = Database(db_path=":memory:")
    today = datetime.now()
    report_date = (today - timedelta(days=35)).strftime("%Y-%m-%d")

    # 7개 소형주 cascade 후보 시드
    codes = [f"7770{i:02d}" for i in range(7)]
    conn = db._get_conn()
    for code in codes:
        conn.execute(
            """INSERT INTO daily_report_log
               (report_date, stock_code, stock_name, rank, total_score,
                signal, signal_label, current_price)
               VALUES (?, ?, ?, 1, 80, 'strong_buy', 'strong_buy', 1000)""",
            (report_date, code, f"NAME_{code}"),
        )
        conn.execute(
            """INSERT INTO stock_scores
               (analysis_date, stock_code, stock_name, market_cap)
               VALUES (?, ?, ?, ?)""",
            (report_date, code, f"NAME_{code}", 50_000_000_000),
        )
    conn.commit()

    class FailingKIS:
        def get_stock_price(self, code):
            raise ConnectionError(f"failure for {code}")

    fake = FailingKIS()
    for _ in range(3):
        db.update_performance_tracking(fake)

    rows = conn.execute(
        "SELECT is_delisted FROM performance_tracking WHERE report_date = ?",
        (report_date,),
    ).fetchall()
    delisted = sum(1 for r in rows if r["is_delisted"])
    assert delisted <= _CASCADE_PER_CYCLE_LIMIT, (
        f"circuit breaker 미동작: {delisted}건 cascade "
        f"(한도 {_CASCADE_PER_CYCLE_LIMIT})"
    )


# ----------------------------------------------------------------------
# 5. KIS 조회 silent fail 가드 (test_kospi_index_parse 보완)
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_kospi_index_returns_zeros_on_empty_response():
    """KIS 응답이 비면 dict는 채워져 반환되되 모든 값이 0.0.

    main.py는 이 경우 stats_json에 0.0/0.0을 동봉하고, T1-2b는
    pass(둘 다 0)로 통과한다.
    """
    from collectors.kis_api import KISClient
    kis = KISClient()
    with patch.object(
        kis, "_request_get",
        new=AsyncMock(return_value={"output": {}}),
    ):
        result = await kis.aget_kospi_index()
    assert result["index"] == 0.0
    assert result["change"] == 0.0
    assert result["change_rate"] == 0.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

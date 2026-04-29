"""A1 Phase 4: daily_disclosure_monitor + send_daily_report 통합 테스트."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.disclosure_impact import (  # noqa: E402
    DisclosureImpact,
    ScoreSnapshot,
)
from collectors.dart_disclosure import Disclosure  # noqa: E402
from database.models import Database  # noqa: E402


# ----------------------------------------------------------------------
# 빌더
# ----------------------------------------------------------------------
def _disc(stock_code="004800", report_nm="사업보고서") -> Disclosure:
    return Disclosure(
        rcept_no="20260428001",
        corp_code="00111111",
        stock_code=stock_code,
        corp_name="효성",
        report_nm=report_nm,
        rcept_dt="20260428",
        rm="",
    )


def _snap(code="004800", total=55, signal="hold") -> ScoreSnapshot:
    return ScoreSnapshot(
        stock_code=code, stock_name="효성",
        total_score=total, value_score=15, financial_score=10,
        growth_score=10, momentum_score=12, quality_score=8,
        signal=signal,
    )


def _impact(code="004800", before=55, after=62) -> DisclosureImpact:
    return DisclosureImpact(
        disclosure=_disc(code),
        stock_code=code,
        before=_snap(code, before), after=_snap(code, after),
        total_diff=after - before,
        value_diff=3, financial_diff=2,
        growth_diff=2, momentum_diff=0, quality_diff=0,
        signal_changed=False,
    )


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


# ----------------------------------------------------------------------
# daily_disclosure_monitor
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_monitor_no_disclosures_skips_processing(tmp_path, monkeypatch):
    """어제 공시 0건이면 process_disclosures 호출 없이 종료."""
    monkeypatch.setattr(
        "config.settings.DBConfig.DB_PATH", str(tmp_path / "m1.db"),
    )
    import importlib
    import database.models as models_mod
    importlib.reload(models_mod)
    import main as main_mod
    importlib.reload(main_mod)

    with patch("collectors.dart_disclosure.fetch_disclosures",
               return_value=[]), \
         patch("analysis.disclosure_impact.process_disclosures") as proc, \
         patch("collectors.dart_api.DARTClient"), \
         patch("analysis.scorer.ScoringEngine"), \
         patch.object(main_mod, "KOSPIBot"):
        await main_mod.daily_disclosure_monitor()
    proc.assert_not_called()


@pytest.mark.asyncio
async def test_monitor_year_uses_yesterday_minus_one(tmp_path, monkeypatch):
    """H1: process_disclosures에 전달되는 year는 어제.year - 1.

    financial_metrics는 직전 사업연도 기준으로 저장되므로, 모니터가
    yesterday.year를 그대로 넘기면 미래 연도의 보고서를 요구하게 된다.
    """
    monkeypatch.setattr(
        "config.settings.DBConfig.DB_PATH", str(tmp_path / "year.db"),
    )
    import importlib
    import database.models as models_mod
    importlib.reload(models_mod)
    seed = models_mod.Database(db_path=str(tmp_path / "year.db"))
    seed.save_financial_metrics({
        "stock_code": "004800", "year": 2025, "quarter": "annual",
    })
    seed.close()
    import main as main_mod
    importlib.reload(main_mod)

    from datetime import date
    yesterday = date.today() - __import__("datetime").timedelta(days=1)
    expected_year = yesterday.year - 1

    captured = {}

    def capture_year(**kwargs):
        captured["year"] = kwargs.get("year")
        return []

    with patch("collectors.dart_disclosure.fetch_disclosures",
               return_value=[_disc("004800", "[기재정정]사업보고서")]), \
         patch("analysis.disclosure_impact.process_disclosures",
               side_effect=capture_year), \
         patch("collectors.dart_api.DARTClient"), \
         patch("analysis.scorer.ScoringEngine"), \
         patch.object(main_mod, "KOSPIBot"):
        await main_mod.daily_disclosure_monitor()

    assert captured["year"] == expected_year, (
        f"year 인자가 {captured['year']}, 기대 {expected_year} "
        f"(yesterday.year - 1)"
    )


@pytest.mark.asyncio
async def test_monitor_processes_and_saves(tmp_path, monkeypatch):
    """공시 발견 시 process_disclosures + save_disclosure_impacts_batch 호출."""
    db_path = tmp_path / "m2.db"
    monkeypatch.setattr(
        "config.settings.DBConfig.DB_PATH", str(db_path),
    )
    import importlib
    import database.models as models_mod
    importlib.reload(models_mod)
    # financial_metrics에 분석 종목 1건 시드 (analyzed_codes 추출용)
    seed_db = models_mod.Database(db_path=str(db_path))
    seed_db.save_financial_metrics({
        "stock_code": "004800", "year": 2025, "quarter": "annual",
    })
    seed_db.close()

    import main as main_mod
    importlib.reload(main_mod)

    fake_discs = [_disc("004800", "[기재정정]사업보고서")]
    fake_impacts = [_impact("004800", 55, 62)]

    with patch("collectors.dart_disclosure.fetch_disclosures",
               return_value=fake_discs), \
         patch("analysis.disclosure_impact.process_disclosures",
               return_value=fake_impacts), \
         patch("collectors.dart_api.DARTClient"), \
         patch("analysis.scorer.ScoringEngine"), \
         patch.object(main_mod, "KOSPIBot"):
        await main_mod.daily_disclosure_monitor()

    # DB에 오늘자 행 저장 확인
    today_str = date.today().strftime("%Y-%m-%d")
    db_check = models_mod.Database(db_path=str(db_path))
    try:
        rows = db_check.get_disclosure_impacts(today_str)
    finally:
        db_check.close()
    assert len(rows) == 1
    assert rows[0].stock_code == "004800"


@pytest.mark.asyncio
async def test_monitor_handles_dart_failure_gracefully(
    tmp_path, monkeypatch,
):
    """DART 조회 예외 시 send_error_alert 후 graceful 종료."""
    monkeypatch.setattr(
        "config.settings.DBConfig.DB_PATH", str(tmp_path / "m3.db"),
    )
    import importlib
    import database.models as models_mod
    importlib.reload(models_mod)
    import main as main_mod
    importlib.reload(main_mod)

    fake_bot = MagicMock()
    fake_bot.send_error_alert = AsyncMock()
    bot_cls = MagicMock(return_value=fake_bot)
    with patch("collectors.dart_disclosure.fetch_disclosures",
               side_effect=RuntimeError("DART 503")), \
         patch("collectors.dart_api.DARTClient"), \
         patch("analysis.scorer.ScoringEngine"), \
         patch.object(main_mod, "KOSPIBot", new=bot_cls):
        # 예외가 밖으로 새 나오면 안됨
        await main_mod.daily_disclosure_monitor()
    fake_bot.send_error_alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_monitor_handles_process_failure_gracefully(
    tmp_path, monkeypatch,
):
    """process_disclosures 예외도 send_error_alert + graceful 종료."""
    monkeypatch.setattr(
        "config.settings.DBConfig.DB_PATH", str(tmp_path / "m4.db"),
    )
    import importlib
    import database.models as models_mod
    importlib.reload(models_mod)
    import main as main_mod
    importlib.reload(main_mod)

    fake_bot = MagicMock()
    fake_bot.send_error_alert = AsyncMock()
    with patch("collectors.dart_disclosure.fetch_disclosures",
               return_value=[_disc()]), \
         patch("analysis.disclosure_impact.process_disclosures",
               side_effect=RuntimeError("scorer down")), \
         patch("collectors.dart_api.DARTClient"), \
         patch("analysis.scorer.ScoringEngine"), \
         patch.object(main_mod, "KOSPIBot", return_value=fake_bot):
        await main_mod.daily_disclosure_monitor()
    fake_bot.send_error_alert.assert_awaited_once()


# ----------------------------------------------------------------------
# send_daily_report 통합: 자동 로드
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_send_daily_report_auto_loads_disclosure_impacts(
    tmp_path, monkeypatch, telegram_env,
):
    """disclosure_impacts 인자 미지정 시 오늘자 행을 DB에서 자동 로드."""
    db_path = tmp_path / "auto.db"
    db = Database(db_path=str(db_path))
    today_str = date.today().strftime("%Y-%m-%d")
    db.save_disclosure_impact(today_str, _impact("004800", 55, 62))

    bot_mod = telegram_env
    bot = bot_mod.KOSPIBot(db)

    fake_send = MagicMock()
    fake_send.send_message = AsyncMock()
    with patch("telegram.Bot", return_value=fake_send):
        await bot.send_daily_report(
            top_10=[{
                "stock_code": "005930", "stock_name": "삼성전자",
                "total_score": 80, "signal": "strong_buy",
                "signal_label": "STRONG_BUY", "current_price": 70000,
                "per": 12.0, "pbr": 1.1, "roe": 8.5,
            }],
            warnings=[], stats={"total_analyzed": 245, "after_filter": 200},
        )

    db.close()
    # 보낸 메시지에 효성/공시 영향 변화 섹션 포함
    sent_text = "\n".join(
        c.kwargs.get("text", "")
        for c in fake_send.send_message.await_args_list
    )
    assert "공시 영향 변화" in sent_text
    assert "효성" in sent_text


@pytest.mark.asyncio
async def test_send_daily_report_explicit_disclosure_impacts_override(
    tmp_path, telegram_env,
):
    """disclosure_impacts 명시 전달 시 DB에서 자동 로드 안 함."""
    db_path = tmp_path / "override.db"
    db = Database(db_path=str(db_path))
    today_str = date.today().strftime("%Y-%m-%d")
    # DB에는 효성 1건이 있지만, 호출자가 빈 리스트를 명시 전달
    db.save_disclosure_impact(today_str, _impact("004800", 55, 62))

    bot_mod = telegram_env
    bot = bot_mod.KOSPIBot(db)

    fake_send = MagicMock()
    fake_send.send_message = AsyncMock()
    with patch("telegram.Bot", return_value=fake_send):
        await bot.send_daily_report(
            top_10=[{
                "stock_code": "005930", "stock_name": "삼성전자",
                "total_score": 80, "signal": "strong_buy",
                "signal_label": "STRONG_BUY", "current_price": 70000,
                "per": 12.0, "pbr": 1.1, "roe": 8.5,
            }],
            warnings=[], stats={"total_analyzed": 245, "after_filter": 200},
            disclosure_impacts=[],  # 명시 빈 리스트
        )
    db.close()
    sent_text = "\n".join(
        c.kwargs.get("text", "")
        for c in fake_send.send_message.await_args_list
    )
    # DB에 효성이 있어도 빈 리스트가 명시되어 섹션 표시 안 됨
    assert "공시 영향 변화" not in sent_text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

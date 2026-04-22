"""
일회성 샘플 임계치 알림 (performance_tracking 30건 도달 시 Telegram 발송).

발송 완료 후에는 data/.sample_notified 플래그로 재발송을 막고,
운영자는 docs/sample_notifier_cleanup.md 절차에 따라 이 스크립트와
main.py의 스케줄 등록을 제거한다.

주요 경로:
  - DB: config.settings.DBConfig.DB_PATH (기본 data/kospi_analyzer.db)
  - 플래그: data/.sample_notified
  - Telegram: config.settings.TelegramConfig.BOT_TOKEN / CHAT_ID

실행:
  python -m tools.sample_threshold_notifier          # 임계치 평가 후 필요 시 발송
  python -m tools.sample_threshold_notifier --force  # 플래그 무시하고 발송
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Optional

from config.settings import DBConfig, TelegramConfig

logger = logging.getLogger(__name__)

SAMPLE_THRESHOLD = 30
FLAG_PATH = Path("data/.sample_notified")


def _count_samples(db_path: str) -> dict[str, int]:
    """신호별 유효 샘플 수 집계.

    유효 조건: return_1m != 0 (미계산 레코드 제외).
    상장폐지(is_delisted=1)도 포함 (-100%는 유효한 결과).
    """
    conn = sqlite3.connect(db_path)
    try:
        counts: dict[str, int] = {
            "strong_buy": 0, "buy": 0, "hold": 0, "sell": 0, "other": 0,
            "total": 0,
        }
        rows = conn.execute(
            """SELECT COALESCE(signal_at_report, 'other') AS sig,
                      COUNT(*) AS n
               FROM performance_tracking
               WHERE return_1m != 0
               GROUP BY signal_at_report""",
        ).fetchall()
        for sig, n in rows:
            key = sig if sig in counts else "other"
            counts[key] += n
            counts["total"] += n
        return counts
    finally:
        conn.close()


def _format_message(counts: dict[str, int]) -> str:
    return (
        "📊 성과 추적 샘플 30개 도달\n"
        f"- 1m 유효 샘플: {counts['total']}개\n"
        f"- strong_buy: {counts['strong_buy']}, "
        f"buy: {counts['buy']}, "
        f"hold: {counts['hold']}, "
        f"sell: {counts['sell']}\n"
        "- 다음 단계: tools/analyze_performance.py 구현 착수 가능"
    )


async def _send_telegram(message: str) -> None:
    """bot/telegram_bot.py의 send 패턴과 동일하게 메시지 발송."""
    # Lazy import: telegram은 무거운 의존성
    from telegram import Bot
    bot = Bot(token=TelegramConfig.BOT_TOKEN)
    await bot.send_message(chat_id=TelegramConfig.CHAT_ID, text=message)


async def run(
    db_path: Optional[str] = None,
    flag_path: Optional[Path] = None,
    sender=_send_telegram,
    force: bool = False,
) -> str:
    """한 번 실행. 상태 문자열 반환.

    가능한 반환값:
      - "skipped_flag": 플래그 파일 있음, 조용히 종료
      - "below_threshold": 샘플 부족
      - "sent": 알림 발송 + 플래그 생성
      - "send_failed": 발송 실패 (플래그 생성 안 함)
    """
    db_path = db_path or DBConfig.DB_PATH
    flag_path = flag_path or FLAG_PATH

    if not force and flag_path.exists():
        logger.info("이미 알림 발송됨 (플래그: %s). 스킵.", flag_path)
        return "skipped_flag"

    counts = _count_samples(db_path)
    logger.info("유효 샘플: %s", counts)

    if counts["total"] < SAMPLE_THRESHOLD:
        logger.info(
            "샘플 %d < 임계치 %d. 발송 안 함.",
            counts["total"], SAMPLE_THRESHOLD,
        )
        return "below_threshold"

    msg = _format_message(counts)
    try:
        await sender(msg)
    except Exception as e:
        logger.error("텔레그램 발송 실패: %s", e)
        return "send_failed"

    # 플래그 생성
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_text(
        f"samples={counts['total']}\n"
        f"strong_buy={counts['strong_buy']} buy={counts['buy']} "
        f"hold={counts['hold']} sell={counts['sell']}\n",
        encoding="utf-8",
    )
    logger.info("알림 발송 완료 + 플래그 생성: %s", flag_path)
    return "sent"


async def scheduled_job() -> None:
    """APScheduler에서 호출. 예외 전파 안 함."""
    try:
        await run()
    except Exception as e:
        logger.error("샘플 임계치 알림 잡 실패: %s", e)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="플래그 무시하고 발송")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    status = asyncio.run(run(force=args.force))
    print(f"status: {status}")
    return 0 if status in ("sent", "skipped_flag", "below_threshold") else 1


if __name__ == "__main__":
    raise SystemExit(main())

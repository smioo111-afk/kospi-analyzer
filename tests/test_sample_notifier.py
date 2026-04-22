"""
샘플 임계치 알림 테스트.

실행: pytest tests/test_sample_notifier.py -v
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import Database
from tools import sample_threshold_notifier as stn


def _seed(db: Database, rows: list[tuple[str, float]]) -> None:
    """performance_tracking에 (signal, return_1m) 쌍을 삽입."""
    conn = db._get_conn()
    for i, (sig, ret) in enumerate(rows):
        code = f"{i:06d}"
        conn.execute(
            """INSERT INTO performance_tracking
               (report_date, stock_code, stock_name, signal_at_report,
                score_at_report, price_at_report, return_1m, last_updated)
               VALUES ('2026-04-01', ?, ?, ?, 80, 10000, ?, '2026-04-22')""",
            (code, f"N_{code}", sig, ret),
        )
    conn.commit()


def _mk_db(tmp_path) -> Database:
    """tmp_path의 파일 SQLite로 Database 생성.

    :memory:는 connection마다 분리된 공간이라 다른 프로세스/커넥션에서
    같은 테이블을 볼 수 없다. notifier는 별도 connection을 쓰므로 파일 사용.
    """
    return Database(db_path=str(tmp_path / "test.db"))


class _FakeSender:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail = False

    async def __call__(self, msg: str) -> None:
        if self.fail:
            raise RuntimeError("simulated telegram outage")
        self.calls.append(msg)


# ================================================================
# 1. 30 미만 → 발송 안 함
# ================================================================
@pytest.mark.asyncio
async def test_below_threshold_no_send(tmp_path):
    db = _mk_db(tmp_path)
    _seed(db, [("strong_buy", 1.2)] * 10 + [("buy", 0.5)] * 10)
    # 총 20건 (임계치 30 미만)

    sender = _FakeSender()
    flag = tmp_path / ".sample_notified"
    status = await stn.run(
        db_path=db.db_path,
        flag_path=flag,
        sender=sender,
    )
    assert status == "below_threshold"
    assert sender.calls == []
    assert not flag.exists()


# ================================================================
# 2. 30 이상 + 플래그 없음 → 발송 + 플래그 생성
# ================================================================
@pytest.mark.asyncio
async def test_threshold_reached_sends_and_creates_flag(tmp_path):
    db = _mk_db(tmp_path)
    _seed(
        db,
        [("strong_buy", 2.1)] * 15
        + [("buy", 1.0)] * 10
        + [("hold", 0.1)] * 5
        + [("sell", -1.5)] * 3,
    )
    # 총 33건

    sender = _FakeSender()
    flag = tmp_path / ".sample_notified"
    status = await stn.run(
        db_path=db.db_path,
        flag_path=flag,
        sender=sender,
    )
    assert status == "sent"
    assert len(sender.calls) == 1
    msg = sender.calls[0]
    assert "33개" in msg
    assert "strong_buy: 15" in msg
    assert "buy: 10" in msg
    assert "hold: 5" in msg
    assert "sell: 3" in msg
    assert flag.exists()
    assert "samples=33" in flag.read_text()


# ================================================================
# 3. 플래그 존재 → 조용히 종료
# ================================================================
@pytest.mark.asyncio
async def test_flag_exists_skipped(tmp_path):
    db = _mk_db(tmp_path)
    _seed(db, [("strong_buy", 1.0)] * 40)  # 임계치 이상이라도
    flag = tmp_path / ".sample_notified"
    flag.write_text("already_sent")

    sender = _FakeSender()
    status = await stn.run(
        db_path=db.db_path,
        flag_path=flag,
        sender=sender,
    )
    assert status == "skipped_flag"
    assert sender.calls == []


# ================================================================
# 4. 플래그 존재 + force → 재발송
# ================================================================
@pytest.mark.asyncio
async def test_force_overrides_flag(tmp_path):
    db = _mk_db(tmp_path)
    _seed(db, [("strong_buy", 1.0)] * 40)
    flag = tmp_path / ".sample_notified"
    flag.write_text("already_sent")

    sender = _FakeSender()
    status = await stn.run(
        db_path=db.db_path,
        flag_path=flag,
        sender=sender,
        force=True,
    )
    assert status == "sent"
    assert len(sender.calls) == 1


# ================================================================
# 5. 발송 실패 시 플래그 생성 안 함
# ================================================================
@pytest.mark.asyncio
async def test_send_failure_keeps_flag_absent(tmp_path):
    db = _mk_db(tmp_path)
    _seed(db, [("buy", 1.0)] * 35)
    sender = _FakeSender()
    sender.fail = True
    flag = tmp_path / ".sample_notified"

    status = await stn.run(
        db_path=db.db_path,
        flag_path=flag,
        sender=sender,
    )
    assert status == "send_failed"
    assert not flag.exists()
    # 다음 실행에서 다시 시도되어야 함 (재처리 가능)


# ================================================================
# 6. return_1m == 0 은 제외
# ================================================================
@pytest.mark.asyncio
async def test_uncomputed_rows_excluded(tmp_path):
    db = _mk_db(tmp_path)
    # 유효 20, 미계산 20 → 임계치 미달
    _seed(db, [("buy", 1.0)] * 20 + [("buy", 0.0)] * 20)
    sender = _FakeSender()
    flag = tmp_path / ".sample_notified"
    status = await stn.run(
        db_path=db.db_path,
        flag_path=flag,
        sender=sender,
    )
    assert status == "below_threshold"


# ================================================================
# 7. 메시지 포맷 검증
# ================================================================
def test_format_message():
    counts = {
        "strong_buy": 10, "buy": 8, "hold": 7, "sell": 5,
        "other": 0, "total": 30,
    }
    msg = stn._format_message(counts)
    assert "30개" in msg
    assert "strong_buy: 10" in msg
    assert "analyze_performance" in msg

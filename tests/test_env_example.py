"""SEC-5: config/.env.example 회귀 테스트.

- 파일이 존재한다.
- REQUIRED_ENV_VARS의 모든 키가 템플릿에 등장한다.
- 실제 자격증명 형태의 값이 들어있지 않다 (placeholder만).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import REQUIRED_ENV_VARS  # noqa: E402

ENV_EXAMPLE = ROOT / "config" / ".env.example"


def test_env_example_exists():
    assert ENV_EXAMPLE.exists(), f"{ENV_EXAMPLE} 가 생성되어 있어야 함"


def test_env_example_lists_all_required_keys():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    missing = [k for k, _ in REQUIRED_ENV_VARS if f"{k}=" not in text]
    assert not missing, (
        f".env.example에 누락된 필수 키: {missing}"
    )


def test_env_example_has_no_real_secret_pattern():
    """templace placeholder만 들어있어야 — 자격증명처럼 보이는 값 금지."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    # KIS app keys는 보통 알파벳/숫자 36자+. placeholder는 "replace-me"
    for line in text.splitlines():
        if line.startswith("KIS_APP_KEY="):
            val = line.split("=", 1)[1].strip()
            assert val == "replace-me", (
                f"KIS_APP_KEY 값이 placeholder가 아님: {val!r}"
            )
        if line.startswith("KIS_APP_SECRET="):
            val = line.split("=", 1)[1].strip()
            assert val == "replace-me", (
                f"KIS_APP_SECRET 값이 placeholder가 아님: {val!r}"
            )
        if line.startswith("DART_API_KEY="):
            val = line.split("=", 1)[1].strip()
            assert val == "replace-me", (
                f"DART_API_KEY 값이 placeholder가 아님: {val!r}"
            )
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            val = line.split("=", 1)[1].strip()
            # 실제 텔레그램 토큰은 "<bot_id>:<hash>" 형식
            assert ":" not in val, (
                f"TELEGRAM_BOT_TOKEN이 실제 토큰처럼 보임: {val!r}"
            )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

"""SEC-4: 필수 환경변수 fail-fast 검증 테스트."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import REQUIRED_ENV_VARS, validate_required_env  # noqa: E402


@pytest.fixture
def all_required_set(monkeypatch):
    """모든 필수 환경변수에 더미 값을 채워둔다."""
    for key, _ in REQUIRED_ENV_VARS:
        monkeypatch.setenv(key, f"dummy-{key}")
    return None


def test_all_required_env_passes(all_required_set):
    # 모든 키 채워져 있으면 예외 없이 통과
    validate_required_env()


def test_missing_one_required_env_raises(monkeypatch):
    for key, _ in REQUIRED_ENV_VARS:
        monkeypatch.setenv(key, f"dummy-{key}")
    monkeypatch.delenv("DART_API_KEY", raising=False)
    with pytest.raises(EnvironmentError) as exc:
        validate_required_env()
    assert "DART_API_KEY" in str(exc.value)


def test_missing_multiple_required_env_lists_all(monkeypatch):
    for key, _ in REQUIRED_ENV_VARS:
        monkeypatch.setenv(key, f"dummy-{key}")
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(EnvironmentError) as exc:
        validate_required_env()
    msg = str(exc.value)
    assert "KIS_APP_KEY" in msg
    assert "TELEGRAM_BOT_TOKEN" in msg


def test_whitespace_only_value_treated_as_missing(monkeypatch):
    for key, _ in REQUIRED_ENV_VARS:
        monkeypatch.setenv(key, f"dummy-{key}")
    monkeypatch.setenv("KIS_APP_SECRET", "   ")
    with pytest.raises(EnvironmentError) as exc:
        validate_required_env()
    assert "KIS_APP_SECRET" in str(exc.value)


def test_required_set_covers_all_critical_keys():
    # 회귀 차단: 필수 키 목록이 실수로 비거나 핵심 키가 빠지지 않게.
    keys = {k for k, _ in REQUIRED_ENV_VARS}
    expected = {
        "KIS_APP_KEY",
        "KIS_APP_SECRET",
        "DART_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    }
    assert expected.issubset(keys)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

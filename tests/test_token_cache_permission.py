"""토큰 캐시 파일 권한 회귀 테스트 (SEC-2).

`_save_token_to_cache`가 디렉토리 0o700, 파일 0o600 권한으로 만들어
다른 사용자가 access_token을 읽지 못하게 보장.
"""

from __future__ import annotations

import os
import stat
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.kis_api import KISTokenManager  # noqa: E402


def _make_manager(tmp_path: Path) -> KISTokenManager:
    mgr = KISTokenManager()
    mgr._token_cache_path = tmp_path / "tcache" / "kis_token.json"
    mgr._access_token = "fake-token-xyz123"
    mgr._token_expired_at = datetime.now() + timedelta(hours=24)
    return mgr


def test_token_cache_file_permission_600(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr._save_token_to_cache()

    assert mgr._token_cache_path.exists()
    mode = stat.S_IMODE(os.stat(mgr._token_cache_path).st_mode)
    assert mode == 0o600, f"파일 권한이 0o600이 아님: {oct(mode)}"


def test_token_cache_dir_permission_700(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr._save_token_to_cache()

    parent = mgr._token_cache_path.parent
    mode = stat.S_IMODE(os.stat(parent).st_mode)
    assert mode == 0o700, f"디렉토리 권한이 0o700이 아님: {oct(mode)}"


def test_token_cache_overwrites_existing_with_secure_perm(tmp_path):
    """기존 파일이 644여도 다시 저장 시 600으로 보정."""
    mgr = _make_manager(tmp_path)
    # 첫 저장
    mgr._save_token_to_cache()
    # 일부러 권한 644로 망가뜨림
    os.chmod(mgr._token_cache_path, 0o644)
    # 다시 저장 (토큰 갱신 흐름)
    mgr._access_token = "fake-token-renewed"
    mgr._save_token_to_cache()

    mode = stat.S_IMODE(os.stat(mgr._token_cache_path).st_mode)
    assert mode == 0o600


def test_token_cache_dir_perm_corrected_on_resave(tmp_path):
    """기존 디렉토리가 755여도 보정해야 함."""
    mgr = _make_manager(tmp_path)
    parent = mgr._token_cache_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    os.chmod(parent, 0o755)

    mgr._save_token_to_cache()

    mode = stat.S_IMODE(os.stat(parent).st_mode)
    assert mode == 0o700


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

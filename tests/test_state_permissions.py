from __future__ import annotations

import logging
import os
import stat
from logging.handlers import RotatingFileHandler

import httpx
import pytest
import structlog

from routelet import monitoring
from routelet.app import create_app
from routelet.cli.config_io import load_startup_config
from routelet.db import CallStore


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX permission modes")


@pytest.fixture(params=[0o000, 0o022])
def state_home(routelet_home, request):
    original_umask = os.umask(request.param)
    try:
        yield routelet_home
    finally:
        os.umask(original_umask)


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.parametrize("existing", [False, True])
async def test_config_env_and_dashboard_save_are_private(
    state_home, monkeypatch, existing
):
    config_path = state_home / "config.toml"
    env_path = state_home / ".env"
    if existing:
        state_home.mkdir(mode=0o755)
        config_path.write_text("[server]\nport = 9456\n", encoding="utf-8")
        env_path.write_text("ROUTELET_PERMISSION_TEST_KEY=example\n", encoding="utf-8")
        config_path.chmod(0o644)
        env_path.chmod(0o644)
        monkeypatch.delenv("ROUTELET_PERMISSION_TEST_KEY", raising=False)
    parent_mode = _mode(state_home.parent)
    config = load_startup_config(
        str(config_path), env_file=str(env_path), no_env_file=False
    )
    assert _mode(state_home) == 0o700
    assert _mode(config_path) == 0o600
    assert _mode(state_home.parent) == parent_mode
    if existing:
        assert _mode(env_path) == 0o600
        assert os.environ["ROUTELET_PERMISSION_TEST_KEY"] == "example"

    app = create_app(config, CallStore())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put("/api/config", json={"server": {"port": 9457}})
            assert response.status_code == 200
    assert _mode(config_path) == 0o600
    assert "9457" in config_path.read_text(encoding="utf-8")
    assert not list(state_home.glob("*.tmp"))


async def test_database_and_journal_remain_private_when_reopened(state_home):
    store = CallStore()
    await store.init()
    try:
        call_id = await store.record(virtual_model="router", status="success")
        assert _mode(store.db_path) == 0o600
        assert _mode(state_home) == 0o700
    finally:
        await store.close()

    # Also cover data copied from an older installation with wider permissions.
    store.db_path.chmod(0o644)
    state_home.chmod(0o755)
    await store.init()
    try:
        assert await store.get_call(call_id) is not None
        assert _mode(store.db_path) == 0o600
        assert _mode(state_home) == 0o700
        await store.conn.execute("UPDATE calls SET status = 'error'")
        assert _mode(state_home / "calls.db-wal") == 0o600
        assert _mode(state_home / "calls.db-shm") == 0o600
        await store.conn.rollback()
    finally:
        await store.close()


def test_log_rollover_and_existing_backups_are_private(state_home):
    log_dir = state_home / "logs"
    log_dir.mkdir(parents=True)
    state_home.chmod(0o755)
    log_dir.chmod(0o755)
    log_file = log_dir / "routelet.log"
    backup = log_dir / "routelet.log.1"
    for path in (log_file, backup):
        path.write_text("old record\n", encoding="utf-8")
        path.chmod(0o644)
    try:
        monitoring.setup_logging(log_backup_count=2)
        assert _mode(state_home) == _mode(log_dir) == 0o700
        assert _mode(log_file) == _mode(backup) == 0o600
        handler = next(
            handler
            for handler in logging.getLogger().handlers
            if isinstance(handler, RotatingFileHandler)
        )
        handler.doRollover()
        structlog.get_logger("permissions").info("after.rollover")
        handler.flush()
        for path in log_dir.iterdir():
            assert _mode(path) == 0o600
        assert "after.rollover" in log_file.read_text(encoding="utf-8")
    finally:
        root = logging.getLogger()
        for handler in list(root.handlers):
            handler.close()
            root.removeHandler(handler)
        structlog.reset_defaults()


def test_explicit_config_does_not_change_its_parent_permissions(state_home, tmp_path):
    directory = tmp_path / "shared"
    directory.mkdir(mode=0o755)
    config_path = directory / "custom.toml"
    load_startup_config(str(config_path), env_file="", no_env_file=True)
    assert _mode(directory) == 0o755
    assert _mode(config_path) == 0o600
    assert not state_home.exists()


def test_cannot_secure_default_directory_fails_before_writing(state_home, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("cannot restrict directory")

    monkeypatch.setattr(os, "chmod", denied)
    with pytest.raises(PermissionError, match="cannot restrict directory"):
        load_startup_config(
            str(state_home / "config.toml"), env_file="", no_env_file=True
        )
    assert not (state_home / "config.toml").exists()

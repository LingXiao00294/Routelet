from __future__ import annotations

import logging
import subprocess
import sys

import httpx
import pytest
import uvicorn

from routelet import cli
from routelet.app import create_app
from routelet.cli import app as cli_app
from routelet.cli import server
from routelet.cli.config_io import load_startup_config
from routelet.config import load_config
from routelet.db import CallStore


@pytest.fixture
def startup(monkeypatch, tmp_path):
    # Uvicorn Config disables access-log propagation as a process-wide setting.
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn.asgi"):
        logger = logging.getLogger(name)
        for attribute in ("level", "handlers", "propagate"):
            monkeypatch.setattr(logger, attribute, getattr(logger, attribute))
    monkeypatch.chdir(tmp_path)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>Dashboard</html>", encoding="utf-8")
    monkeypatch.setattr(server, "find_dashboard_dist", lambda path: dist)
    monkeypatch.setattr(server, "setup_logging", lambda **kwargs: None)
    seen = []

    def fake_run(instance):
        instance.started = True
        seen.append(instance)

    monkeypatch.setattr(server.BrowserServer, "run", fake_run)
    return seen


def test_single_command_initializes_config_and_combines_services(
    startup, tmp_path, routelet_home, capsys
):
    assert cli.run([]) == 0
    config = load_config(routelet_home / "config.toml")
    assert config.providers == {}
    assert config.models == {}
    assert not (tmp_path / "config.toml").exists()
    assert not (tmp_path / "calls.db").exists()
    output = capsys.readouterr().out
    assert str(routelet_home / "config.toml") in output
    assert str(routelet_home / "calls.db") in output
    instance = startup[0]
    assert instance.config.host == "127.0.0.1"
    assert instance.config.port == 9456
    assert instance.browser_url == "http://127.0.0.1:9456"
    paths = [route.path for route in instance.config.app.routes]
    assert "/v1/messages" in paths
    assert "" == paths[-1]  # Dashboard mount is after every API route.


def test_startup_overrides_and_no_browser_preserve_existing_config(startup, tmp_path):
    config = tmp_path / "custom.toml"
    original = '[server]\nport = 9456\nlog_file = ""\n'
    config.write_text(original, encoding="utf-8")
    assert cli.run(["-c", str(config), "--port", "9457", "--no-browser"]) == 0
    assert startup[0].config.port == 9457
    assert startup[0].browser_url is None
    assert config.read_text(encoding="utf-8") == original


def test_invalid_config_is_reported_without_overwriting(startup, routelet_home, capsys):
    routelet_home.mkdir()
    config = routelet_home / "config.toml"
    config.write_text("invalid [", encoding="utf-8")
    assert cli.run([]) == 1
    assert not startup
    assert config.read_text(encoding="utf-8") == "invalid ["
    assert "读取配置文件失败" in capsys.readouterr().err


def test_missing_assets_prevent_partial_startup(
    startup, monkeypatch, routelet_home, capsys
):
    monkeypatch.setattr(server, "find_dashboard_dist", lambda path: None)
    assert cli.run([]) == 1
    assert not startup
    assert not routelet_home.exists()
    assert "bun run build" in capsys.readouterr().err


def test_remote_bind_requires_opt_in(startup, capsys):
    assert cli.run(["--host", "0.0.0.0"]) == 2
    assert not startup
    assert "--allow-remote" in capsys.readouterr().err
    assert cli.run(["--host", "0.0.0.0", "--allow-remote"]) == 0
    assert startup[0].browser_url == "http://127.0.0.1:9456"


def test_unresolved_keys_allow_dashboard_setup(
    startup, routelet_home, monkeypatch, capsys
):
    monkeypatch.delenv("MISSING_STARTUP_KEY", raising=False)
    routelet_home.mkdir()
    (routelet_home / "config.toml").write_text(
        '[providers.test]\ntype = "anthropic"\napi_key = "${MISSING_STARTUP_KEY}"\n'
        'base_url = "https://example.test"\n',
        encoding="utf-8",
    )
    assert cli.run(["--no-env-file"]) == 0
    assert "api_key 未解析" in capsys.readouterr().err


async def test_default_state_survives_dashboard_save_and_a_different_cwd(
    startup, routelet_home, tmp_path, monkeypatch
):
    key = "ROUTELET_TEST_HOME_KEY"
    monkeypatch.delenv(key, raising=False)
    routelet_home.mkdir()
    (routelet_home / ".env").write_text(f"{key}=home-key\n", encoding="utf-8")
    (routelet_home / "config.toml").write_text(
        '[providers.test]\ntype = "anthropic"\n'
        f'api_key = "${{{key}}}"\nbase_url = "https://example.test"\n',
        encoding="utf-8",
    )
    # A stale project config and .env must never override the shared defaults.
    (tmp_path / "config.toml").write_text("invalid [", encoding="utf-8")
    (tmp_path / ".env").write_text(f"{key}=cwd-key\n", encoding="utf-8")
    assert cli.run([]) == 0
    app = startup[0].config.app
    assert app.state.router_engine.config.providers["test"].api_key == "home-key"
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put("/api/config", json={"server": {"port": 9457}})
            assert response.status_code == 200
    assert (routelet_home / "calls.db").is_file()
    assert load_config(routelet_home / "config.toml").server.port == 9457
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert cli.run([]) == 0
    assert startup[1].config.port == 9457
    assert list(elsewhere.iterdir()) == []
    assert (tmp_path / "config.toml").read_text(encoding="utf-8") == "invalid ["
    assert not (tmp_path / "calls.db").exists()


async def test_app_factory_uses_the_shared_config_path(routelet_home):
    config = load_startup_config(
        str(routelet_home / "config.toml"), env_file="", no_env_file=True
    )
    app = create_app(config, CallStore())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put("/api/config", json={"server": {"port": 9458}})
            assert response.status_code == 200
    assert load_config(routelet_home / "config.toml").server.port == 9458


@pytest.mark.parametrize("use_tilde", [False, True])
async def test_explicit_paths_override_defaults(
    startup, routelet_home, tmp_path, monkeypatch, use_tilde
):
    key = "ROUTELET_TEST_EXPLICIT_KEY"
    monkeypatch.delenv(key, raising=False)
    base = routelet_home.parent if use_tilde else tmp_path
    prefix = "~/" if use_tilde else ""
    (base / "custom.env").write_text(f"{key}=custom-key\n", encoding="utf-8")
    (base / "custom.toml").write_text(
        '[providers.test]\ntype = "anthropic"\n'
        f'api_key = "${{{key}}}"\nbase_url = "https://example.test"\n',
        encoding="utf-8",
    )
    assert (
        cli.run(
            [
                "-c",
                f"{prefix}custom.toml",
                "--db",
                f"{prefix}data/custom.db",
                "--env-file",
                f"{prefix}custom.env",
            ]
        )
        == 0
    )
    app = startup[0].config.app
    assert app.state.router_engine.config.providers["test"].api_key == "custom-key"
    async with app.router.lifespan_context(app):
        assert (base / "data" / "custom.db").is_file()
    assert not routelet_home.exists()


def test_env_file_is_loaded_and_can_be_disabled(monkeypatch, tmp_path):
    key = "ROUTELET_TEST_STARTUP_KEY"
    monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"{key}=secret\n", encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(
        '[providers.test]\ntype = "anthropic"\n'
        f'api_key = "${{{key}}}"\nbase_url = "https://example.test"\n',
        encoding="utf-8",
    )
    disabled = load_startup_config(
        str(config), env_file=str(env_file), no_env_file=True
    )
    assert disabled.providers["test"].api_key == f"${{{key}}}"
    enabled = load_startup_config(
        str(config), env_file=str(env_file), no_env_file=False
    )
    assert enabled.providers["test"].api_key == "secret"


@pytest.mark.parametrize("port", ["0", "65536", "abc", "-1"])
def test_invalid_ports_fail_before_startup(port, startup, capsys):
    assert cli.run(["--port", port]) == 2
    assert not startup
    assert "Traceback" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "command",
    ["serve", "dashboard", "config", "models", "providers", "calls", "stats", "doctor"],
)
def test_removed_subcommands_are_rejected(command, startup, capsys):
    assert cli.run([command]) == 2
    assert not startup
    assert "Traceback" not in capsys.readouterr().err


def test_help_and_version_do_not_start_service(startup, routelet_home, capsys):
    assert cli.run(["--help"]) == 0
    output = capsys.readouterr().out
    assert "--no-browser" in output
    assert "~/.routelet/config.toml" in output
    assert "~/.routelet/calls.db" in output
    assert "~/.routelet/.env" in output
    assert cli.run(["--version"]) == 0
    assert not startup
    assert not routelet_home.exists()


@pytest.mark.parametrize("module", ["routelet.main", "routelet.cli"])
def test_module_entrypoints_work_outside_repository(module, tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "--no-browser" in result.stdout


def test_process_entrypoint_preserves_exit_code(monkeypatch):
    monkeypatch.setattr(cli_app, "command_start", lambda **kwargs: 17)
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 17


@pytest.mark.parametrize(
    "host, expected",
    [
        ("127.0.0.1", "http://127.0.0.1:9456"),
        ("::1", "http://[::1]:9456"),
        ("::", "http://[::1]:9456"),
    ],
)
def test_browser_url(host, expected):
    assert server._browser_url(host, 9456) == expected


@pytest.mark.parametrize(
    "started, disabled", [(True, False), (False, False), (True, True)]
)
async def test_browser_opens_only_after_successful_startup(
    monkeypatch, started, disabled
):
    events = []

    async def fake_startup(instance, sockets=None):
        events.append("startup")
        instance.started = started

    def fake_open(url):
        events.append(url)
        return True

    monkeypatch.setattr(uvicorn.Server, "startup", fake_startup)
    monkeypatch.setattr(server.webbrowser, "open", fake_open)
    url = "http://127.0.0.1:9456"
    instance = server.BrowserServer(
        uvicorn.Config("unused", log_config=None), None if disabled else url
    )
    await instance.startup()
    assert events == (["startup", url] if started and not disabled else ["startup"])


async def test_browser_failure_does_not_stop_service(monkeypatch, capsys):
    async def fake_startup(instance, sockets=None):
        instance.started = True

    def fail_open(url):
        raise OSError("no desktop")

    monkeypatch.setattr(uvicorn.Server, "startup", fake_startup)
    monkeypatch.setattr(server.webbrowser, "open", fail_open)
    instance = server.BrowserServer(
        uvicorn.Config("unused", log_config=None), "http://localhost:9456"
    )
    await instance.startup()
    assert instance.started
    assert "http://localhost:9456" in capsys.readouterr().err


def test_startup_failure_returns_nonzero(startup, monkeypatch):
    monkeypatch.setattr(server.BrowserServer, "run", lambda instance: None)
    assert cli.run([]) == 1

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

from agent_router import cli
from agent_router.db import SCHEMA

SUCCESS_CALL_ID = "75e3917e-0000-4000-8000-000000000001"
ERROR_CALL_ID = "176091dd-0000-4000-8000-000000000002"


def _write_config(path: Path) -> None:
    path.write_text(
        """
[server]
host = "127.0.0.1"
port = 9456
log_level = "info"

[providers.p1]
type = "anthropic"
api_key = "sk-secret-1111"
base_url = "https://api.one.test"

[providers.p1.models.sonnet-first]

[providers.p2]
type = "anthropic"
api_key = "sk-secret-2222"
base_url = "https://api.two.test"

[providers.p2.models.sonnet-second]

[models.sonnet-router]
pinned_model = { provider = "p1", model = "sonnet-first" }
models = [
  { provider = "p1", model = "sonnet-first" },
  { provider = "p2", model = "sonnet-second" },
]
""",
        encoding="utf-8",
    )


def _create_calls_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.execute(
            """INSERT INTO calls (
                id, timestamp, virtual_model, provider_name, provider_type,
                provider_model, latency_ms, status, input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens, cost_usd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                SUCCESS_CALL_ID,
                "2026-06-30T10:00:00+00:00",
                "sonnet-router",
                "p1",
                "anthropic",
                "sonnet-first",
                125,
                "success",
                100,
                40,
                10,
                5,
                0.0123,
            ),
        )
        conn.execute(
            """INSERT INTO calls (
                id, timestamp, virtual_model, provider_name, provider_type,
                provider_model, latency_ms, status, error_type, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ERROR_CALL_ID,
                "2026-06-30T10:01:00+00:00",
                "haiku-router",
                "p2",
                "anthropic",
                "haiku-real",
                300,
                "error",
                "timeout",
                "upstream timed out",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_root_options_keep_serve_compatibility(monkeypatch):
    seen: dict[str, str] = {}

    def fake_serve(args):
        seen["config"] = args.config
        seen["db"] = args.db
        return 17

    monkeypatch.setattr(cli, "command_serve", fake_serve)

    assert cli.run(["--config", "custom.toml", "--db", "custom.db"]) == 17
    assert seen == {"config": "custom.toml", "db": "custom.db"}


def test_dashboard_command_runs_separate_server(monkeypatch, tmp_path, capsys):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<div>dashboard</div>", encoding="utf-8")
    seen: dict[str, Any] = {}

    def fake_create_dashboard_app(dist_path, router_base_url):
        seen["dist"] = dist_path
        seen["router_base_url"] = router_base_url
        return object()

    def fake_uvicorn_run(app, **kwargs):
        seen["app"] = app
        seen["uvicorn"] = kwargs

    monkeypatch.setattr(cli, "find_dashboard_dist", lambda explicit: dist)
    monkeypatch.setattr(cli, "create_dashboard_app", fake_create_dashboard_app)
    monkeypatch.setattr(cli.uvicorn, "run", fake_uvicorn_run)

    exit_code = cli.run(
        [
            "dashboard",
            "--host",
            "0.0.0.0",
            "--port",
            "6180",
            "--router-url",
            "http://127.0.0.1:9456",
            "--allow-remote",
        ]
    )

    assert exit_code == 0
    assert seen["dist"] == dist
    assert seen["router_base_url"] == "http://127.0.0.1:9456"
    assert seen["uvicorn"] == {
        "host": "0.0.0.0",
        "port": 6180,
        "log_level": "info",
        "access_log": False,
        "log_config": None,
    }
    output = capsys.readouterr()
    assert "Agent Router Dashboard 启动" in output.out
    assert "没有内置鉴权" in output.err


def test_dashboard_command_refuses_remote_bind_without_opt_in(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "find_dashboard_dist",
        lambda explicit: (_ for _ in ()).throw(AssertionError("不应查找 dist")),
    )

    exit_code = cli.run(["dashboard", "--host", "0.0.0.0"])

    assert exit_code == 2
    assert "--allow-remote" in capsys.readouterr().err


def test_dashboard_command_reports_missing_dist(monkeypatch, capsys):
    monkeypatch.setattr(cli, "find_dashboard_dist", lambda explicit: None)

    exit_code = cli.run(["dashboard"])

    assert exit_code == 1
    assert "未找到 dashboard 静态文件" in capsys.readouterr().err


def test_dashboard_entrypoint_prefixes_dashboard_command(monkeypatch):
    seen: dict[str, int] = {}

    def fake_dashboard(args):
        seen["port"] = args.port
        return 23

    monkeypatch.setattr(cli, "command_dashboard", fake_dashboard)

    assert cli.run_dashboard(["--port", "6181"]) == 23
    assert seen == {"port": 6181}


def test_config_init_copies_example(tmp_path, capsys):
    example = tmp_path / "config.toml.example"
    target = tmp_path / "config.toml"
    example.write_text("[server]\nport = 9456\n", encoding="utf-8")

    exit_code = cli.run(
        ["config", "init", "--config", str(target), "--example", str(example)]
    )

    assert exit_code == 0
    assert target.read_text(encoding="utf-8") == "[server]\nport = 9456\n"
    assert "已生成配置文件" in capsys.readouterr().out


def test_config_init_refuses_existing_file(tmp_path, capsys):
    example = tmp_path / "config.toml.example"
    target = tmp_path / "config.toml"
    example.write_text("[server]\nport = 9456\n", encoding="utf-8")
    target.write_text("[server]\nport = 1\n", encoding="utf-8")

    exit_code = cli.run(
        ["config", "init", "--config", str(target), "--example", str(example)]
    )

    assert exit_code == 1
    assert target.read_text(encoding="utf-8") == "[server]\nport = 1\n"
    assert "已存在" in capsys.readouterr().err


def test_config_show_masks_api_keys(tmp_path, capsys):
    config = tmp_path / "config.toml"
    _write_config(config)

    exit_code = cli.run(["config", "show", "--config", str(config), "--format", "json"])

    assert exit_code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["providers"]["p1"]["api_key"] == "sk-s******1111"
    assert data["providers"]["p1"]["has_key"] is True
    assert "sk-secret-1111" not in json.dumps(data)


def test_config_validate_outputs_route_summary(tmp_path, capsys):
    config = tmp_path / "config.toml"
    _write_config(config)

    exit_code = cli.run(
        ["config", "validate", "--config", str(config), "--no-env-file"]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "配置有效" in out
    assert (
        "sonnet-router [pin=p1:sonnet-first]: "
        "p1:sonnet-first(p1) -> p2:sonnet-second(p2)"
    ) in out


def test_serve_allows_unresolved_api_key_for_dashboard_setup(
    monkeypatch, tmp_path, capsys
):
    config = tmp_path / "config.toml"
    config.write_text(
        """
[server]
host = "127.0.0.1"
port = 9456
log_file = ""

[providers.p1]
type = "anthropic"
api_key = "${MISSING_API_KEY_FOR_STARTUP_TEST}"
base_url = "https://api.one.test"

[providers.p1.models.sonnet-first]

[models.sonnet-router]
pinned_model = { provider = "p1", model = "sonnet-first" }
models = [{ provider = "p1", model = "sonnet-first" }]
""",
        encoding="utf-8",
    )
    seen: dict[str, object] = {}

    def fake_uvicorn_run(app, **kwargs):
        seen["app"] = app
        seen["uvicorn"] = kwargs

    monkeypatch.delenv("MISSING_API_KEY_FOR_STARTUP_TEST", raising=False)
    monkeypatch.setattr(cli.uvicorn, "run", fake_uvicorn_run)

    exit_code = cli.run(
        [
            "serve",
            "--config",
            str(config),
            "--no-env-file",
            "--db",
            str(tmp_path / "calls.db"),
        ]
    )

    assert exit_code == 0
    uvicorn_kwargs = cast(dict[str, Any], seen["uvicorn"])
    assert uvicorn_kwargs["host"] == "127.0.0.1"
    assert "api_key 未解析" in capsys.readouterr().err


def test_serve_refuses_remote_bind_without_opt_in(monkeypatch, tmp_path, capsys):
    config = tmp_path / "config.toml"
    _write_config(config)
    monkeypatch.setattr(
        cli.uvicorn,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("不应启动 uvicorn")
        ),
    )

    exit_code = cli.run(
        [
            "serve",
            "--config",
            str(config),
            "--host",
            "0.0.0.0",
            "--no-env-file",
            "--db",
            str(tmp_path / "calls.db"),
        ]
    )

    assert exit_code == 2
    assert "--allow-remote" in capsys.readouterr().err


def test_loopback_bind_detection_accepts_ipv4_ipv6_and_localhost(capsys):
    assert cli._allow_bind("127.0.0.1", False)
    assert cli._allow_bind("::1", False)
    assert cli._allow_bind("[::1]", False)
    assert cli._allow_bind("localhost", False)
    assert capsys.readouterr().err == ""


def test_models_lists_routes_in_priority_order(tmp_path, capsys):
    config = tmp_path / "config.toml"
    _write_config(config)

    exit_code = cli.run(["models", "--config", str(config), "--format", "json"])

    assert exit_code == 0
    rows = json.loads(capsys.readouterr().out)
    assert [row["model"] for row in rows] == ["sonnet-first", "sonnet-second"]
    assert [row["priority"] for row in rows] == [1, 2]


def test_stats_reads_summary_without_writing(tmp_path, capsys):
    db_path = tmp_path / "calls.db"
    _create_calls_db(db_path)

    exit_code = cli.run(["stats", "--db", str(db_path), "--format", "json"])

    assert exit_code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["total_calls"] == 2
    assert summary["success_count"] == 1
    assert summary["error_count"] == 1
    assert summary["success_rate"] == 50.0
    assert summary["total_input_tokens"] == 100
    assert summary["total_output_tokens"] == 40


def test_calls_list_filters_by_status(tmp_path, capsys):
    db_path = tmp_path / "calls.db"
    _create_calls_db(db_path)

    exit_code = cli.run(
        ["calls", "list", "--db", str(db_path), "--status", "error", "--format", "json"]
    )

    assert exit_code == 0
    rows = json.loads(capsys.readouterr().out)
    assert [row["id"] for row in rows] == [ERROR_CALL_ID]
    assert rows[0]["error_type"] == "timeout"


def test_calls_show_outputs_single_record(tmp_path, capsys):
    db_path = tmp_path / "calls.db"
    _create_calls_db(db_path)

    exit_code = cli.run(
        ["calls", "show", SUCCESS_CALL_ID, "--db", str(db_path), "--format", "json"]
    )

    assert exit_code == 0
    row = json.loads(capsys.readouterr().out)
    assert row["id"] == SUCCESS_CALL_ID
    assert row["provider_model"] == "sonnet-first"


def test_calls_show_accepts_unique_short_id_prefix(tmp_path, capsys):
    db_path = tmp_path / "calls.db"
    _create_calls_db(db_path)

    exit_code = cli.run(
        [
            "calls",
            "show",
            SUCCESS_CALL_ID[:8],
            "--db",
            str(db_path),
            "--format",
            "json",
        ]
    )

    assert exit_code == 0
    row = json.loads(capsys.readouterr().out)
    assert row["id"] == SUCCESS_CALL_ID


def test_calls_show_reports_ambiguous_short_id_prefix(tmp_path, capsys):
    db_path = tmp_path / "calls.db"
    _create_calls_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO calls (
                id, timestamp, virtual_model, provider_name, provider_type,
                provider_model, latency_ms, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "75e3917e-9999-4000-8000-000000000003",
                "2026-06-30T10:02:00+00:00",
                "sonnet-router",
                "p1",
                "anthropic",
                "sonnet-first",
                200,
                "success",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    exit_code = cli.run(["calls", "show", "75e3917e", "--db", str(db_path)])

    assert exit_code == 1
    assert "匹配到多条调用记录" in capsys.readouterr().err


def test_typer_usage_errors_do_not_render_tracebacks(capsys):
    calls_exit_code = cli.run(["calls"])
    calls_output = capsys.readouterr()
    show_exit_code = cli.run(["calls", "show"])
    show_output = capsys.readouterr()

    assert calls_exit_code != 0
    assert show_exit_code != 0
    combined = calls_output.out + calls_output.err + show_output.out + show_output.err
    assert "Traceback" not in combined
    assert "NoArgsIsHelpError" not in combined
    assert "MissingParameter" not in combined


def test_provider_ref_counts_include_distinct_failover_pin():
    raw = {
        "models": {
            "router": {
                "pinned_model": {"provider": "pinned", "model": "special"},
                "models": [{"provider": "primary", "model": "regular"}],
            }
        }
    }

    assert cli._provider_ref_counts(raw) == {"primary": 1, "pinned": 1}

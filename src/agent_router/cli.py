from __future__ import annotations

import json
from ipaddress import ip_address
import shutil
import sqlite3
import sys
import tomllib
from collections.abc import Sequence
from copy import deepcopy
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import typer
import uvicorn
from click import ClickException
from dotenv import load_dotenv

from agent_router.config import (
    AppConfig,
    ConfigError,
    has_unresolved_env_var,
    load_config,
)
from agent_router.dashboard import (
    DEFAULT_ROUTER_URL,
    create_dashboard_app,
    find_dashboard_dist,
)
from agent_router.db import CallStore
from agent_router.monitoring import setup_logging

DEFAULT_CONFIG_PATH = "config.toml"
DEFAULT_DB_PATH = "calls.db"
DEFAULT_EXAMPLE_PATH = "config.toml.example"
DEFAULT_DASHBOARD_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 5173

_ROOT_OPTION_COMMANDS = {"-h", "--help", "--version"}
_CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


class OutputFormat(str, Enum):
    """Supported CLI output formats."""

    text = "text"
    json = "json"


class CallStatus(str, Enum):
    """Supported call status filters."""

    success = "success"
    error = "error"


class LogLevel(str, Enum):
    """Supported dashboard log levels."""

    debug = "debug"
    info = "info"
    warning = "warning"
    error = "error"


app = typer.Typer(
    help="Agent Router CLI - 本地 LLM API 路由代理",
    add_completion=False,
    context_settings=_CONTEXT_SETTINGS,
)
config_app = typer.Typer(
    help="管理配置文件",
    add_completion=False,
    context_settings=_CONTEXT_SETTINGS,
    no_args_is_help=True,
)
calls_app = typer.Typer(
    help="查询调用记录",
    add_completion=False,
    context_settings=_CONTEXT_SETTINGS,
    no_args_is_help=True,
)
dashboard_app = typer.Typer(
    help="Agent Router Dashboard - 独立监控面板服务",
    add_completion=False,
    context_settings=_CONTEXT_SETTINGS,
)

app.add_typer(config_app, name="config")
app.add_typer(calls_app, name="calls")


def main(argv: Sequence[str] | None = None) -> None:
    """Run the Agent Router command-line interface."""
    raise SystemExit(run(argv))


def dashboard_main(argv: Sequence[str] | None = None) -> None:
    """Run the standalone dashboard command-line entry point."""
    raise SystemExit(run_dashboard(argv))


def run(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and dispatch to the selected command."""
    return _invoke_typer(app, _normalize_argv(argv), "agent-router")


def run_dashboard(argv: Sequence[str] | None = None) -> int:
    """Parse arguments for the standalone dashboard executable."""
    raw = list(sys.argv[1:] if argv is None else argv)
    return _invoke_typer(dashboard_app, raw, "agent-router-dashboard")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"agent-router {_package_version()}")
        raise typer.Exit()


@app.callback()
def root_callback(
    version_: bool = typer.Option(
        False,
        "--version",
        help="显示版本并退出",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Agent Router CLI."""


@app.command("serve")
def serve_command(
    config: str = typer.Option(
        DEFAULT_CONFIG_PATH,
        "--config",
        "-c",
        help="配置文件路径",
    ),
    env_file: str = typer.Option(
        ".env",
        "--env-file",
        help="加载环境变量文件；设为空字符串可跳过",
    ),
    no_env_file: bool = typer.Option(
        False,
        "--no-env-file",
        help="不加载 .env 文件",
    ),
    host: str | None = typer.Option(
        None,
        "--host",
        help="覆盖配置文件中的 server.host",
    ),
    port: int | None = typer.Option(
        None,
        "--port",
        "-p",
        help="覆盖配置文件中的 server.port",
    ),
    db: str = typer.Option(DEFAULT_DB_PATH, "--db", help="调用记录数据库路径"),
    allow_remote: bool = typer.Option(
        False,
        "--allow-remote",
        help="允许监听非回环地址（服务无内置鉴权，仅限受信网络）",
    ),
) -> int:
    """启动本地路由代理服务."""
    return command_serve(
        SimpleNamespace(
            config=config,
            env_file=env_file,
            no_env_file=no_env_file,
            host=host,
            port=port,
            db=db,
            allow_remote=allow_remote,
        )
    )


@app.command("dashboard")
def dashboard_command(
    host: str = typer.Option(
        DEFAULT_DASHBOARD_HOST,
        "--host",
        help="dashboard 监听地址",
    ),
    port: int = typer.Option(
        DEFAULT_DASHBOARD_PORT,
        "--port",
        "-p",
        help="dashboard 监听端口",
    ),
    router_url: str = typer.Option(
        DEFAULT_ROUTER_URL,
        "--router-url",
        help="后端 router 服务地址",
    ),
    dist: str | None = typer.Option(
        None,
        "--dist",
        help="dashboard/dist 静态文件目录；默认自动查找构建目录",
    ),
    log_level: LogLevel = typer.Option(
        LogLevel.info,
        "--log-level",
        help="dashboard 服务日志级别",
    ),
    allow_remote: bool = typer.Option(
        False,
        "--allow-remote",
        help="允许监听非回环地址（Dashboard 可访问敏感管理接口）",
    ),
) -> int:
    """启动独立 dashboard 服务."""
    return command_dashboard(
        SimpleNamespace(
            host=host,
            port=port,
            router_url=router_url,
            dist=dist,
            log_level=log_level.value,
            allow_remote=allow_remote,
        )
    )


@dashboard_app.command()
def standalone_dashboard_command(
    host: str = typer.Option(
        DEFAULT_DASHBOARD_HOST,
        "--host",
        help="dashboard 监听地址",
    ),
    port: int = typer.Option(
        DEFAULT_DASHBOARD_PORT,
        "--port",
        "-p",
        help="dashboard 监听端口",
    ),
    router_url: str = typer.Option(
        DEFAULT_ROUTER_URL,
        "--router-url",
        help="后端 router 服务地址",
    ),
    dist: str | None = typer.Option(
        None,
        "--dist",
        help="dashboard/dist 静态文件目录；默认自动查找构建目录",
    ),
    log_level: LogLevel = typer.Option(
        LogLevel.info,
        "--log-level",
        help="dashboard 服务日志级别",
    ),
    allow_remote: bool = typer.Option(
        False,
        "--allow-remote",
        help="允许监听非回环地址（Dashboard 可访问敏感管理接口）",
    ),
) -> int:
    """启动独立 dashboard 服务."""
    return command_dashboard(
        SimpleNamespace(
            host=host,
            port=port,
            router_url=router_url,
            dist=dist,
            log_level=log_level.value,
            allow_remote=allow_remote,
        )
    )


@config_app.command("init")
def config_init_command(
    config: str = typer.Option(
        DEFAULT_CONFIG_PATH,
        "--config",
        "-c",
        help="要写入的配置文件路径",
    ),
    example: str | None = typer.Option(
        None,
        "--example",
        help="示例配置文件路径",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="允许覆盖已存在的配置文件",
    ),
) -> int:
    """从示例文件生成配置."""
    return command_config_init(
        SimpleNamespace(config=config, example=example, force=force)
    )


@config_app.command("validate")
def config_validate_command(
    config: str = typer.Option(
        DEFAULT_CONFIG_PATH,
        "--config",
        "-c",
        help="配置文件路径",
    ),
    env_file: str = typer.Option(
        ".env",
        "--env-file",
        help="加载环境变量文件；设为空字符串可跳过",
    ),
    no_env_file: bool = typer.Option(
        False,
        "--no-env-file",
        help="不加载 .env 文件",
    ),
) -> int:
    """校验配置并输出路由摘要."""
    return command_config_validate(
        SimpleNamespace(
            config=config,
            env_file=env_file,
            no_env_file=no_env_file,
        )
    )


@config_app.command("show")
def config_show_command(
    config: str = typer.Option(
        DEFAULT_CONFIG_PATH,
        "--config",
        "-c",
        help="配置文件路径",
    ),
    format_: OutputFormat = typer.Option(
        OutputFormat.text,
        "--format",
        help="输出格式",
    ),
) -> int:
    """查看配置内容，自动脱敏 api_key."""
    return command_config_show(SimpleNamespace(config=config, format=format_.value))


@app.command("models")
def models_command(
    config: str = typer.Option(
        DEFAULT_CONFIG_PATH,
        "--config",
        "-c",
        help="配置文件路径",
    ),
    format_: OutputFormat = typer.Option(
        OutputFormat.text,
        "--format",
        help="输出格式",
    ),
) -> int:
    """列出虚拟模型和 provider 优先级链."""
    return command_models(SimpleNamespace(config=config, format=format_.value))


@app.command("providers")
def providers_command(
    config: str = typer.Option(
        DEFAULT_CONFIG_PATH,
        "--config",
        "-c",
        help="配置文件路径",
    ),
    format_: OutputFormat = typer.Option(
        OutputFormat.text,
        "--format",
        help="输出格式",
    ),
) -> int:
    """列出 provider 定义和引用情况."""
    return command_providers(SimpleNamespace(config=config, format=format_.value))


@calls_app.command("list")
def calls_list_command(
    db: str = typer.Option(DEFAULT_DB_PATH, "--db", help="调用记录数据库路径"),
    limit: int = typer.Option(20, "--limit", help="返回记录数量"),
    model: str | None = typer.Option(None, "--model", help="按虚拟模型过滤"),
    status: CallStatus | None = typer.Option(
        None,
        "--status",
        help="按调用状态过滤",
    ),
    format_: OutputFormat = typer.Option(
        OutputFormat.text,
        "--format",
        help="输出格式",
    ),
) -> int:
    """列出最近调用记录."""
    return command_calls_list(
        SimpleNamespace(
            db=db,
            limit=limit,
            model=model,
            status=status.value if status else None,
            format=format_.value,
        )
    )


@calls_app.command("show")
def calls_show_command(
    call_id: str = typer.Argument(..., help="调用记录 ID"),
    db: str = typer.Option(DEFAULT_DB_PATH, "--db", help="调用记录数据库路径"),
    format_: OutputFormat = typer.Option(
        OutputFormat.text,
        "--format",
        help="输出格式",
    ),
) -> int:
    """查看单次调用详情."""
    return command_calls_show(
        SimpleNamespace(call_id=call_id, db=db, format=format_.value)
    )


@app.command("stats")
def stats_command(
    db: str = typer.Option(DEFAULT_DB_PATH, "--db", help="调用记录数据库路径"),
    format_: OutputFormat = typer.Option(
        OutputFormat.text,
        "--format",
        help="输出格式",
    ),
) -> int:
    """查看调用统计摘要."""
    return command_stats(SimpleNamespace(db=db, format=format_.value))


@app.command("doctor")
def doctor_command(
    config: str = typer.Option(
        DEFAULT_CONFIG_PATH,
        "--config",
        "-c",
        help="配置文件路径",
    ),
    env_file: str = typer.Option(
        ".env",
        "--env-file",
        help="加载环境变量文件；设为空字符串可跳过",
    ),
    no_env_file: bool = typer.Option(
        False,
        "--no-env-file",
        help="不加载 .env 文件",
    ),
    db: str = typer.Option(DEFAULT_DB_PATH, "--db", help="调用记录数据库路径"),
) -> int:
    """检查配置、数据库路径和日志路径."""
    return command_doctor(
        SimpleNamespace(
            config=config,
            env_file=env_file,
            no_env_file=no_env_file,
            db=db,
        )
    )


def _invoke_typer(
    typer_app: typer.Typer,
    argv: Sequence[str],
    prog_name: str,
) -> int:
    try:
        result = typer_app(
            args=list(argv),
            prog_name=prog_name,
            standalone_mode=False,
        )
        return result if isinstance(result, int) else 0
    except typer.Exit as exc:
        return exc.exit_code or 0
    except SystemExit as exc:
        return _system_exit_code(exc)
    except ClickException as exc:
        exc.show()
        return exc.exit_code
    except Exception as exc:
        if _is_typer_usage_error(exc):
            show = getattr(exc, "show", None)
            if callable(show):
                show()
            elif exc.__class__.__name__ != "NoArgsIsHelpError":
                print(f"错误: {exc}", file=sys.stderr)
            return int(getattr(exc, "exit_code", 2))
        raise
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130


def command_serve(args: SimpleNamespace) -> int:
    """Start the FastAPI router service."""
    _load_env_file(args)
    config, exit_code = _load_config(args.config, allow_unresolved_api_keys=True)
    if config is None:
        return exit_code
    unresolved = _unresolved_runtime_providers(config)
    if unresolved:
        print(
            "警告: 以下 provider 的 api_key 未解析，路由请求会跳过它们: "
            + ", ".join(unresolved),
            file=sys.stderr,
        )

    if args.host:
        config.server.host = args.host
    if args.port:
        config.server.port = args.port

    if not _allow_bind(config.server.host, getattr(args, "allow_remote", False)):
        return 2

    setup_logging(
        level=config.server.log_level,
        log_file=config.server.log_file,
        log_max_bytes=config.server.log_max_bytes,
        log_backup_count=config.server.log_backup_count,
    )

    store = CallStore(args.db)

    # 延迟导入，确保日志已配置。
    from agent_router.app import create_app

    app = create_app(config, store, config_path=args.config)

    print(f"Agent Router 启动: http://{config.server.host}:{config.server.port}")
    print(f"配置文件: {args.config}")
    print(f"数据库: {args.db}")
    print(f"虚拟模型: {', '.join(config.models.keys()) or '(未配置)'}")

    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level=config.server.log_level,
        access_log=False,
        log_config=None,
    )
    return 0


def command_dashboard(args: SimpleNamespace) -> int:
    """Start the standalone dashboard server."""
    if not _allow_bind(args.host, getattr(args, "allow_remote", False)):
        return 2

    dist = find_dashboard_dist(args.dist)
    if dist is None:
        print(
            "错误: 未找到 dashboard 静态文件。请先执行 `cd dashboard && bun install && "
            "bun run build` 后重新安装，或使用 --dist 指向构建目录。",
            file=sys.stderr,
        )
        return 1

    try:
        app = create_dashboard_app(dist, router_base_url=args.router_url)
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    print(f"Agent Router Dashboard 启动: http://{args.host}:{args.port}")
    print(f"静态文件: {dist}")
    print(f"Router API: {args.router_url}")
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=False,
        log_config=None,
    )
    return 0


def _allow_bind(host: str, allow_remote: bool) -> bool:
    """Validate an unauthenticated service bind address.

    Args:
        host: Hostname or IP address that uvicorn will bind.
        allow_remote: Whether the operator explicitly accepted remote exposure.

    Returns:
        ``True`` for loopback addresses or an explicitly allowed remote bind.
        Otherwise prints an actionable error and returns ``False``.
    """
    normalized = host.strip().strip("[]").casefold()
    is_loopback = normalized == "localhost"
    if not is_loopback:
        try:
            is_loopback = ip_address(normalized).is_loopback
        except ValueError:
            is_loopback = False
    if is_loopback:
        return True

    warning = (
        f"监听地址 {host!r} 可被远程访问；服务没有内置鉴权，"
        "会暴露调用正文、配置写入和上游调用能力"
    )
    if not allow_remote:
        print(
            f"错误: {warning}。如已部署外部鉴权，请显式添加 --allow-remote",
            file=sys.stderr,
        )
        return False
    print(f"警告: {warning}；请确保仅位于受信网络或鉴权反向代理之后", file=sys.stderr)
    return True


def command_config_init(args: SimpleNamespace) -> int:
    """Create a configuration file from the bundled example."""
    target = Path(args.config)
    example = _resolve_example_path(args.example)

    if not example.exists():
        print(f"错误: 示例配置不存在: {example}", file=sys.stderr)
        return 1
    if target.exists() and not args.force:
        print(
            f"错误: 配置文件已存在: {target}。如需覆盖请添加 --force",
            file=sys.stderr,
        )
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(example, target)
    print(f"已生成配置文件: {target}")
    return 0


def command_config_validate(args: SimpleNamespace) -> int:
    """Validate configuration and print a route summary."""
    _load_env_file(args)
    config, exit_code = _load_config(args.config)
    if config is None:
        return exit_code

    raw = _read_config_raw(args.config)
    print(f"配置有效: {args.config}")
    print(f"监听地址: {config.server.host}:{config.server.port}")
    print(f"Provider 数量: {len(raw.get('providers', {}))}")
    print(f"虚拟模型数量: {len(config.models)}")
    _print_model_routes(config)
    return 0


def command_config_show(args: SimpleNamespace) -> int:
    """Print a redacted view of the configuration file."""
    raw = _read_config_raw(args.config)
    safe = _redact_config(raw)
    if args.format == "json":
        print(json.dumps(safe, ensure_ascii=False, indent=2))
    else:
        _print_config(safe)
    return 0


def command_models(args: SimpleNamespace) -> int:
    """List virtual model routes from the raw configuration."""
    raw = _read_config_raw(args.config)
    models = _model_rows(raw)
    if args.format == "json":
        print(json.dumps(models, ensure_ascii=False, indent=2))
        return 0
    if not models:
        print("未配置虚拟模型")
        return 0
    _print_table(
        ["虚拟模型", "优先级", "Provider", "真实模型"],
        [
            [
                row["virtual_model"],
                row["priority"],
                row["provider"],
                row["model"],
            ]
            for row in models
        ],
    )
    return 0


def command_providers(args: SimpleNamespace) -> int:
    """List provider definitions and model reference counts."""
    raw = _read_config_raw(args.config)
    providers = _provider_rows(raw)
    if args.format == "json":
        print(json.dumps(providers, ensure_ascii=False, indent=2))
        return 0
    if not providers:
        print("未配置 provider")
        return 0
    _print_table(
        ["Provider", "类型", "Base URL", "API Key", "模型引用"],
        [
            [
                row["name"],
                row["type"],
                row["base_url"],
                row["api_key"],
                row["model_refs"],
            ]
            for row in providers
        ],
    )
    return 0


def command_calls_list(args: SimpleNamespace) -> int:
    """List recent call records from the SQLite database."""
    conn = _connect_readonly_db(args.db)
    if conn is None:
        return 1
    try:
        rows = _query_calls(
            conn,
            limit=args.limit,
            model=args.model,
            status=args.status,
        )
        if args.format == "json":
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        if not rows:
            print("未找到调用记录")
            return 0
        _print_table(
            ["ID", "时间", "虚拟模型", "Provider", "真实模型", "状态", "延迟ms"],
            [
                [
                    row["id"][:8],
                    _short_time(row["timestamp"]),
                    row["virtual_model"] or "-",
                    row["provider_name"] or row["provider_type"] or "-",
                    row["provider_model"] or "-",
                    row["status"] or "-",
                    row["latency_ms"] if row["latency_ms"] is not None else "-",
                ]
                for row in rows
            ],
        )
    finally:
        conn.close()
    return 0


def command_calls_show(args: SimpleNamespace) -> int:
    """Show one call record from the SQLite database."""
    conn = _connect_readonly_db(args.db)
    if conn is None:
        return 1
    try:
        rows = _query_call_matches(conn, args.call_id)
        if not rows:
            print(f"错误: 调用记录不存在: {args.call_id}", file=sys.stderr)
            return 1
        if len(rows) > 1:
            ids = ", ".join(row["id"] for row in rows[:5])
            print(
                f"错误: '{args.call_id}' 匹配到多条调用记录，请使用更长 ID: {ids}",
                file=sys.stderr,
            )
            return 1
        row = rows[0]
        if args.format == "json":
            print(json.dumps(row, ensure_ascii=False, indent=2))
        else:
            for key, value in row.items():
                print(f"{key}: {_format_detail_value(value)}")
    finally:
        conn.close()
    return 0


def command_stats(args: SimpleNamespace) -> int:
    """Print call statistics from the SQLite database."""
    conn = _connect_readonly_db(args.db)
    if conn is None:
        return 1
    try:
        summary = _query_summary(conn)
        if args.format == "json":
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            _print_key_values(
                {
                    "总调用": summary["total_calls"],
                    "成功": summary["success_count"],
                    "失败": summary["error_count"],
                    "成功率": f"{summary['success_rate']}%",
                    "输入 tokens": summary["total_input_tokens"],
                    "输出 tokens": summary["total_output_tokens"],
                    "缓存读 tokens": summary["total_cache_read"],
                    "缓存写 tokens": summary["total_cache_write"],
                    "平均延迟 ms": summary["avg_latency_ms"],
                    "总费用 USD": f"{summary['total_cost_usd']:.6f}",
                }
            )
    finally:
        conn.close()
    return 0


def command_doctor(args: SimpleNamespace) -> int:
    """Run local checks for common setup problems."""
    _load_env_file(args)
    config_path = Path(args.config)
    db_path = Path(args.db)
    checks: list[dict[str, str]] = []

    if config_path.exists():
        checks.append(_check("ok", "配置文件", str(config_path)))
    else:
        checks.append(_check("error", "配置文件", f"不存在: {config_path}"))

    config, exit_code = _load_config(args.config, allow_unresolved_api_keys=True)
    if config is None:
        checks.append(_check("error", "配置语义", "校验失败"))
    else:
        checks.append(
            _check(
                "ok",
                "配置语义",
                f"{len(config.models)} 个虚拟模型，监听 {config.server.host}:{config.server.port}",
            )
        )
        if not config.models:
            checks.append(_check("warn", "路由模型", "未配置任何可用虚拟模型"))
        unresolved = _unresolved_runtime_providers(config)
        if unresolved:
            checks.append(
                _check(
                    "warn",
                    "Provider API Key",
                    "未解析，将在路由时跳过: " + ", ".join(unresolved),
                )
            )
        if config.server.log_file:
            log_parent = Path(config.server.log_file).parent
            checks.append(
                _check_path_parent(
                    "日志目录",
                    log_parent,
                    "运行时会自动写入日志文件",
                )
            )

    checks.append(
        _check_path_parent("数据库目录", db_path.parent, "运行时会自动创建数据库")
    )
    if db_path.exists():
        checks.append(_check("ok", "数据库文件", str(db_path)))
    else:
        checks.append(_check("warn", "数据库文件", f"尚不存在: {db_path}"))

    dashboard_dist = find_dashboard_dist()
    if dashboard_dist is None:
        checks.append(_check("warn", "Dashboard", "未找到 dashboard/dist 静态文件"))
    else:
        checks.append(_check("ok", "Dashboard", str(dashboard_dist)))

    _print_table(
        ["状态", "检查项", "说明"],
        [[item["status"], item["name"], item["message"]] for item in checks],
    )
    return 1 if exit_code else 0


def _is_typer_usage_error(exc: Exception) -> bool:
    return exc.__class__.__name__ in {
        "BadParameter",
        "MissingParameter",
        "NoArgsIsHelpError",
        "NoSuchOption",
        "UsageError",
    }


def _normalize_argv(argv: Sequence[str] | None) -> list[str]:
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw:
        return ["serve"]
    if raw[0] in _ROOT_OPTION_COMMANDS:
        return raw
    if raw[0].startswith("-"):
        return ["serve", *raw]
    return raw


def _package_version() -> str:
    try:
        return version("agent-router")
    except PackageNotFoundError:
        return "0.1.0"


def _load_env_file(args: SimpleNamespace) -> None:
    if getattr(args, "no_env_file", False):
        return
    env_file = getattr(args, "env_file", ".env")
    if env_file:
        load_dotenv(env_file)


def _load_config(
    config_path: str, *, allow_unresolved_api_keys: bool = False
) -> tuple[AppConfig | None, int]:
    try:
        return (
            load_config(
                config_path,
                allow_unresolved_api_keys=allow_unresolved_api_keys,
            ),
            0,
        )
    except ConfigError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return None, 1


def _unresolved_runtime_providers(config: AppConfig) -> list[str]:
    unresolved: dict[str, str] = {}
    for vm in config.models.values():
        for provider in vm.providers:
            if has_unresolved_env_var(provider.api_key):
                unresolved[provider.name] = provider.api_key
    return [f"{name}({api_key})" for name, api_key in sorted(unresolved.items())]


def _system_exit_code(exc: SystemExit) -> int:
    if isinstance(exc.code, int):
        return exc.code
    return 1


def _read_config_raw(config_path: str) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        print(f"错误: 配置文件不存在: {path}", file=sys.stderr)
        raise SystemExit(1)
    with path.open("rb") as f:
        return tomllib.load(f)


def _resolve_example_path(example_path: str | None) -> Path:
    if example_path:
        requested = Path(example_path)
        if requested.exists() or example_path != DEFAULT_EXAMPLE_PATH:
            return requested

    local_example = Path(DEFAULT_EXAMPLE_PATH)
    if local_example.exists():
        return local_example

    packaged_example = Path(__file__).resolve().with_name(DEFAULT_EXAMPLE_PATH)
    if packaged_example.exists():
        return packaged_example

    return Path(example_path or DEFAULT_EXAMPLE_PATH)


def _redact_config(raw: dict[str, Any]) -> dict[str, Any]:
    safe = deepcopy(raw)
    providers = safe.get("providers", {})
    if isinstance(providers, dict):
        for provider in providers.values():
            if isinstance(provider, dict) and "api_key" in provider:
                api_key = str(provider["api_key"])
                provider["has_key"] = bool(api_key)
                provider["api_key"] = _mask_key(api_key)
    return safe


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def _print_config(config: dict[str, Any]) -> None:
    server = config.get("server", {})
    if isinstance(server, dict) and server:
        print("[server]")
        _print_key_values(server)
        print()

    router = config.get("router", {})
    if isinstance(router, dict) and router:
        print("[router]")
        _print_key_values(router)
        print()

    providers = _provider_rows(config)
    if providers:
        print("[providers]")
        _print_table(
            ["Provider", "类型", "Base URL", "API Key", "模型引用"],
            [
                [
                    row["name"],
                    row["type"],
                    row["base_url"],
                    row["api_key"],
                    row["model_refs"],
                ]
                for row in providers
            ],
        )
        print()

    models = _model_rows(config)
    if models:
        print("[models]")
        _print_table(
            ["虚拟模型", "优先级", "Provider", "真实模型"],
            [
                [
                    row["virtual_model"],
                    row["priority"],
                    row["provider"],
                    row["model"],
                ]
                for row in models
            ],
        )


def _print_model_routes(config: AppConfig) -> None:
    if not config.models:
        print("虚拟模型: (未配置)")
        return
    print("路由链:")
    print(f"路由模式: {config.router.mode}")
    for virtual_model, vm in config.models.items():
        chain = " -> ".join(f"{p.name}:{p.model}(p{p.priority})" for p in vm.providers)
        pin = (
            f" [pin={vm.pinned_model.provider}:{vm.pinned_model.model}]"
            if vm.pinned_model is not None
            else ""
        )
        print(f"  {virtual_model}{pin}: {chain}")


def _provider_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    providers = raw.get("providers", {})
    if not isinstance(providers, dict):
        return []
    ref_counts = _provider_ref_counts(raw)
    rows: list[dict[str, Any]] = []
    for name, pdata in sorted(providers.items()):
        if not isinstance(pdata, dict):
            continue
        provider_name = str(name)
        provider_data = cast(dict[str, Any], pdata)
        api_key = str(provider_data.get("api_key", ""))
        rows.append(
            {
                "name": provider_name,
                "type": provider_data.get("type", "-"),
                "base_url": provider_data.get("base_url", "-"),
                "api_key": _mask_key(api_key),
                "has_key": bool(api_key),
                "model_refs": ref_counts.get(provider_name, 0),
            }
        )
    return rows


def _provider_ref_counts(raw: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    models = raw.get("models", {})
    if not isinstance(models, dict):
        return counts
    for entry in models.values():
        if not isinstance(entry, dict):
            continue
        refs = entry.get("models", [])
        if not isinstance(refs, list):
            continue
        pinned_model = entry.get("pinned_model")
        if isinstance(pinned_model, dict) and pinned_model not in refs:
            refs = [*refs, pinned_model]
        for ref in refs:
            if isinstance(ref, dict) and "provider" in ref:
                provider = str(ref["provider"])
                counts[provider] = counts.get(provider, 0) + 1
    return counts


def _model_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    models = raw.get("models", {})
    if not isinstance(models, dict):
        return []
    rows: list[dict[str, Any]] = []
    for virtual_model, entry in sorted(models.items()):
        if not isinstance(entry, dict):
            continue
        refs = cast(dict[str, Any], entry).get("models", [])
        if not isinstance(refs, list):
            continue
        model_refs = [
            cast(dict[str, Any], ref) for ref in refs if isinstance(ref, dict)
        ]
        for index, model_ref in enumerate(model_refs):
            rows.append(
                {
                    "virtual_model": virtual_model,
                    "priority": index + 1,
                    "provider": model_ref.get("provider", "-"),
                    "model": model_ref.get("model", "-"),
                }
            )
    return rows


def _connect_readonly_db(db_path: str) -> sqlite3.Connection | None:
    path = Path(db_path)
    if not path.exists():
        print(f"错误: 数据库文件不存在: {path}", file=sys.stderr)
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        print(f"错误: 打开数据库失败: {exc}", file=sys.stderr)
        return None
    conn.row_factory = sqlite3.Row
    return conn


def _query_calls(
    conn: sqlite3.Connection,
    *,
    limit: int,
    model: str | None,
    status: str | None,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if model:
        conditions.append("virtual_model = ?")
        params.append(model)
    if status:
        conditions.append("status = ?")
        params.append(status)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    rows = conn.execute(
        f"""SELECT id, timestamp, virtual_model, provider_name, provider_type,
            provider_model, status, latency_ms, input_tokens, output_tokens,
            cache_read_tokens, cache_write_tokens,
            input_price_per_million, output_price_per_million,
            cache_read_price_per_million, cache_write_price_per_million,
            cost_usd, error_type, error_message
            FROM calls {where}
            ORDER BY timestamp DESC
            LIMIT ?""",
        [*params, limit],
    ).fetchall()
    return [dict(row) for row in rows]


def _query_call(conn: sqlite3.Connection, call_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
    return dict(row) if row else None


def _query_call_matches(conn: sqlite3.Connection, call_id: str) -> list[dict[str, Any]]:
    exact = _query_call(conn, call_id)
    if exact is not None:
        return [exact]

    rows = conn.execute(
        """SELECT * FROM calls
        WHERE substr(id, 1, ?) = ?
        ORDER BY timestamp DESC
        LIMIT 6""",
        (len(call_id), call_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _query_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """SELECT
            COUNT(*) AS total_calls,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
            SUM(input_tokens) AS total_input_tokens,
            SUM(output_tokens) AS total_output_tokens,
            SUM(cache_read_tokens) AS total_cache_read,
            SUM(cache_write_tokens) AS total_cache_write,
            SUM(cost_usd) AS total_cost_usd,
            AVG(CASE WHEN status = 'success' THEN latency_ms END) AS avg_latency_ms
        FROM calls"""
    ).fetchone()
    data = dict(row)
    total = data["total_calls"] or 0
    success = data["success_count"] or 0
    return {
        "total_calls": total,
        "success_count": success,
        "error_count": total - success,
        "success_rate": round(success / total * 100, 2) if total else 0,
        "total_input_tokens": data["total_input_tokens"] or 0,
        "total_output_tokens": data["total_output_tokens"] or 0,
        "total_cache_read": data["total_cache_read"] or 0,
        "total_cache_write": data["total_cache_write"] or 0,
        "total_cost_usd": round(data["total_cost_usd"] or 0, 6),
        "avg_latency_ms": round(data["avg_latency_ms"] or 0),
    }


def _print_table(headers: list[str], rows: list[list[Any]]) -> None:
    string_rows = [[_format_cell(value) for value in row] for row in rows]
    widths = [
        max([len(header), *(len(row[index]) for row in string_rows)])
        for index, header in enumerate(headers)
    ]
    fmt = "  ".join(f"{{:<{width}}}" for width in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * width for width in widths]))
    for row in string_rows:
        print(fmt.format(*row))


def _print_key_values(values: dict[str, Any]) -> None:
    if not values:
        print("(空)")
        return
    key_width = max(len(str(key)) for key in values)
    for key, value in values.items():
        print(f"{key:<{key_width}}  {_format_detail_value(value)}")


def _format_cell(value: Any) -> str:
    text = "-" if value is None else str(value)
    return text.replace("\n", "\\n")


def _format_detail_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return "-"
    return str(value)


def _short_time(timestamp: str | None) -> str:
    if not timestamp:
        return "-"
    return timestamp[:19]


def _check(status: str, name: str, message: str) -> dict[str, str]:
    return {"status": status, "name": name, "message": message}


def _check_path_parent(name: str, parent: Path, success_message: str) -> dict[str, str]:
    if parent == Path("."):
        parent = Path.cwd()
    if parent.exists():
        return _check("ok", name, success_message)
    return _check("warn", name, f"目录不存在: {parent}")


if __name__ == "__main__":
    main()

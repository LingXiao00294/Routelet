from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version

from routelet.cli.server import command_start


def main(argv: Sequence[str] | None = None) -> None:
    """Start the combined service and preserve its process exit code."""
    raise SystemExit(run(argv))


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口必须在 1–65535 之间")
    return port


def run(argv: Sequence[str] | None = None) -> int:
    """Parse startup options; all management happens in the Dashboard."""
    try:
        package_version = version("routelet")
    except PackageNotFoundError:
        package_version = "0.1.0"
    parser = argparse.ArgumentParser(
        prog="routelet",
        description="启动 Routelet API 和 Dashboard，并打开浏览器。",
    )
    parser.add_argument("--version", action="version", version=package_version)
    parser.add_argument("-c", "--config", default="config.toml", help="配置文件路径")
    parser.add_argument("--db", default="calls.db", help="调用记录数据库路径")
    parser.add_argument("--host", help="覆盖 server.host")
    parser.add_argument("-p", "--port", type=_port, help="覆盖 server.port")
    parser.add_argument("--env-file", default=".env", help="环境变量文件路径")
    parser.add_argument("--no-env-file", action="store_true", help="不加载环境变量文件")
    parser.add_argument("--dist", help="Dashboard 静态文件目录")
    parser.add_argument("--no-browser", action="store_true", help="启动后不打开浏览器")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="允许监听非回环地址（无内置鉴权，仅限受信网络）",
    )
    try:
        args = parser.parse_args(argv)
        return command_start(
            config_path=args.config,
            db=args.db,
            host=args.host,
            port=args.port,
            env_file=args.env_file,
            no_env_file=args.no_env_file,
            dist_path=args.dist,
            no_browser=args.no_browser,
            allow_remote=args.allow_remote,
        )
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    except (OSError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130

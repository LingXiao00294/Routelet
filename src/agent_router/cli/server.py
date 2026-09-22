from __future__ import annotations

import asyncio
import socket
import sys
import webbrowser
from ipaddress import ip_address

import uvicorn

from agent_router.cli.config_io import load_startup_config, unresolved_runtime_providers
from agent_router.dashboard import find_dashboard_dist, mount_dashboard
from agent_router.db import CallStore
from agent_router.monitoring import setup_logging


class BrowserServer(uvicorn.Server):
    """Open the Dashboard only after Uvicorn has successfully bound its socket."""

    def __init__(self, config: uvicorn.Config, browser_url: str | None) -> None:
        super().__init__(config)
        self.browser_url = browser_url

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        if self.started and not self.should_exit and self.browser_url:
            try:
                opened = await asyncio.to_thread(webbrowser.open, self.browser_url)
            except Exception:
                opened = False
            if not opened:
                print(
                    f"无法自动打开浏览器，请访问: {self.browser_url}", file=sys.stderr
                )


def _browser_url(host: str, port: int) -> str:
    host = host.strip().strip("[]")
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1" if host == "0.0.0.0" else "::1"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}"


def command_start(
    *,
    config_path: str,
    env_file: str,
    no_env_file: bool,
    host: str | None,
    port: int | None,
    db: str,
    allow_remote: bool,
    dist_path: str | None,
    no_browser: bool,
) -> int:
    """Run API routes and built Dashboard assets in one process and port."""
    dist = find_dashboard_dist(dist_path)
    if dist is None:
        print(
            "错误: 未找到 Dashboard 静态文件。请在 dashboard 目录执行 "
            "`bun install --frozen-lockfile` 和 `bun run build` 后重新安装，"
            "或使用 --dist 指向构建目录。",
            file=sys.stderr,
        )
        return 1
    config = load_startup_config(
        config_path,
        env_file=env_file,
        no_env_file=no_env_file,
    )
    if host is not None:
        config.server.host = host.strip().strip("[]")
    if port is not None:
        config.server.port = port
    if not _allow_bind(config.server.host, allow_remote):
        return 2
    unresolved = unresolved_runtime_providers(config)
    if unresolved:
        print(
            "警告: 以下 provider 的 api_key 未解析，路由请求会跳过它们: "
            + ", ".join(unresolved),
            file=sys.stderr,
        )
    setup_logging(
        level=config.server.log_level,
        log_file=config.server.log_file,
        log_max_bytes=config.server.log_max_bytes,
        log_backup_count=config.server.log_backup_count,
    )
    from agent_router.app import create_app

    app = create_app(config, CallStore(db), config_path=config_path)
    mount_dashboard(app, dist)
    url = _browser_url(config.server.host, config.server.port)
    print(f"Agent Router API / Dashboard: {url}")
    print(f"配置文件: {config_path}")
    print(f"数据库: {db}")
    server = BrowserServer(
        uvicorn.Config(
            app,
            host=config.server.host,
            port=config.server.port,
            log_level=config.server.log_level,
            access_log=False,
            log_config=None,
        ),
        browser_url=None if no_browser else url,
    )
    server.run()
    return 0 if server.started else 1


def _allow_bind(host: str, allow_remote: bool) -> bool:
    """Require explicit opt-in before exposing the unauthenticated service."""
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
        "会暴露调用正文、配置写入和 Provider 调用能力"
    )
    if not allow_remote:
        print(
            f"错误: {warning}。如已部署外部鉴权，请显式添加 --allow-remote",
            file=sys.stderr,
        )
        return False
    print(f"警告: {warning}；请确保仅位于受信网络或鉴权反向代理之后", file=sys.stderr)
    return True

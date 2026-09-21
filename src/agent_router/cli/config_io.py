from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from agent_router.config import AppConfig, has_unresolved_env_var, load_config


_INITIAL_CONFIG = """# 在 Dashboard 中添加 Provider 和虚拟模型。
[server]
host = "127.0.0.1"
port = 9456

[providers]

[models]
"""


def load_startup_config(
    config_path: str, *, env_file: str, no_env_file: bool
) -> AppConfig:
    """Create an empty first-run config without overwriting existing files."""
    if not no_env_file and env_file:
        load_dotenv(env_file)
    path = Path(config_path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as file:
                file.write(_INITIAL_CONFIG)
        except FileExistsError:
            pass
        else:
            print(f"已生成配置文件: {path}；请在 Dashboard 中添加 Provider 和模型。")
    return load_config(path, allow_unresolved_api_keys=True)


def unresolved_runtime_providers(config: AppConfig) -> list[str]:
    return sorted(
        name
        for name, provider in config.providers.items()
        if has_unresolved_env_var(provider.api_key)
    )

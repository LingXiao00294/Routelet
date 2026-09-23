from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from routelet.config import AppConfig, has_unresolved_env_var, load_config
from routelet.paths import prepare_state_file, private_file_opener


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
        env_path = Path(env_file).expanduser()
        if env_path.is_file():
            prepare_state_file(env_path)
        load_dotenv(env_path)
    path = Path(config_path).expanduser()
    prepare_state_file(path)
    if not path.exists():
        try:
            with open(path, "x", encoding="utf-8", opener=private_file_opener) as file:
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

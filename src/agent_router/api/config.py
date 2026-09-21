from __future__ import annotations

import asyncio
import os
import re
import tomllib
from collections.abc import Awaitable, Callable
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from agent_router.config import AppConfig, ConfigError, has_unresolved_env_var
from agent_router.config import parse_config_data


class RuntimeReloadError(RuntimeError):
    """表示运行时配置切换失败，且旧运行时也未能完整恢复。"""


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def _is_key_masked(api_key: str) -> bool:
    """检查 api_key 是否为空、占位符或已脱敏。"""
    if not api_key or api_key == "${PLACEHOLDER}":
        return True
    if re.match(r"^\*+$", api_key):
        return True
    if len(api_key) > 8:
        middle = api_key[4:-4]
        if middle and all(char == "*" for char in middle):
            return True
    return False


def _safe_key_fields(api_key: str) -> dict[str, object]:
    expanded = os.path.expandvars(api_key)
    unresolved = has_unresolved_env_var(expanded)
    has_key = bool(expanded) and not unresolved
    return {
        "api_key": "" if unresolved else _mask_key(expanded),
        "has_key": has_key,
        "api_key_unresolved": unresolved,
    }


def _read_config_raw(config_path: str) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        raise HTTPException(500, f"配置文件不存在: {config_path}")
    try:
        with path.open("rb") as file:
            return tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise HTTPException(500, f"读取配置失败: {exc}") from exc


def _toml_escape(value: str) -> str:
    """转义 TOML basic string 中的特殊字符。"""
    escapes = {
        "\\": "\\\\",
        '"': '\\"',
        "\b": "\\b",
        "\t": "\\t",
        "\n": "\\n",
        "\f": "\\f",
        "\r": "\\r",
    }
    output: list[str] = []
    for character in value:
        if character in escapes:
            output.append(escapes[character])
        elif ord(character) <= 0x1F or ord(character) == 0x7F:
            output.append(f"\\u{ord(character):04X}")
        else:
            output.append(character)
    return "".join(output)


def _toml_key(name: str) -> str:
    """返回正确引用的 TOML key，处理含点号或方括号的名称。"""
    if re.match(r"^[A-Za-z0-9_-]+$", name):
        return name
    return f'"{_toml_escape(name)}"'


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{_toml_escape(value)}"'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        fields = ", ".join(
            f"{_toml_key(str(key))} = {_toml_value(item)}"
            for key, item in value.items()
            if item is not None
        )
        return f"{{ {fields} }}"
    raise TypeError(f"不支持的 TOML 值类型: {type(value).__name__}")


def _append_simple_fields(
    lines: list[str], data: dict[str, Any], *, excluded: set[str] | None = None
) -> None:
    excluded = excluded or set()
    for key, value in data.items():
        if key in excluded or value is None:
            continue
        lines.append(f"{_toml_key(str(key))} = {_toml_value(value)}")


def _serialize_toml(data: dict[str, Any]) -> str:
    """将规范配置序列化为目标 TOML 格式，不执行文件写入。"""
    lines: list[str] = []

    lines.append("[server]")
    _append_simple_fields(lines, data.get("server", {}))
    lines.append("")

    lines.append("[router]")
    _append_simple_fields(lines, data.get("router", {}))
    lines.append("")

    providers = data.get("providers", {})
    for provider_name, provider in providers.items():
        provider_key = _toml_key(str(provider_name))
        lines.append(f"[providers.{provider_key}]")
        _append_simple_fields(lines, provider, excluded={"models"})
        lines.append("")

        for model_name, actual_model in provider.get("models", {}).items():
            model_key = _toml_key(str(model_name))
            lines.append(f"[providers.{provider_key}.models.{model_key}]")
            _append_simple_fields(lines, actual_model)
            lines.append("")

    for virtual_name, virtual_model in data.get("models", {}).items():
        virtual_key = _toml_key(str(virtual_name))
        lines.append(f"[models.{virtual_key}]")
        pinned_model = virtual_model.get("pinned_model")
        if pinned_model is not None:
            lines.append(f"pinned_model = {_toml_value(pinned_model)}")
        lines.append("models = [")
        for model_ref in virtual_model.get("models", []):
            lines.append(f"  {_toml_value(model_ref)},")
        lines.append("]")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _replace_file(path: Path, content: bytes) -> None:
    """Atomically replace a file through a unique sibling temporary file."""
    tmp_path: Path | None = None
    try:
        # 唯一同目录文件同时满足 os.replace 的原子性，并避免多个进程或测试实例
        # 共用固定 ``config.toml.tmp`` 时互相覆盖。
        with NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            tmp_file.write(content)
        tmp_path.replace(path)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


async def _update_config_transaction(
    config_path: str,
    body: dict[str, Any],
    reload_config_fn: Callable[[AppConfig], Awaitable[None]] | None,
) -> dict[str, str] | JSONResponse:
    """Validate, persist, and reload one configuration transaction.

    The caller must serialize invocations that share ``config_path`` so the
    runtime reload and any rollback remain ordered with the corresponding disk
    replacement.

    Args:
        config_path: Configuration file updated by this transaction.
        body: Candidate API payload, possibly containing masked key values.
        reload_config_fn: Optional callback that applies the validated runtime.

    Returns:
        A success payload, or a fixed conflict response for referenced deletes.

    Raises:
        HTTPException: If validation, persistence, reload, or rollback fails.
    """
    path = Path(config_path)
    existing = _read_config_raw(config_path)
    original_bytes = path.read_bytes()

    try:
        candidate = _merge_and_preserve_keys(body, existing)
        conflict = _deletion_conflict(existing, candidate)
        if conflict is not None:
            return JSONResponse(status_code=409, content={"error": conflict})
        content = _serialize_toml(candidate)
        round_trip = tomllib.loads(content)
        runtime_config = parse_config_data(round_trip, allow_unresolved_api_keys=True)
    except ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise HTTPException(400, f"配置序列化失败: {exc}") from exc

    try:
        _replace_file(path, content.encode("utf-8"))
    except OSError as exc:
        raise HTTPException(500, f"写入配置失败，旧配置保持不变: {exc}") from exc

    if reload_config_fn is not None:
        try:
            await reload_config_fn(runtime_config)
        except Exception as exc:
            try:
                _replace_file(path, original_bytes)
            except OSError as rollback_exc:
                raise HTTPException(
                    500,
                    "运行时切换失败，且配置文件回滚失败: "
                    f"{exc}; rollback: {rollback_exc}",
                ) from exc
            if isinstance(exc, RuntimeReloadError):
                detail = f"运行时切换失败，配置文件已回滚，但运行时回滚失败: {exc}"
            else:
                detail = f"运行时切换失败，配置文件与运行时已回滚: {exc}"
            raise HTTPException(500, detail) from exc

    return {"status": "ok", "message": "配置已更新并热重载"}


def _references_by_identity(
    raw: dict[str, Any],
) -> dict[tuple[str, str], set[str]]:
    references: dict[tuple[str, str], set[str]] = {}
    models = raw.get("models", {})
    if not isinstance(models, dict):
        return references
    for virtual_name, virtual_model in models.items():
        if not isinstance(virtual_model, dict):
            continue
        refs = list(virtual_model.get("models", []))
        pinned_model = virtual_model.get("pinned_model")
        if isinstance(pinned_model, dict):
            refs.append(pinned_model)
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            provider = ref.get("provider")
            model = ref.get("model")
            if isinstance(provider, str) and isinstance(model, str):
                references.setdefault((provider, model), set()).add(str(virtual_name))
    return references


def _deletion_conflict(
    existing: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any] | None:
    """返回必须分两次保存的 Provider 或实际模型删除冲突。"""
    existing_providers = existing.get("providers", {})
    candidate_providers = candidate.get("providers", {})
    if not isinstance(existing_providers, dict):
        raise ConfigError("现有配置的 providers 必须是对象")
    if not isinstance(candidate_providers, dict):
        raise ConfigError("providers 必须是对象")
    references = _references_by_identity(existing)

    for provider in sorted(set(existing_providers) - set(candidate_providers)):
        referenced_by = sorted(
            {
                virtual_name
                for (provider_name, _), names in references.items()
                if provider_name == provider
                for virtual_name in names
            }
        )
        if referenced_by:
            return {
                "code": "provider_in_use",
                "provider": provider,
                "referenced_by": referenced_by,
            }

    for provider in sorted(set(existing_providers) & set(candidate_providers)):
        existing_provider = existing_providers[provider]
        candidate_provider = candidate_providers[provider]
        if not isinstance(existing_provider, dict):
            raise ConfigError(f"现有 Provider '{provider}' 配置必须是对象")
        if not isinstance(candidate_provider, dict):
            raise ConfigError(f"Provider '{provider}' 配置必须是对象")
        existing_models = existing_provider.get("models", {})
        candidate_models = candidate_provider.get("models", {})
        if not isinstance(existing_models, dict):
            raise ConfigError(f"现有 Provider '{provider}' 的 models 必须是对象")
        if not isinstance(candidate_models, dict):
            raise ConfigError(f"Provider '{provider}' 的 models 必须是对象")
        for model in sorted(set(existing_models) - set(candidate_models)):
            referenced_by = sorted(references.get((provider, model), set()))
            if referenced_by:
                return {
                    "code": "model_in_use",
                    "provider": provider,
                    "model": model,
                    "referenced_by": referenced_by,
                }
    return None


def _merge_and_preserve_keys(
    body: dict[str, Any], existing: dict[str, Any]
) -> dict[str, Any]:
    candidate = deepcopy(body)
    for section in ("server", "router", "providers", "models"):
        if section not in candidate:
            candidate[section] = deepcopy(existing.get(section, {}))

    existing_providers = existing.get("providers", {})
    if not isinstance(existing_providers, dict):
        raise ConfigError("现有配置的 providers 必须是对象")
    providers = candidate.get("providers", {})
    if not isinstance(providers, dict):
        raise ConfigError("providers 必须是对象")
    for provider_name, provider in providers.items():
        if not isinstance(provider, dict):
            raise ConfigError(f"Provider '{provider_name}' 配置必须是对象")
        provider.pop("has_key", None)
        provider.pop("api_key_unresolved", None)
        api_key = str(provider.get("api_key", ""))
        if not _is_key_masked(api_key):
            continue
        if provider_name not in existing_providers:
            raise ConfigError(f"新建 Provider '{provider_name}' 需要提供有效的 api_key")
        existing_provider = existing_providers[provider_name]
        if not isinstance(existing_provider, dict):
            raise ConfigError(f"现有 Provider '{provider_name}' 配置必须是对象")
        provider["api_key"] = existing_provider.get("api_key", api_key)
    return candidate


def _safe_config(raw: dict[str, Any]) -> dict[str, Any]:
    safe = deepcopy(raw)
    providers = safe.get("providers", {})
    if not isinstance(providers, dict):
        raise ConfigError("providers 必须是对象")
    for provider_name, provider in providers.items():
        if not isinstance(provider, dict):
            raise ConfigError(f"Provider '{provider_name}' 配置必须是对象")
        if "api_key" in provider:
            provider.update(_safe_key_fields(str(provider["api_key"])))
    return safe


def _read_safe_config(config_path: str) -> dict[str, Any]:
    """Read a config document and return its API-key-safe representation."""
    try:
        return _safe_config(_read_config_raw(config_path))
    except ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc


def create_config_router(
    config_path: str,
    reload_config_fn: Callable[[AppConfig], Awaitable[None]] | None = None,
) -> APIRouter:
    """创建配置读取与原子更新 API。"""
    router = APIRouter(tags=["config"])
    update_lock = asyncio.Lock()

    @router.get("/api/config")
    async def get_config():
        """返回完整规范配置，并对 api_key 脱敏。"""
        async with update_lock:
            return _read_safe_config(config_path)

    @router.get("/api/config/providers")
    async def list_providers():
        """返回包含实际模型目录的 Provider 配置，并对 api_key 脱敏。"""
        async with update_lock:
            raw = _read_safe_config(config_path)
            return raw.get("providers", {})

    @router.get("/api/config/models")
    async def list_models():
        """返回有序 ModelRef 与单一结构化 pinned_model。"""
        async with update_lock:
            return _read_config_raw(config_path).get("models", {})

    @router.put("/api/config")
    async def update_config(body: dict[str, Any]):
        """Serialize config writes so disk, runtime, and rollback stay ordered."""
        async with update_lock:
            return await _update_config_transaction(
                config_path,
                body,
                reload_config_fn,
            )

    return router

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog
from structlog.contextvars import merge_contextvars
from structlog.dev import ConsoleRenderer, plain_traceback
from structlog.processors import (
    JSONRenderer,
    StackInfoRenderer,
    TimeStamper,
    add_log_level,
    format_exc_info,
)
from structlog.stdlib import LoggerFactory, ProcessorFormatter

from agent_router.config import (
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_FILE,
    DEFAULT_LOG_MAX_BYTES,
)

# 命中即对值脱敏的敏感键名（小写精确匹配）。
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "token",
        "password",
        "passwd",
        "secret",
        "x-api-key",
        "set-cookie",
        "cookie",
        "access_token",
        "refresh_token",
        "id_token",
        "auth_token",
        "session_token",
        "client_secret",
        "api_secret",
        "secret_key",
        "access_key",
        "private_key",
    }
)

# 敏感键名后缀（小写）。键名以任一后缀结尾即视为敏感，覆盖未来新增的
# 复合命名（如 ``oauth_token``、``db_password``）。后缀自带分隔符或为
# 完整裸词，避免 ``token_count`` / ``tokenizer`` 等业务字段误伤。
_SENSITIVE_SUFFIXES = (
    "_token",
    "_secret",
    "_key",
    "-key",
    "apikey",
    "password",
    "authorization",
    "secret",
    "token",
)

# 脱敏后保留的前缀 / 后缀长度。
_KEEP_PREFIX = 4
_KEEP_SUFFIX = 2

# stdout 单行摘要中，单个字符串字段值的最大字符数（超出截断）。
_STDOUT_VALUE_MAX = 120


def _redact(value: Any) -> str:
    """脱敏单个值：保留前 4 + 后 2 字符，中间以 *** 代替；过短则全 ***。"""
    s = str(value)
    if len(s) <= _KEEP_PREFIX + _KEEP_SUFFIX:
        return "***"
    return f"{s[:_KEEP_PREFIX]}***{s[-_KEEP_SUFFIX:]}"


def _is_sensitive(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    k = key.lower()
    if k in _SENSITIVE_KEYS:
        return True
    return any(k.endswith(s) for s in _SENSITIVE_SUFFIXES)


# _scrub 递归进入的容器类型（dict 之外的序列/集合）。
# JSONRenderer 会把 tuple 序列化为 JSON 数组，故 tuple 内的敏感字段同样
# 必须脱敏；set/frozenset 元素经 str() 渲染，递归保证其中 dict 元素也被处理。
_CONTAINER_TYPES = (dict, list, tuple, set, frozenset)


def _scrub(obj: Any) -> Any:
    """递归脱敏容器中键名敏感的字段（值脱敏，键名保留）。

    覆盖 dict / list / tuple / set / frozenset：tuple 经 JSONRenderer 序列化
    为 JSON 数组，若不递归其内 dict 的敏感字段会原样泄漏到日志文件。

    快速路径：当前层级既无敏感键又无嵌套容器时原样返回，避免在无敏感内容的
    高频日志上逐键重建字典。
    """
    if isinstance(obj, dict):
        if not any(
            _is_sensitive(k) or isinstance(v, _CONTAINER_TYPES) for k, v in obj.items()
        ):
            return obj
        return {
            k: (_redact(v) if _is_sensitive(k) else _scrub(v)) for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_scrub(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(_scrub(item) for item in obj)
    if isinstance(obj, set):
        return {_scrub(item) for item in obj}
    if isinstance(obj, frozenset):
        return frozenset(_scrub(item) for item in obj)
    return obj


def redact_secrets(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    """structlog processor：递归脱敏事件字典中的敏感字段。"""
    return _scrub(event_dict)


def _brief_stdout_filter(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    """stdout 简洁化 processor：截断过长的字符串字段，并把 errors 列表折叠为摘要。

    仅作用于 stdout 渲染链；完整字段仍以 JSON 写入本地日志文件，便于事后排查。
    """
    errs = event_dict.get("errors")
    if isinstance(errs, list):
        event_dict["errors"] = f"<{len(errs)} 条，详见日志文件>"
    for k, v in list(event_dict.items()):
        if isinstance(v, str) and len(v) > _STDOUT_VALUE_MAX:
            event_dict[k] = v[:_STDOUT_VALUE_MAX] + "...(截断)"
    return event_dict


def _shared_processors() -> list:
    """structlog 与 stdlib 日志共用的前置 processor 链。"""
    return [
        merge_contextvars,
        add_log_level,
        TimeStamper(fmt="iso", utc=True),
        redact_secrets,
    ]


def setup_logging(
    level: str = "info",
    log_file: str = DEFAULT_LOG_FILE,
    log_max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    log_backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
) -> None:
    """配置结构化日志，可重复调用（热重载时复用）。

    输出双路：
    - stdout：彩色简洁单行（长字段截断、errors 折叠），便于终端实时浏览
    - 本地文件（log_file，默认 logs/agent-router.log，按大小轮转）：全量 JSON

    structlog / stdlib / uvicorn 日志经 ProcessorFormatter 统一走同一渲染管线；
    时间戳 UTC ISO，敏感字段自动脱敏。log_file 为空字符串时只输出到 stdout。
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared = _shared_processors()

    stdout_processors = [
        ProcessorFormatter.remove_processors_meta,
        _brief_stdout_filter,
        # Rich's default renderer includes frame locals, which can contain
        # upstream Authorization/x-api-key headers. Plain tracebacks retain
        # stack context without bypassing the structured-field redactor.
        ConsoleRenderer(colors=True, exception_formatter=plain_traceback),
    ]
    json_processors = [
        ProcessorFormatter.remove_processors_meta,
        StackInfoRenderer(),
        format_exc_info,
        JSONRenderer(),
    ]

    handlers: list[logging.Handler] = []
    try:
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(
            ProcessorFormatter(foreign_pre_chain=shared, processors=stdout_processors)
        )
        # stdout 固定 INFO+：debug 配置下第三方库（httpcore/aiosqlite 等）的
        # DEBUG 噪音不刷屏，仅以 INFO+ 业务日志进入终端；debug 全量仍写本地文件。
        stdout_handler.setLevel(max(log_level, logging.INFO))
        handlers.append(stdout_handler)

        if log_file:
            path = Path(log_file)
            if path.parent and not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                str(path),
                maxBytes=log_max_bytes,
                backupCount=log_backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(
                ProcessorFormatter(foreign_pre_chain=shared, processors=json_processors)
            )
            handlers.append(file_handler)
    except Exception:
        for handler in handlers:
            handler.close()
        raise

    # 所有新 handler 构造成功后才切换全局日志状态，避免失败时留下半配置。
    structlog.reset_defaults()
    structlog.configure(
        processors=[*shared, ProcessorFormatter.wrap_for_formatter],
        logger_factory=LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        # 关闭缓存，保证运行时热切换日志配置对所有 logger 实例生效。
        cache_logger_on_first_use=False,
    )

    # force=True 移除既有 handler 重新配置 root，避免热重载时 handler 累积。
    logging.basicConfig(handlers=handlers, level=log_level, force=True)

    structlog.get_logger("monitoring").info(
        "logging.configured",
        level=level,
        log_file=log_file or None,
        stdout="brief",
        file="full_json" if log_file else None,
    )


# 运行时热切换日志配置（供 PUT /api/config 热重载调用）。
# 与 setup_logging 行为完全一致，单独命名仅为在调用点表达「热重载」语义。
reconfigure_logging = setup_logging

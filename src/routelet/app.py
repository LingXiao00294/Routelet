from __future__ import annotations

import asyncio
import json
import math
import re
import time
import uuid
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from typing import Any, cast

import httpx
import structlog
from anyio import CancelScope
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from structlog.contextvars import (
    bind_contextvars,
    bound_contextvars,
    get_contextvars,
    unbind_contextvars,
)

from routelet.api.config import RuntimeReloadError, create_config_router
from routelet.api.metrics import create_metrics_router
from routelet.config import AppConfig
from routelet.db import CallStore
from routelet.monitoring import reconfigure_logging
from routelet.paths import resolve_path
from routelet.providers.anthropic_compat import FORWARDED_ANTHROPIC_HEADERS_KEY
from routelet.providers.base import (
    NonRetryableError,
    UpstreamHTTPError,
    UpstreamSSEError,
)
from routelet.recording import CallRecorder
from routelet.responses import ManagedStreamingResponse
from routelet.routing import (
    AllProvidersFailedError,
    NoProviderAvailableError,
    Router,
    UnknownModelError,
)
from routelet.sse import SSEDecoder, SSEEvent

logger = structlog.get_logger(__name__)


def _calculate_cost_usd(usage: Mapping[str, Any], outcome: Mapping[str, Any]) -> float:
    """Calculate request cost from usage and per-million-token prices.

    Missing usage values and prices contribute zero, preserving compatibility
    with providers and model references that do not expose pricing details.
    Scale prices before multiplication to avoid intermediate float overflow.
    """
    pricing = outcome.get("pricing")
    if not isinstance(pricing, Mapping):
        return 0.0

    token_prices = (
        ("input_tokens", "input"),
        ("output_tokens", "output"),
        ("cache_read_input_tokens", "cache_read"),
        ("cache_creation_input_tokens", "cache_write"),
    )
    total = sum(
        float(usage.get(token_key) or 0)
        * (float(pricing.get(price_key) or 0) / 1_000_000)
        for token_key, price_key in token_prices
    )
    return round(total, 10)


_PRICE_SNAPSHOT_FIELDS = (
    ("input_price_per_million", "input"),
    ("output_price_per_million", "output"),
    ("cache_read_price_per_million", "cache_read"),
    ("cache_write_price_per_million", "cache_write"),
)


def _price_snapshot_kwargs(outcome: Mapping[str, Any]) -> dict[str, Any]:
    """Build the four price-snapshot keyword arguments for store.record().

    Extracts the pricing mapping once and preserves ``None`` for unconfigured
    prices so they persist as SQL NULL rather than 0.
    """
    pricing = outcome.get("pricing")
    if not isinstance(pricing, Mapping):
        return {field: None for field, _ in _PRICE_SNAPSHOT_FIELDS}
    kwargs: dict[str, Any] = {}
    for field, key in _PRICE_SNAPSHOT_FIELDS:
        value = pricing.get(key)
        kwargs[field] = float(value) if value is not None else None
    return kwargs


def _failover_details(
    outcome: Mapping[str, Any], errors: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]] | None:
    """Preserve the same attempt fields across every error-recording path."""
    failures = outcome.get("_failures") or errors
    if not failures:
        return None
    return [
        {
            "provider": entry.get("provider"),
            "model": entry.get("model"),
            "error": entry.get("error"),
            "latency_ms": entry.get("latency_ms"),
        }
        for entry in failures
    ]


# 预取首字节最长等待：超时后仍先返回 SSE 响应头，避免代理因无头超时；
# 快速失败（冷却/容量/全失败）仍可在超时内转成 HTTP 429/503/502。
# 超时后的限流会变成流内 error（HTTP 200），属有意取舍。
_STREAM_FIRST_BYTE_PREFETCH_TIMEOUT = 5.0

# 合法 X-Request-ID：仅字母数字与 - _，长度 ≤128；违规则回退到生成的 uuid，
# 避免客户端注入超长/特殊字符污染日志与响应头。
_REQUEST_ID_MAX_LEN = 128
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# 与设计文档公开契约一致；流式读取会在超过上限的首个 chunk 立即停止，
# 避免超大正文先完整进入内存、Provider 调用和持久化队列。
_MAX_REQUEST_BODY_BYTES = 50 * 1024 * 1024
_FORWARDED_ANTHROPIC_HEADERS = ("anthropic-version", "anthropic-beta")


class RequestBodyTooLarge(ValueError):
    """Raised when an inbound Messages request exceeds the configured limit."""


def _sanitize_request_id(raw: str | None) -> str:
    """校验客户端透传的 X-Request-ID，非法则回退到新 uuid。"""
    if raw and _REQUEST_ID_RE.fullmatch(raw):
        return raw
    return str(uuid.uuid4())


def _record_error_type(error: Exception) -> str:
    """Return the stable call-history error type for a routing exception."""
    if isinstance(error, NoProviderAvailableError):
        return "overloaded_error" if error.kind == "capacity" else "rate_limit_error"
    if isinstance(error, AllProvidersFailedError):
        return "all_providers_failed"
    return type(error).__name__


def _stream_error_type(error: Exception) -> str:
    """Return the Anthropic-compatible error type emitted in an SSE stream."""
    if isinstance(error, NoProviderAvailableError):
        return "overloaded_error" if error.kind == "capacity" else "rate_limit_error"
    return "api_error"


def create_app(
    config: AppConfig,
    store: CallStore,
    config_path: str | None = None,
    *,
    call_recorder: CallRecorder | None = None,
) -> FastAPI:
    """Create the router application and its background call recorder."""
    config_path = str(resolve_path(config_path, "config.toml"))
    http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        timeout=httpx.Timeout(300.0, connect=10.0),
    )
    recorder = call_recorder or CallRecorder(store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await store.init()
            await recorder.start()
            logger.info(
                "server.start", host=config.server.host, port=config.server.port
            )
            yield
        finally:
            await recorder.close()
            await http_client.aclose()
            await store.close()
            logger.info("server.shutdown")

    app = FastAPI(
        title="Routelet",
        description="本地 LLM API 路由代理",
        version="0.1.0",
        lifespan=lifespan,
    )

    router_engine = Router(config, http_client)
    app.state.router_engine = router_engine

    # 注册 metrics API
    metrics_router = create_metrics_router(store)
    app.include_router(metrics_router)

    # 注册 config API
    def _apply_logging_config(runtime_config: AppConfig) -> None:
        """Apply the logging settings from a validated runtime config."""
        reconfigure_logging(
            level=runtime_config.server.log_level,
            log_file=runtime_config.server.log_file,
            log_max_bytes=runtime_config.server.log_max_bytes,
            log_backup_count=runtime_config.server.log_backup_count,
        )

    def _logging_settings(runtime_config: AppConfig) -> tuple[str, str, int, int]:
        """Return the fields that determine the active logging configuration."""
        server = runtime_config.server
        return (
            server.log_level,
            server.log_file,
            server.log_max_bytes,
            server.log_backup_count,
        )

    async def _reload_config(new_config: AppConfig) -> None:
        """切换日志与 Router 配置，并报告任何运行时回滚失败。"""
        old_config = app.state.router_engine.config
        logging_changed = _logging_settings(new_config) != _logging_settings(old_config)
        logging_switched = False
        try:
            if logging_changed:
                _apply_logging_config(new_config)
                logging_switched = True
            await app.state.router_engine.reload_config(new_config)
        except Exception as exc:
            rollback_errors: list[str] = []
            if app.state.router_engine.config is not old_config:
                try:
                    await app.state.router_engine.reload_config(old_config)
                except Exception as rollback_exc:
                    rollback_errors.append(f"Router: {rollback_exc}")
            if logging_switched:
                try:
                    _apply_logging_config(old_config)
                except Exception as rollback_exc:
                    rollback_errors.append(f"logging: {rollback_exc}")
            if rollback_errors:
                details = "; ".join(rollback_errors)
                raise RuntimeReloadError(f"{exc}; rollback: {details}") from exc
            raise

    config_router = create_config_router(config_path, reload_config_fn=_reload_config)
    app.include_router(config_router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/circuit-breaker")
    async def get_circuit_breaker_states():
        """返回所有 provider 的熔断状态."""
        states = await app.state.router_engine.circuit_breaker.get_all_states()
        return {name: state.value for name, state in states.items()}

    @app.post("/api/circuit-breaker/{provider:path}/reset")
    async def reset_circuit_breaker(provider: str):
        """重置指定 provider 的熔断状态."""
        await app.state.router_engine.circuit_breaker.reset(provider)
        return {"status": "ok", "provider": provider}

    @app.get("/v1/models")
    async def list_models():
        """Anthropic List Models 格式."""
        return {
            "data": [
                {
                    "id": name,
                    "type": "model",
                    "display_name": name,
                    "created_at": "2025-01-01T00:00:00Z",
                }
                for name in app.state.router_engine.model_names
            ]
        }

    @app.post("/v1/messages")
    async def messages(request: Request):
        start_time = time.time()
        try:
            body = await _read_message_body(request)
            _validate_message_fields(body)
        except RequestBodyTooLarge as exc:
            return _anthropic_error_response(str(exc), status_code=413)
        except ValueError as exc:
            return _anthropic_error_response(str(exc), status_code=400)

        virtual_model = cast(str, body["model"])
        is_stream = body.get("stream", False) is True
        upstream_body = _with_forwarded_anthropic_headers(body, request.headers)
        # 中间件已绑定 request_id；此处显式取出，供流式场景在中间件清理上下文后
        # 仍能把同一 request_id 贯穿到 routing 层日志：StreamingResponse 的 body 在
        # 中间件返回后才被 ASGI 消费，此时中间件已 unbind request_id，
        # routing 层会 fallback 到新 uuid 而与 http.request 日志断链。
        request_id = get_contextvars().get("request_id")
        engine: Router = request.app.state.router_engine
        result: Any = None

        try:
            if is_stream:
                outcome: dict = {}
                # sticky 等待 Provider 响应，保留错误 HTTP 状态；failover 有界预取，
                # 超时则先返回 SSE 头，继续在响应体中等待首字节。
                stream_agen: AsyncGenerator[bytes, None] = engine.route_stream(
                    upstream_body, outcome
                )
                try:
                    first_chunk, pending_first = await _prefetch_first_chunk(
                        stream_agen,
                        routing_outcome=outcome,
                    )
                except asyncio.CancelledError:
                    _record_cancelled_stream(
                        outcome=outcome,
                        recorder=recorder,
                        virtual_model=virtual_model,
                        request_body=body,
                        start_time=start_time,
                        request_id=request_id,
                    )
                    raise
                except NoProviderAvailableError as e:
                    await _close_prefetched_stream(stream_agen, None)
                    return await _no_provider_response(
                        e, recorder, virtual_model, body, start_time, outcome=outcome
                    )
                except AllProvidersFailedError as e:
                    await _close_prefetched_stream(stream_agen, None)
                    return await _all_failed_response(
                        e, recorder, virtual_model, body, start_time, outcome=outcome
                    )
                except UnknownModelError:
                    await _close_prefetched_stream(stream_agen, None)
                    raise

                recording_claimed = False

                async def _prepended() -> AsyncGenerator[bytes, None]:
                    nonlocal recording_claimed
                    # 从此处开始由 _stream_wrapper 负责记录最终结果。
                    recording_claimed = True
                    # 客户端中途断开时必须 aclose 预取的 generator，
                    # 否则 ProviderGate.slot / Provider 响应会一直占用到 GC。
                    try:
                        chunk = first_chunk
                        if pending_first is not None:
                            try:
                                chunk = await pending_first
                            except StopAsyncIteration:
                                chunk = None
                        if chunk is not None:
                            yield chunk
                        async for more in stream_agen:
                            yield more
                    finally:
                        await _close_prefetched_stream(stream_agen, pending_first)

                async def _close_response_stream() -> None:
                    """Close prefetch resources and record an unstarted response."""
                    nonlocal recording_claimed
                    try:
                        await _close_prefetched_stream(stream_agen, pending_first)
                    finally:
                        if not recording_claimed:
                            recording_claimed = True
                            usage: dict[str, Any] = {}
                            chunk = first_chunk
                            if (
                                chunk is None
                                and pending_first is not None
                                and pending_first.done()
                                and not pending_first.cancelled()
                            ):
                                try:
                                    chunk = pending_first.result()
                                except Exception:
                                    pass
                            if chunk is not None:
                                _update_stream_usage(
                                    SSEDecoder().feed(chunk), usage, set()
                                )
                            _record_cancelled_stream(
                                outcome=outcome,
                                recorder=recorder,
                                virtual_model=virtual_model,
                                request_body=body,
                                start_time=start_time,
                                request_id=request_id,
                                usage=usage,
                            )

                return ManagedStreamingResponse(
                    _stream_wrapper(
                        _prepended(),
                        outcome=outcome,
                        recorder=recorder,
                        virtual_model=virtual_model,
                        request_body=body,
                        start_time=start_time,
                        request_id=request_id,
                    ),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                    on_close=_close_response_stream,
                )
            else:
                outcome: dict = {}
                result = await engine.route_non_stream(upstream_body, outcome)
                if not isinstance(result, Mapping):
                    raise ValueError("Provider 响应必须是 JSON 对象")
                response = JSONResponse(result)
                latency_ms = int((time.time() - start_time) * 1000)
                usage = _validated_usage(result.get("usage"))
                recorder.submit(
                    virtual_model=virtual_model,
                    status="success",
                    provider_name=outcome.get("provider_name"),
                    provider_type=outcome.get("provider_type"),
                    provider_model=outcome.get("provider_model"),
                    provider_url=outcome.get("provider_url"),
                    attempt=outcome.get("attempt", 0),
                    latency_ms=latency_ms,
                    request_body=body,
                    response_body=result,
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                    cache_read_tokens=usage.get("cache_read_input_tokens"),
                    cache_write_tokens=usage.get("cache_creation_input_tokens"),
                    **_price_snapshot_kwargs(outcome),
                    cost_usd=_calculate_cost_usd(usage, outcome),
                    failover_details=outcome.get("_failures"),
                )
                return response

        except UnknownModelError as e:
            latency_ms = int((time.time() - start_time) * 1000)
            recorder.submit(
                virtual_model=virtual_model,
                status="error",
                error_type="unknown_model",
                error_message=str(e),
                attempt=outcome.get("attempt", 0),
                latency_ms=latency_ms,
                request_body=body,
                failover_details=outcome.get("_failures"),
            )
            return JSONResponse(
                {
                    "error": {
                        "type": "invalid_request_error",
                        "message": str(e),
                    }
                },
                status_code=400,
            )

        except NoProviderAvailableError as e:
            return await _no_provider_response(
                e, recorder, virtual_model, body, start_time, outcome=outcome
            )

        except AllProvidersFailedError as e:
            return await _all_failed_response(
                e, recorder, virtual_model, body, start_time, outcome=outcome
            )

        except UpstreamHTTPError as e:
            recorder.submit(
                virtual_model=virtual_model,
                status="error",
                error_type="upstream_http_error",
                error_message=str(e),
                attempt=outcome.get("attempt", 0),
                latency_ms=int((time.time() - start_time) * 1000),
                request_body=body,
                failover_details=outcome.get("_failures"),
            )
            return _upstream_error_response(e)

        except NonRetryableError as e:
            latency_ms = int((time.time() - start_time) * 1000)
            status_code = e.status_code or 502
            error_type = "invalid_request_error" if status_code < 500 else "api_error"
            recorder.submit(
                virtual_model=virtual_model,
                status="error",
                error_type=error_type,
                error_message=str(e),
                attempt=outcome.get("attempt", 0),
                latency_ms=latency_ms,
                request_body=body,
                failover_details=_failover_details(outcome),
            )
            logger.warning(
                "request.non_retryable_error",
                model=virtual_model,
                status_code=status_code,
                error=str(e),
            )
            return _anthropic_error_response(
                str(e),
                status_code=status_code,
                error_type=error_type,
            )

        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            usage = (
                _validated_usage(result.get("usage"))
                if isinstance(result, Mapping)
                else {}
            )
            recorder.submit(
                virtual_model=virtual_model,
                status="error",
                provider_name=outcome.get("provider_name"),
                provider_type=outcome.get("provider_type"),
                provider_model=outcome.get("provider_model"),
                provider_url=outcome.get("provider_url"),
                error_type=type(e).__name__,
                error_message=str(e),
                attempt=outcome.get("attempt", 0),
                latency_ms=latency_ms,
                request_body=body,
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                cache_read_tokens=usage.get("cache_read_input_tokens"),
                cache_write_tokens=usage.get("cache_creation_input_tokens"),
                **_price_snapshot_kwargs(outcome),
                cost_usd=_calculate_cost_usd(usage, outcome)
                if result is not None
                else None,
                failover_details=_failover_details(outcome),
            )
            logger.error(
                "request.error",
                model=virtual_model,
                error=str(e),
                exc_info=True,
            )
            return JSONResponse(
                {
                    "error": {
                        "type": "api_error",
                        "message": str(e),
                    }
                },
                status_code=502,
            )

    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next):
        """请求级中间件：注入 request_id 到日志上下文，并记录结构化请求日志。"""
        request_id = _sanitize_request_id(request.headers.get("x-request-id"))
        bind_contextvars(request_id=request_id)
        start = time.time()
        try:
            response = await call_next(request)
            # 注意：对 StreamingResponse，call_next 在响应对象创建后即返回（状态码
            # 200、首字节尚未发送），故此处 duration_ms 仅度量请求 setup / 首字节前
            # 耗时，status_code 恒为 200 即便流中途出错。流式真实耗时与最终状态以
            # _stream_wrapper 内异步提交的最终调用记录为准。
            logger.info(
                "http.request",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round((time.time() - start) * 1000),
            )
            response.headers.setdefault("X-Request-ID", request_id)
            return response
        except Exception:
            logger.error(
                "http.request_error",
                method=request.method,
                path=request.url.path,
                duration_ms=round((time.time() - start) * 1000),
                exc_info=True,
            )
            raise
        finally:
            # 仅解绑本中间件注入的 request_id，避免误清请求处理期间绑定的其它
            # 上下文（如 tenant / user id）；clear_contextvars 会清空全部 structlog 上下文。
            unbind_contextvars("request_id")

    return app


async def _read_message_body(request: Request) -> dict[str, Any]:
    """Read and validate one bounded JSON object from a Messages request.

    Args:
        request: Incoming FastAPI request whose body has not been consumed.

    Returns:
        The decoded top-level JSON object.

    Raises:
        RequestBodyTooLarge: If Content-Length or streamed bytes exceed 50 MiB.
        ValueError: If the body is not a JSON object or cannot be forwarded as
            finite UTF-8 JSON within the decoder and encoder's nesting limits.
    """
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError:
            declared_length = 0
        if declared_length > _MAX_REQUEST_BODY_BYTES:
            raise RequestBodyTooLarge(
                f"请求体超过 {_MAX_REQUEST_BODY_BYTES // (1024 * 1024)} MiB 上限"
            )

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _MAX_REQUEST_BODY_BYTES:
            raise RequestBodyTooLarge(
                f"请求体超过 {_MAX_REQUEST_BODY_BYTES // (1024 * 1024)} MiB 上限"
            )
        body.extend(chunk)

    try:
        payload = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise ValueError("请求体必须是有效的 JSON 对象") from exc
    del body
    if not isinstance(payload, dict):
        raise ValueError("请求体顶层必须是 JSON 对象")
    try:
        # HTTPX requires finite UTF-8 JSON. Validate before choosing a Provider
        # so client encoding errors cannot become upstream failures or attempts.
        # Match its encoder: iterencode() has a lower Python recursion limit and
        # would reject some deeply nested bodies that HTTPX can still forward.
        json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except (ValueError, RecursionError) as exc:
        raise ValueError("请求体包含无法编码的 JSON 值或嵌套过深") from exc
    return payload


def _with_forwarded_anthropic_headers(
    body: dict[str, Any], headers: Mapping[str, str]
) -> dict[str, Any]:
    """Attach selected Anthropic headers as trusted provider-only metadata.

    The returned copy is used only for routing. The original body remains free
    of internal metadata for call recording, and a client-supplied key matching
    the private metadata field cannot forge additional upstream headers.
    """
    upstream_body = {**body}
    upstream_body.pop(FORWARDED_ANTHROPIC_HEADERS_KEY, None)
    forwarded = {
        name: value
        for name in _FORWARDED_ANTHROPIC_HEADERS
        if (value := headers.get(name))
    }
    if forwarded:
        upstream_body[FORWARDED_ANTHROPIC_HEADERS_KEY] = forwarded
    return upstream_body


def _validate_message_fields(body: Mapping[str, Any]) -> None:
    """Validate routing-critical Messages fields before dictionary lookup.

    Raises:
        ValueError: If ``model`` is absent or not a non-empty string, or if a
            supplied ``stream`` value is not a JSON boolean.
    """
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model 必须是非空字符串")
    if "stream" in body and not isinstance(body["stream"], bool):
        raise ValueError("stream 必须是布尔值")


def _anthropic_error_response(
    message: str,
    *,
    status_code: int,
    error_type: str = "invalid_request_error",
) -> JSONResponse:
    """Build an Anthropic-compatible JSON error response."""
    return JSONResponse(
        {"error": {"type": error_type, "message": message}},
        status_code=status_code,
    )


def _upstream_error_response(error: UpstreamHTTPError) -> Response:
    """Preserve error payload and metadata, excluding transport-level headers.

    HTTPX has already decoded content encoding; Starlette computes the new
    content length. Only error-relevant end-to-end headers are forwarded.
    """
    response = Response(error.response.body, status_code=error.response.status_code)
    connection_headers = {
        token.strip().lower()
        for name, value in error.response.headers
        if name.lower() == "connection"
        for token in value.split(",")
    }
    for name, value in error.response.headers:
        name = name.lower()
        if name in connection_headers:
            continue
        if name in {
            "content-type",
            "content-language",
            "location",
            "retry-after",
            "www-authenticate",
            "request-id",
            "x-request-id",
        } or name.startswith(("ratelimit-", "x-ratelimit-", "anthropic-ratelimit-")):
            response.headers.append(name, value)
    return response


async def _prefetch_first_chunk(
    stream_agen: AsyncGenerator[bytes, None],
    *,
    timeout: float | None = None,
    routing_outcome: dict | None = None,
) -> tuple[bytes | None, asyncio.Task[bytes] | None]:
    """有界预取流式首 chunk，超时不取消 Provider 任务.

    ``timeout`` 默认读取模块常量（调用时求值，便于测试 monkeypatch）。
    仅实际开始的 failover 尝试可提前发送 SSE 头；sticky 或尚未选定 Provider 时
    等待首块，避免惰性迭代或热重载使预取策略与路由模式不一致。
    返回前调用方若取消或发生异常，负责取消并等待预取任务，然后关闭源流。

    Returns:
        ``(first_chunk, pending_task)``：
        - 超时内完成：``pending_task is None``，``first_chunk`` 可为 ``None``（空流）
        - 超时：``first_chunk is None``，``pending_task`` 仍在跑，由调用方在响应体中 await
        - 业务异常（限流/全失败等）：直接向上抛出
    """
    wait_for = _STREAM_FIRST_BYTE_PREFETCH_TIMEOUT if timeout is None else timeout
    task: asyncio.Task[bytes] = asyncio.create_task(anext(stream_agen))
    try:
        done, _pending = await asyncio.wait({task}, timeout=wait_for)
        if not done:
            if routing_outcome is None:
                return None, task
            if routing_outcome.get("_stream_mode") == "failover":
                # No await between checking the selected mode and committing it.
                routing_outcome["_stream_response_mode"] = "failover"
                return None, task
            await asyncio.wait({task})
        try:
            return task.result(), None
        except StopAsyncIteration:
            return None, None
    except BaseException:
        # 在所有权交给响应之前，取消调用方也必须回收独立的 anext 任务。
        await _close_prefetched_stream(stream_agen, task)
        raise


async def _close_prefetched_stream(
    stream_agen: AsyncGenerator[bytes, None],
    pending_first: asyncio.Task[bytes] | None,
) -> None:
    """屏蔽外层取消，等待预取任务退出后幂等关闭源流，避免竞态 RuntimeError."""
    with CancelScope(shield=True):
        if pending_first is not None:
            if not pending_first.done():
                pending_first.cancel()
            try:
                await pending_first
            except (asyncio.CancelledError, StopAsyncIteration, Exception):
                pass
        await stream_agen.aclose()


async def _no_provider_response(
    e: NoProviderAvailableError,
    recorder: CallRecorder,
    virtual_model: str,
    body: dict,
    start_time: float,
    *,
    outcome: Mapping[str, Any],
) -> JSONResponse:
    """Record unavailability using the accumulated count of upstream calls."""
    latency_ms = int((time.time() - start_time) * 1000)
    failover = _failover_details(outcome, e.errors)
    status_code = 503 if e.kind == "capacity" else 429
    error_type = _record_error_type(e)
    recorder.submit(
        virtual_model=virtual_model,
        status="error",
        attempt=outcome.get("attempt", 0),
        error_type=error_type,
        error_message=str(e),
        latency_ms=latency_ms,
        request_body=body,
        failover_details=failover,
    )
    headers = {}
    if e.retry_after is not None and e.retry_after > 0:
        headers["Retry-After"] = str(max(1, math.ceil(e.retry_after)))
    return JSONResponse(
        {
            "error": {
                "type": error_type,
                "message": str(e),
            }
        },
        status_code=status_code,
        headers=headers,
    )


async def _all_failed_response(
    e: AllProvidersFailedError,
    recorder: CallRecorder,
    virtual_model: str,
    body: dict,
    start_time: float,
    *,
    outcome: Mapping[str, Any],
) -> JSONResponse:
    """Record exhausted routing without counting skipped providers as calls."""
    latency_ms = int((time.time() - start_time) * 1000)
    failover = _failover_details(outcome, e.errors)
    recorder.submit(
        virtual_model=virtual_model,
        status="error",
        attempt=outcome.get("attempt", 0),
        error_type=_record_error_type(e),
        error_message=str(e),
        latency_ms=latency_ms,
        request_body=body,
        failover_details=failover,
    )
    return JSONResponse(
        {
            "error": {
                "type": "api_error",
                "message": str(e),
            }
        },
        status_code=502,
    )


def _validated_usage(value: Any) -> dict[str, int]:
    """Return token counts safe for accounting and SQLite integer storage.

    Ignore non-object metadata and malformed fields while retaining valid
    counts independently. Never modify the original upstream response.
    """
    if not isinstance(value, Mapping):
        return {}
    return {
        name: count
        for name in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
        if isinstance(count := value.get(name), int)
        and not isinstance(count, bool)
        and 0 <= count <= 2**63 - 1
    }


def _update_stream_usage(
    events: list[SSEEvent], usage: dict[str, Any], seen: set[str]
) -> None:
    """Merge first valid start usage and the largest cumulative delta counts.

    Accept only non-negative integer counts that fit SQLite's signed integers.
    Ignore malformed metadata so accounting cannot interrupt delivery, overwrite
    a valid count, or prevent persistence of the completed call.

    Args:
        events: Newly completed SSE events from the upstream stream.
        usage: Mutable aggregate updated with discovered token counts.
        seen: Event names already consumed for usage accounting.
    """
    for event in events:
        if event.event not in {"message_start", "message_delta"}:
            continue
        if event.event == "message_start" and event.event in seen:
            continue
        try:
            payload = json.loads(event.data)
        except (ValueError, RecursionError):
            # Accounting remains optional even when JSON hits integer or nesting limits.
            continue
        if not isinstance(payload, Mapping):
            continue

        if event.event == "message_start":
            message = payload.get("message")
            event_usage = message.get("usage") if isinstance(message, Mapping) else None
        else:
            event_usage = payload.get("usage")
        valid_usage = _validated_usage(event_usage)
        if valid_usage:
            if event.event == "message_delta":
                for name, count in valid_usage.items():
                    usage[name] = max(usage.get(name, 0), count)
            else:
                usage.update(valid_usage)
                seen.add(event.event)


def _record_cancelled_stream(
    *,
    outcome: Mapping[str, Any],
    recorder: CallRecorder,
    virtual_model: str,
    request_body: dict[str, Any],
    start_time: float,
    request_id: str | None,
    usage: Mapping[str, Any] | None = None,
) -> None:
    """Submit one cancellation with the available routing and token metadata.

    The endpoint owns recording before body iteration; the stream wrapper owns
    it afterwards. Restore the request context temporarily for callbacks that
    run after request middleware has already unbound it.
    """
    usage = usage if usage is not None else {}
    with bound_contextvars(request_id=request_id):
        recorder.submit(
            virtual_model=virtual_model,
            status="error",
            provider_name=outcome.get("provider_name"),
            provider_type=outcome.get("provider_type"),
            provider_model=outcome.get("provider_model"),
            provider_url=outcome.get("provider_url"),
            attempt=outcome.get("attempt", 0),
            error_type="client_cancelled",
            error_message="客户端在流完成前断开连接",
            latency_ms=int((time.time() - start_time) * 1000),
            request_body=request_body,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cache_read_tokens=usage.get("cache_read_input_tokens"),
            cache_write_tokens=usage.get("cache_creation_input_tokens"),
            **_price_snapshot_kwargs(outcome),
            cost_usd=_calculate_cost_usd(usage, outcome),
            failover_details=outcome.get("_failures"),
        )


async def _stream_wrapper(
    stream, *, outcome, recorder, virtual_model, request_body, start_time, request_id
):
    """包装流式响应，在流完成后提交调用记录，同时从 SSE 提取 usage.

    request_id 由端点显式传入：本 wrapper 在中间件清理 contextvars 之后才被
    ASGI 消费，需在此重新绑定，使 routing 层（route_stream 函数体在首次
    async for 时才执行）的日志与 http.request 共用同一 request_id。
    """
    usage_decoder = SSEDecoder()
    usage: dict[str, Any] = {}
    usage_events_seen: set[str] = set()
    recorded = False

    if request_id is not None:
        bind_contextvars(request_id=request_id)
    try:
        async for chunk in stream:
            _update_stream_usage(usage_decoder.feed(chunk), usage, usage_events_seen)

            # 先更新 usage 再交付 chunk；若客户端在发送期间断开，取消记录仍能
            # 保留这个已从 Provider 收到的 chunk 中的 token 信息。
            yield chunk

        # 流成功完成
        latency_ms = int((time.time() - start_time) * 1000)
        recorder.submit(
            virtual_model=virtual_model,
            status="success",
            provider_name=outcome.get("provider_name"),
            provider_type=outcome.get("provider_type"),
            provider_model=outcome.get("provider_model"),
            provider_url=outcome.get("provider_url"),
            attempt=outcome.get("attempt", 0),
            latency_ms=latency_ms,
            request_body=request_body,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cache_read_tokens=usage.get("cache_read_input_tokens"),
            cache_write_tokens=usage.get("cache_creation_input_tokens"),
            **_price_snapshot_kwargs(outcome),
            cost_usd=_calculate_cost_usd(usage, outcome),
            failover_details=outcome.get("_failures"),
        )
        recorded = True
    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        errors = (
            e.errors
            if isinstance(e, (AllProvidersFailedError, NoProviderAvailableError))
            else None
        )
        failover = _failover_details(outcome, errors)
        record_error_type = _record_error_type(e)
        recorder.submit(
            virtual_model=virtual_model,
            status="error",
            provider_name=outcome.get("provider_name"),
            provider_type=outcome.get("provider_type"),
            provider_model=outcome.get("provider_model"),
            provider_url=outcome.get("provider_url"),
            attempt=outcome.get("attempt", 0),
            error_type=record_error_type,
            error_message=str(e),
            latency_ms=latency_ms,
            request_body=request_body,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cache_read_tokens=usage.get("cache_read_input_tokens"),
            cache_write_tokens=usage.get("cache_creation_input_tokens"),
            **_price_snapshot_kwargs(outcome),
            cost_usd=_calculate_cost_usd(usage, outcome),
            failover_details=failover,
        )
        recorded = True
        if isinstance(e, UpstreamSSEError):
            # Routing already yielded the original error frame; do not duplicate it.
            return
        err_type = _stream_error_type(e)
        error_body = json.dumps(
            {
                "type": "error",
                "error": {
                    "type": err_type,
                    "message": str(e),
                },
            }
        )
        yield f"event: error\ndata: {error_body}\n\n".encode()
    finally:
        try:
            if not recorded:
                _record_cancelled_stream(
                    outcome=outcome,
                    recorder=recorder,
                    virtual_model=virtual_model,
                    request_body=request_body,
                    start_time=start_time,
                    request_id=request_id,
                    usage=usage,
                )
        finally:
            try:
                # 即使记录失败，也必须释放 Provider 连接与 gate slot。
                await stream.aclose()
            except Exception:
                logger.warning(
                    "stream.close_failed",
                    model=virtual_model,
                    exc_info=True,
                )
            finally:
                # 解绑本 wrapper 绑定的 request_id，保持上下文对称清理。
                if request_id is not None:
                    unbind_contextvars("request_id")

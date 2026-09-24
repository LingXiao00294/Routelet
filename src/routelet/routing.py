from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Literal

import structlog
from structlog.contextvars import get_contextvars

from routelet.circuit_breaker import CircuitBreaker, CircuitPermit, CircuitState
from routelet.config import (
    AppConfig,
    ProviderConfig,
    VirtualModelConfig,
    has_unresolved_env_var,
)
from routelet.provider_gate import (
    ProviderCapacityError,
    ProviderCooldownError,
    ProviderGate,
)
from routelet.providers.anthropic_compat import AnthropicCompatProvider
from routelet.providers.base import (
    BaseProvider,
    NonRetryableError,
    RetryableError,
    UpstreamHTTPError,
    UpstreamSSEError,
)
from routelet.sse import SSEDecodeError, SSEDecoder, SSEEvent

logger = structlog.get_logger(__name__)

_RATE_LIMIT_ERROR_TYPES = {
    "rate_limit_error",
    "overloaded_error",
    "overloaded",
}

# Error types that should trigger failover (same as RETRYABLE_STATUSES)
_RETRYABLE_ERROR_TYPES = {
    "rate_limit_error",
    "overloaded_error",
    "api_error",
    "overloaded",
}

# Error types that should immediately circuit break (same as AUTH_STATUSES)
_AUTH_ERROR_TYPES = {
    "authentication_error",
    "permission_error",
}


class _RoutingConfigChanged(Exception):
    """Signal that a not-yet-started attempt must use a newer config snapshot."""


def _provider_pricing_snapshot(provider_cfg: ProviderConfig) -> dict[str, float | None]:
    return {
        "input": provider_cfg.input_price_per_million,
        "output": provider_cfg.output_price_per_million,
        "cache_read": provider_cfg.cache_read_price_per_million,
        "cache_write": provider_cfg.cache_write_price_per_million,
    }


def _raise_for_stream_error(event: SSEEvent) -> None:
    """Raise the routing error represented by a decoded SSE error event."""
    if event.event != "error":
        return

    try:
        data = json.loads(event.data)
        if not isinstance(data, dict):
            raise TypeError("error event data must be an object")
        error = data.get("error", {})
        if not isinstance(error, dict):
            raise TypeError("error field must be an object")
        error_type = error.get("type", "")
        error_message = error.get("message", "Unknown stream error")
        if not isinstance(error_type, str):
            raise TypeError("error type must be a string")
        if not isinstance(error_message, str):
            error_message = str(error_message)

        if error_type in _AUTH_ERROR_TYPES:
            raise RetryableError(
                f"Stream error ({error_type}): {error_message}",
                immediate_break=True,
            )
        if error_type in _RATE_LIMIT_ERROR_TYPES:
            raise RetryableError(
                f"Stream error ({error_type}): {error_message}",
                rate_limited=True,
            )
        if error_type in _RETRYABLE_ERROR_TYPES:
            raise RetryableError(f"Stream error ({error_type}): {error_message}")
        # Unknown error types are non-retryable (don't blindly failover)
        raise NonRetryableError(f"Stream error ({error_type}): {error_message}")
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
        # Malformed error event — non-retryable since we can't identify the type
        raise NonRetryableError(
            f"Stream error: {event.data.decode(errors='replace')[:500]}"
        ) from None


def _create_provider(config: ProviderConfig, http_client) -> BaseProvider:
    return AnthropicCompatProvider(config, http_client)


def _max_retry_after(errors: list[dict]) -> float | None:
    values = [
        e["retry_after"]
        for e in errors
        if isinstance(e.get("retry_after"), (int, float)) and e["retry_after"] > 0
    ]
    return max(values) if values else None


class Router:
    def __init__(self, config: AppConfig, http_client) -> None:
        self.config = config
        self._config_generation = 0
        self.http = http_client
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=config.router.failure_threshold,
            recovery_timeout=config.router.recovery_timeout,
        )
        self.provider_gate = ProviderGate()
        self.provider_gate.configure_from_models(config.models)

    async def reload_config(self, new_config: AppConfig) -> None:
        """热重载配置；关闭故障转移时清除熔断状态。"""
        if self.config.router.mode == "failover" and new_config.router.mode == "sticky":
            await self.circuit_breaker.reset_all()
        self._apply_config(new_config)

    def _apply_config(self, config: AppConfig) -> None:
        """Apply runtime settings after the provider gate accepts the config."""
        self.provider_gate.configure_from_models(config.models)
        self.circuit_breaker.failure_threshold = config.router.failure_threshold
        self.circuit_breaker.recovery_timeout = config.router.recovery_timeout
        self.config = config
        self._config_generation += 1

    def _ensure_config_generation(self, generation: int) -> None:
        """Reject an attempt whose Provider snapshot predates a hot reload."""
        if generation != self._config_generation:
            raise _RoutingConfigChanged

    async def _get_providers_for_generation(
        self, virtual_model: str, generation: int, outcome: dict
    ) -> list[ProviderConfig]:
        """Return candidates only when routing configuration stayed unchanged.

        Raises:
            _RoutingConfigChanged: If hot reload overlaps candidate discovery,
                including an error produced from the obsolete configuration.
        """
        try:
            providers = await self._get_providers(virtual_model)
        except UnknownModelError:
            self._ensure_config_generation(generation)
            raise
        except (AllProvidersFailedError, NoProviderAvailableError) as exc:
            self._ensure_config_generation(generation)
            outcome.setdefault("_failures", []).extend(
                {
                    "provider": error["provider"],
                    "model": error["model"],
                    "error": error["error"],
                }
                for error in exc.errors
            )
            raise
        self._ensure_config_generation(generation)
        return providers

    def _virtual_model(self, name: str) -> VirtualModelConfig:
        if name not in self.config.models:
            raise UnknownModelError(name, list(self.config.models.keys()))
        return self.config.models[name]

    async def _handle_provider_error(
        self,
        e: RetryableError | NonRetryableError,
        provider_cfg: ProviderConfig,
        request_id: str,
        p_start: float,
        errors: list[dict],
        outcome: dict | None,
        providers: list[ProviderConfig],
        i: int,
        *,
        permit: CircuitPermit | None,
        cooldown_generation: int | None,
        allow_failover: bool,
        circuit_enabled: bool,
        passthrough_errors: bool = False,
    ) -> None:
        """Shared error handling for both route methods.

        For NonRetryableError, re-raises after logging.
        Sticky mode preserves available upstream HTTP responses and SSE frames.
        """
        p_latency = (time.time() - p_start) * 1000
        is_retryable = isinstance(e, RetryableError)
        rate_limited = is_retryable and e.rate_limited
        retry_after: float | None = None

        if is_retryable and rate_limited:
            retry_after = self.provider_gate.enter_cooldown(
                provider_cfg.name,
                e.retry_after
                if e.retry_after is not None
                else provider_cfg.rate_limit_cooldown,
                generation=cooldown_generation,
            )
        elif is_retryable and circuit_enabled:
            await self.circuit_breaker.record_failure(
                provider_cfg.name,
                immediate=e.immediate_break,
                failure_threshold=provider_cfg.failure_threshold,
                permit=permit,
            )

        err_entry: dict = {
            "provider": provider_cfg.name,
            "model": provider_cfg.model,
            "priority": provider_cfg.priority,
            "error": str(e),
            "retryable": is_retryable,
            "rate_limited": rate_limited,
            "latency_ms": round(p_latency),
        }
        if retry_after is not None:
            err_entry["retry_after"] = retry_after
        errors.append(err_entry)

        if outcome is not None:
            outcome.setdefault("_failures", []).append(
                {
                    "provider": provider_cfg.name,
                    "model": provider_cfg.model,
                    "error": str(e),
                    "latency_ms": round(p_latency),
                }
            )

        log_fn = logger.warning if is_retryable else logger.error
        log_fn(
            "provider.fail",
            request_id=request_id,
            provider=provider_cfg.name,
            model=provider_cfg.model,
            error=str(e),
            retry=is_retryable,
            rate_limited=rate_limited,
            provider_latency_ms=round(p_latency),
        )

        if passthrough_errors:
            if e.upstream_response is not None:
                raise UpstreamHTTPError(str(e), e.upstream_response) from e
            if e.upstream_frame is not None:
                raise UpstreamSSEError(str(e), e.upstream_frame) from e

        if not is_retryable:
            raise e

        if not allow_failover:
            if rate_limited:
                raise StickyRateLimited(retry_after)
            # sticky：不转移；可重试失败记入 errors，由调用方循环结束后统一抛出
            return

        next_idx = i + 1
        if next_idx < len(providers):
            next_cfg = providers[next_idx]
            logger.info(
                "failover",
                request_id=request_id,
                from_provider=provider_cfg.name,
                from_model=provider_cfg.model,
                to_provider=next_cfg.name,
                to_model=next_cfg.model,
                reason="rate_limited" if rate_limited else "retryable_error",
                circuit_broken=e.immediate_break,
            )

    async def _record_gate_skip(
        self,
        e: ProviderCooldownError | ProviderCapacityError,
        provider_cfg: ProviderConfig,
        request_id: str,
        p_start: float,
        errors: list[dict],
        outcome: dict | None,
        providers: list[ProviderConfig],
        i: int,
        *,
        allow_failover: bool,
        virtual_model: str,
    ) -> None:
        p_latency = (time.time() - p_start) * 1000
        kind = "rate_limit" if isinstance(e, ProviderCooldownError) else "capacity"
        retry_after = e.retry_after
        err_entry: dict = {
            "provider": provider_cfg.name,
            "model": provider_cfg.model,
            "priority": provider_cfg.priority,
            "error": str(e),
            "retryable": True,
            "rate_limited": kind == "rate_limit",
            "capacity": kind == "capacity",
            "latency_ms": round(p_latency),
        }
        if retry_after is not None:
            err_entry["retry_after"] = retry_after
        errors.append(err_entry)

        if outcome is not None:
            outcome.setdefault("_failures", []).append(
                {
                    "provider": provider_cfg.name,
                    "model": provider_cfg.model,
                    "error": str(e),
                    "latency_ms": round(p_latency),
                }
            )

        logger.warning(
            "provider.gate_skip",
            request_id=request_id,
            provider=provider_cfg.name,
            model=provider_cfg.model,
            kind=kind,
            error=str(e),
            provider_latency_ms=round(p_latency),
        )

        if not allow_failover:
            raise NoProviderAvailableError(
                virtual_model,
                errors,
                kind=kind,
                retry_after=retry_after,
            )

        next_idx = i + 1
        if next_idx < len(providers):
            next_cfg = providers[next_idx]
            logger.info(
                "failover",
                request_id=request_id,
                from_provider=provider_cfg.name,
                from_model=provider_cfg.model,
                to_provider=next_cfg.name,
                to_model=next_cfg.model,
                reason=kind,
            )

    async def _record_circuit_skip(
        self,
        provider_cfg: ProviderConfig,
        request_id: str,
        p_start: float,
        errors: list[dict],
        outcome: dict | None,
        providers: list[ProviderConfig],
        i: int,
        *,
        allow_failover: bool,
    ) -> None:
        """Record a provider skipped because no circuit permit was available."""
        state = await self.circuit_breaker.state(
            provider_cfg.name,
            recovery_timeout=provider_cfg.recovery_timeout,
        )
        p_latency = (time.time() - p_start) * 1000
        message = (
            f"provider 熔断暂不可用 (state={state.value}, HALF_OPEN probe 可能正在执行)"
        )
        entry = {
            "provider": provider_cfg.name,
            "model": provider_cfg.model,
            "priority": provider_cfg.priority,
            "error": message,
            "retryable": True,
            "reason": "circuit_unavailable",
            "latency_ms": round(p_latency),
        }
        errors.append(entry)

        if outcome is not None:
            outcome.setdefault("_failures", []).append(
                {
                    "provider": provider_cfg.name,
                    "model": provider_cfg.model,
                    "error": message,
                    "latency_ms": round(p_latency),
                }
            )

        logger.info(
            "provider.circuit_skip",
            request_id=request_id,
            provider=provider_cfg.name,
            model=provider_cfg.model,
            state=state.value,
        )

        next_idx = i + 1
        if allow_failover and next_idx < len(providers):
            next_cfg = providers[next_idx]
            logger.info(
                "failover",
                request_id=request_id,
                from_provider=provider_cfg.name,
                from_model=provider_cfg.model,
                to_provider=next_cfg.name,
                to_model=next_cfg.model,
                reason="circuit_unavailable",
            )

    async def route_non_stream(
        self, request_body: dict, outcome: dict | None = None
    ) -> dict:
        """非流式路由: 返回第一个成功 provider 的响应 JSON.

        outcome 可选字典，成功时写入最终 provider、模型、URL 和价格信息。
        attempt 只统计真正开始的 Provider 调用，在失败及热重载重选后仍累计保留。
        """
        outcome = outcome if outcome is not None else {}
        outcome["attempt"] = 0
        while True:
            generation = self._config_generation
            try:
                return await self._route_non_stream_once(
                    request_body,
                    outcome,
                    generation=generation,
                )
            except _RoutingConfigChanged:
                logger.info(
                    "request.reroute_config_reload",
                    model=request_body.get("model", ""),
                    stream=False,
                    from_generation=generation,
                    to_generation=self._config_generation,
                )

    async def _route_non_stream_once(
        self,
        request_body: dict,
        outcome: dict,
        *,
        generation: int,
    ) -> dict:
        """Route once against one immutable configuration generation."""
        virtual_model = request_body.get("model", "")
        providers = await self._get_providers_for_generation(
            virtual_model, generation, outcome
        )
        allow_failover = self.config.router.mode == "failover"

        request_id = get_contextvars().get("request_id") or str(uuid.uuid4())
        start_time = time.time()
        errors: list[dict] = []

        logger.info(
            "request.start",
            request_id=request_id,
            model=virtual_model,
            stream=False,
            mode=self.config.router.mode,
            provider_count=len(providers),
        )

        for i, provider_cfg in enumerate(providers):
            p_start = time.time()
            permit: CircuitPermit | None = None
            cooldown_generation: int | None = None

            try:
                async with self.provider_gate.slot(provider_cfg) as cooldown_generation:
                    if allow_failover:
                        permit = await self.circuit_breaker.try_acquire(
                            provider_cfg.name,
                            recovery_timeout=provider_cfg.recovery_timeout,
                        )
                        if permit is None:
                            await self._record_circuit_skip(
                                provider_cfg,
                                request_id,
                                p_start,
                                errors,
                                outcome,
                                providers,
                                i,
                                allow_failover=allow_failover,
                            )
                            continue

                    self._ensure_config_generation(generation)
                    attempt = outcome["attempt"] + 1
                    outcome["attempt"] = attempt
                    logger.info(
                        "provider.try",
                        request_id=request_id,
                        provider=provider_cfg.name,
                        model=provider_cfg.model,
                        priority=provider_cfg.priority,
                        attempt=attempt,
                        circuit_probe=permit.probe if permit is not None else False,
                    )
                    provider = _create_provider(provider_cfg, self.http)
                    result = await provider.send(request_body)
                    p_latency = (time.time() - p_start) * 1000
                    total_latency = (time.time() - start_time) * 1000

                    if permit is not None:
                        await self.circuit_breaker.record_success(
                            provider_cfg.name,
                            permit=permit,
                        )

                    logger.info(
                        "provider.success",
                        request_id=request_id,
                        provider=provider_cfg.name,
                        model=provider_cfg.model,
                        attempt=attempt,
                        provider_latency_ms=round(p_latency),
                        total_latency_ms=round(total_latency),
                    )

                    if outcome is not None:
                        outcome["provider_type"] = provider_cfg.type
                        outcome["provider_name"] = provider_cfg.name
                        outcome["provider_model"] = provider_cfg.model
                        outcome["provider_url"] = provider_cfg.base_url
                        outcome["attempt"] = attempt
                        outcome["pricing"] = _provider_pricing_snapshot(provider_cfg)

                    return result

            except (ProviderCooldownError, ProviderCapacityError) as e:
                self._ensure_config_generation(generation)
                await self._record_gate_skip(
                    e,
                    provider_cfg,
                    request_id,
                    p_start,
                    errors,
                    outcome,
                    providers,
                    i,
                    allow_failover=allow_failover,
                    virtual_model=virtual_model,
                )
            except (RetryableError, NonRetryableError) as e:
                try:
                    await self._handle_provider_error(
                        e,
                        provider_cfg,
                        request_id,
                        p_start,
                        errors,
                        outcome,
                        providers,
                        i,
                        permit=permit,
                        cooldown_generation=cooldown_generation,
                        allow_failover=allow_failover,
                        circuit_enabled=allow_failover,
                        passthrough_errors=not allow_failover,
                    )
                except StickyRateLimited as srl:
                    raise NoProviderAvailableError(
                        virtual_model,
                        errors,
                        kind="rate_limit",
                        retry_after=srl.retry_after,
                    ) from e
            finally:
                if permit is not None:
                    await self.circuit_breaker.release(permit)

        self._ensure_config_generation(generation)
        raise self._exhausted(virtual_model, errors, start_time, outcome["attempt"])

    async def route_stream(
        self, request_body: dict, outcome: dict | None = None
    ) -> AsyncGenerator[bytes, None]:
        """流式路由: 返回第一个成功 provider 的 SSE 流.

        outcome 可选字典，开始调用时写入 provider、模型、URL 和价格信息。
        attempt 只统计真正开始的 Provider 调用，在失败及热重载重选后仍累计保留。
        每个 SSE 事件完整校验后才交付，首个事件前的错误仍允许故障转移。
        """
        outcome = outcome if outcome is not None else {}
        outcome["attempt"] = 0
        client_started = False
        while True:
            generation = self._config_generation
            response_mode = outcome.get("_stream_response_mode")
            if response_mode is not None and self.config.router.mode != response_mode:
                raise RuntimeError("routing mode changed after stream response started")
            outcome.pop("_stream_mode", None)
            generation_stream = self._route_stream_once(
                request_body,
                outcome,
                generation=generation,
            )
            try:
                async for chunk in generation_stream:
                    client_started = True
                    yield chunk
                return
            except _RoutingConfigChanged:
                if client_started:
                    raise RuntimeError(
                        "routing configuration changed after stream output started"
                    ) from None
                logger.info(
                    "request.reroute_config_reload",
                    model=request_body.get("model", ""),
                    stream=True,
                    from_generation=generation,
                    to_generation=self._config_generation,
                )
            finally:
                await generation_stream.aclose()

    async def _route_stream_once(
        self,
        request_body: dict,
        outcome: dict,
        *,
        generation: int,
    ) -> AsyncGenerator[bytes, None]:
        """Route one stream against one immutable configuration generation."""
        virtual_model = request_body.get("model", "")
        providers = await self._get_providers_for_generation(
            virtual_model, generation, outcome
        )
        allow_failover = self.config.router.mode == "failover"

        request_id = get_contextvars().get("request_id") or str(uuid.uuid4())
        start_time = time.time()
        errors: list[dict] = []

        logger.info(
            "request.start",
            request_id=request_id,
            model=virtual_model,
            stream=True,
            mode=self.config.router.mode,
            provider_count=len(providers),
        )

        # 一旦向客户端 yield 过字节，禁止再 failover，避免拼接多路 SSE
        client_started = False

        for i, provider_cfg in enumerate(providers):
            p_start = time.time()
            permit: CircuitPermit | None = None
            cooldown_generation: int | None = None

            try:
                async with self.provider_gate.slot(provider_cfg) as cooldown_generation:
                    if allow_failover:
                        permit = await self.circuit_breaker.try_acquire(
                            provider_cfg.name,
                            recovery_timeout=provider_cfg.recovery_timeout,
                        )
                        if permit is None:
                            await self._record_circuit_skip(
                                provider_cfg,
                                request_id,
                                p_start,
                                errors,
                                outcome,
                                providers,
                                i,
                                allow_failover=allow_failover,
                            )
                            continue

                    self._ensure_config_generation(generation)
                    attempt = outcome["attempt"] + 1
                    outcome["attempt"] = attempt
                    logger.info(
                        "provider.try",
                        request_id=request_id,
                        provider=provider_cfg.name,
                        model=provider_cfg.model,
                        priority=provider_cfg.priority,
                        attempt=attempt,
                        circuit_probe=permit.probe if permit is not None else False,
                    )
                    provider = _create_provider(provider_cfg, self.http)
                    # 流可能在自然结束前因客户端断开而被关闭；候选一旦真正开始
                    # 调用就先暴露元数据，使取消记录仍能归因到实际 Provider。
                    if outcome is not None:
                        outcome["provider_type"] = provider_cfg.type
                        outcome["provider_name"] = provider_cfg.name
                        outcome["provider_model"] = provider_cfg.model
                        outcome["provider_url"] = provider_cfg.base_url
                        outcome["attempt"] = attempt
                        outcome["pricing"] = _provider_pricing_snapshot(provider_cfg)
                    error_decoder = SSEDecoder()
                    outcome["_stream_mode"] = "failover" if allow_failover else "sticky"
                    data_event_seen = False
                    async with aclosing(provider.send_stream(request_body)) as upstream:
                        async for chunk in upstream:
                            try:
                                frames = error_decoder.feed_frames(chunk)
                            except SSEDecodeError as exc:
                                raise NonRetryableError(
                                    f"Invalid SSE stream: {exc}"
                                ) from exc
                            for frame in frames:
                                if frame.event is not None:
                                    try:
                                        _raise_for_stream_error(frame.event)
                                    except (RetryableError, NonRetryableError) as e:
                                        e.upstream_frame = frame.raw
                                        raise
                                    data_event_seen = True
                                elif not client_started and allow_failover:
                                    # 初始 keepalive 不能决定使用哪个 Provider。
                                    continue
                                client_started = True
                                yield frame.raw
                    try:
                        error_decoder.finish()
                    except SSEDecodeError as exc:
                        raise NonRetryableError(f"Invalid SSE stream: {exc}") from exc
                    if not data_event_seen:
                        raise NonRetryableError(
                            "Upstream stream ended without an SSE data event"
                        )
                    p_latency = (time.time() - p_start) * 1000
                    total_latency = (time.time() - start_time) * 1000

                    if permit is not None:
                        await self.circuit_breaker.record_success(
                            provider_cfg.name,
                            permit=permit,
                        )

                    logger.info(
                        "provider.success",
                        request_id=request_id,
                        provider=provider_cfg.name,
                        model=provider_cfg.model,
                        attempt=attempt,
                        provider_latency_ms=round(p_latency),
                        total_latency_ms=round(total_latency),
                    )

                    return

            except (ProviderCooldownError, ProviderCapacityError) as e:
                self._ensure_config_generation(generation)
                can_failover = allow_failover and not client_started
                await self._record_gate_skip(
                    e,
                    provider_cfg,
                    request_id,
                    p_start,
                    errors,
                    outcome,
                    providers,
                    i,
                    allow_failover=can_failover,
                    virtual_model=virtual_model,
                )
                if client_started:
                    raise
            except (RetryableError, NonRetryableError) as e:
                can_failover = allow_failover and not client_started
                try:
                    await self._handle_provider_error(
                        e,
                        provider_cfg,
                        request_id,
                        p_start,
                        errors,
                        outcome,
                        providers,
                        i,
                        permit=permit,
                        cooldown_generation=cooldown_generation,
                        allow_failover=can_failover,
                        circuit_enabled=allow_failover,
                        passthrough_errors=not allow_failover,
                    )
                except UpstreamSSEError as upstream_error:
                    yield upstream_error.frame
                    raise
                except StickyRateLimited as srl:
                    raise NoProviderAvailableError(
                        virtual_model,
                        errors,
                        kind="rate_limit",
                        retry_after=srl.retry_after,
                    ) from e
                if client_started:
                    # 已向客户端发送数据后不再 failover；可重试错误也直接抛出
                    raise
            finally:
                if permit is not None:
                    await self.circuit_breaker.release(permit)

        self._ensure_config_generation(generation)
        raise self._exhausted(virtual_model, errors, start_time, outcome["attempt"])

    def _exhausted(
        self,
        virtual_model: str,
        errors: list[dict],
        start_time: float,
        attempts: int,
    ) -> Exception:
        total_latency = (time.time() - start_time) * 1000
        logger.error(
            "failover.exhausted",
            model=virtual_model,
            attempts=attempts,
            total_latency_ms=round(total_latency),
            errors=errors,
        )
        if errors and all(e.get("rate_limited") or e.get("capacity") for e in errors):
            kind: Literal["capacity", "rate_limit"] = (
                "rate_limit"
                if any(e.get("rate_limited") for e in errors)
                else "capacity"
            )
            return NoProviderAvailableError(
                virtual_model,
                errors,
                kind=kind,
                retry_after=_max_retry_after(errors),
            )
        return AllProvidersFailedError(virtual_model, errors)

    async def _get_providers(self, virtual_model: str) -> list[ProviderConfig]:
        vm = self._virtual_model(virtual_model)
        mode = self.config.router.mode

        if mode == "sticky":
            if vm.pinned_model is None:
                raise AllProvidersFailedError(
                    virtual_model,
                    [
                        {
                            "provider": "(none)",
                            "model": "(none)",
                            "error": "sticky 模式未配置 pinned_model",
                            "retryable": False,
                        }
                    ],
                )
            candidates = [
                p
                for p in vm.providers
                if p.name == vm.pinned_model.provider
                and p.model == vm.pinned_model.model
            ]
            if not candidates:
                raise AllProvidersFailedError(
                    virtual_model,
                    [
                        {
                            "provider": vm.pinned_model.provider,
                            "model": vm.pinned_model.model,
                            "error": (
                                "sticky 指定的 provider:model 不在该虚拟模型链中"
                            ),
                            "retryable": False,
                        }
                    ],
                )
        else:
            candidates = list(vm.providers)

        available: list[ProviderConfig] = []
        skipped: list[dict] = []

        for p in candidates:
            if has_unresolved_env_var(p.api_key):
                skipped.append(
                    {
                        "provider": p.name,
                        "model": p.model,
                        "reason": "unresolved_api_key",
                        "error": (f"api_key 环境变量未设置或未正确插值: {p.api_key}"),
                        "retryable": False,
                    }
                )
                continue

            if mode == "failover":
                circuit_state = await self.circuit_breaker.state(
                    p.name,
                    recovery_timeout=p.recovery_timeout,
                )
                if circuit_state == CircuitState.OPEN:
                    skipped.append(
                        {
                            "provider": p.name,
                            "model": p.model,
                            "state": circuit_state.value,
                            "retryable": True,
                            "reason": "circuit_open",
                        }
                    )
                    continue

            remaining = self.provider_gate.cooldown_remaining(p.name)
            if remaining > 0:
                # sticky: 仍返回该 provider，由 slot() 抛出冷却错误 → 429
                # failover: 跳过，尝试下一个
                if mode == "failover":
                    skipped.append(
                        {
                            "provider": p.name,
                            "model": p.model,
                            "reason": "cooldown",
                            "error": (
                                f"provider 处于限流冷却 (remaining={remaining:.1f}s)"
                            ),
                            "retryable": True,
                            "rate_limited": True,
                            "retry_after": remaining,
                        }
                    )
                    continue

            available.append(p)

        if skipped:
            logger.info(
                "providers.skipped",
                model=virtual_model,
                mode=mode,
                skipped=skipped,
                available_count=len(available),
            )

        if not available and skipped:
            if all(
                s.get("reason") == "cooldown" or s.get("rate_limited") for s in skipped
            ):
                raise NoProviderAvailableError(
                    virtual_model,
                    [
                        {
                            "provider": s["provider"],
                            "model": s["model"],
                            "error": s.get("error", "cooldown"),
                            "retryable": True,
                            "rate_limited": True,
                            "retry_after": s.get("retry_after"),
                        }
                        for s in skipped
                    ],
                    kind="rate_limit",
                    retry_after=_max_retry_after(skipped),
                )
            raise AllProvidersFailedError(
                virtual_model,
                [
                    {
                        "provider": s["provider"],
                        "model": s["model"],
                        "error": s["error"]
                        if "error" in s
                        else f"provider 已熔断 (state={s.get('state')})",
                        "retryable": s.get("retryable", True),
                    }
                    for s in skipped
                ],
            )

        return available

    @property
    def model_names(self) -> list[str]:
        return list(self.config.models.keys())


class StickyRateLimited(Exception):
    """Internal: sticky mode hit rate limit; convert to NoProviderAvailableError."""

    def __init__(self, retry_after: float | None) -> None:
        self.retry_after = retry_after


class UnknownModelError(Exception):
    def __init__(self, model: str, known: list[str]) -> None:
        self.model = model
        self.known = known
        super().__init__(f"未知模型 '{model}'，已知模型: {', '.join(known)}")


class AllProvidersFailedError(Exception):
    def __init__(self, model: str, errors: list[dict]) -> None:
        self.model = model
        self.errors = errors
        summary = "; ".join(
            f"[{e['provider']}:{e['model']}] {e['error']}" for e in errors
        )
        super().__init__(f"模型 '{model}' 所有 provider 均失败: {summary}")


class NoProviderAvailableError(Exception):
    """本地容量耗尽或 Provider 限流导致无可用 provider."""

    def __init__(
        self,
        model: str,
        errors: list[dict],
        *,
        kind: Literal["capacity", "rate_limit"],
        retry_after: float | None = None,
    ) -> None:
        self.model = model
        self.errors = errors
        self.kind = kind
        self.retry_after = retry_after
        label = "本地容量不足" if kind == "capacity" else "Provider 限流"
        summary = "; ".join(
            f"[{e['provider']}:{e['model']}] {e['error']}" for e in errors
        )
        super().__init__(f"模型 '{model}' {label}: {summary}")

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from routelet.config import (
    AppConfig,
    ModelRef,
    ProviderConfig,
    RouterConfig,
    ServerConfig,
    VirtualModelConfig,
)
from routelet.circuit_breaker import CircuitState
from routelet.routing import (
    Router,
    UnknownModelError,
    AllProvidersFailedError,
    NoProviderAvailableError,
)
from routelet.providers.base import (
    NonRetryableError,
    RetryableError,
    UpstreamHTTPError,
)


def _reload_race_config(
    *,
    base_url: str,
    api_key: str,
    model: str,
    max_concurrent: int = 0,
    max_queue: int = 0,
) -> AppConfig:
    """Build a one-provider config for hot-reload routing race tests."""
    return AppConfig(
        server=ServerConfig(),
        router=RouterConfig(mode="failover"),
        models={
            "m": VirtualModelConfig(
                providers=[
                    ProviderConfig(
                        type="anthropic",
                        name="p1",
                        model=model,
                        api_key=api_key,
                        base_url=base_url,
                        priority=1,
                        max_concurrent=max_concurrent,
                        max_queue=max_queue,
                        queue_wait_timeout=1.0,
                    )
                ]
            )
        },
    )


class TestRouterModelLookup:
    async def test_known_model(self, sample_config, http_client):
        router = Router(sample_config, http_client)
        providers = await router._get_providers("haiku-router")
        assert len(providers) == 2
        assert providers[0].model == "claude-haiku-4-5-20251001"

    async def test_unknown_model(self, sample_config, http_client):
        router = Router(sample_config, http_client)
        with pytest.raises(UnknownModelError) as exc:
            await router._get_providers("nonexistent-router")
        assert "nonexistent-router" in str(exc.value)
        assert "haiku-router" in exc.value.known

    async def test_model_names(self, sample_config, http_client):
        router = Router(sample_config, http_client)
        names = router.model_names
        assert "haiku-router" in names
        assert "sonnet-router" in names

    async def test_unresolved_api_key_provider_is_skipped(self, http_client):
        config = AppConfig(
            server=ServerConfig(),
            router=RouterConfig(mode="failover"),
            models={
                "m": VirtualModelConfig(
                    providers=[
                        ProviderConfig(
                            type="anthropic",
                            name="missing",
                            model="m1",
                            api_key="${MISSING_KEY}",
                            base_url="https://missing.test",
                            priority=1,
                        ),
                        ProviderConfig(
                            type="anthropic",
                            name="ready",
                            model="m2",
                            api_key="sk-ready",
                            base_url="https://ready.test",
                            priority=2,
                        ),
                    ]
                )
            },
        )
        router = Router(config, http_client)

        providers = await router._get_providers("m")

        assert [p.name for p in providers] == ["ready"]

    async def test_all_unresolved_api_keys_fail_with_clear_error(self, http_client):
        config = AppConfig(
            server=ServerConfig(),
            router=RouterConfig(mode="failover"),
            models={
                "m": VirtualModelConfig(
                    providers=[
                        ProviderConfig(
                            type="anthropic",
                            name="missing",
                            model="m1",
                            api_key="${MISSING_KEY}",
                            base_url="https://missing.test",
                            priority=1,
                        ),
                    ]
                )
            },
        )
        router = Router(config, http_client)

        with pytest.raises(AllProvidersFailedError) as exc:
            await router._get_providers("m")

        assert "api_key 环境变量未设置或未正确插值" in str(exc.value)


class TestRouterHotReload:
    @pytest.mark.parametrize("stream", [False, True])
    async def test_reload_before_attempt_uses_current_provider_config(
        self, monkeypatch, stream
    ):
        """Do not send a request with a Provider snapshot replaced before I/O."""
        requests: list[tuple[str, str | None, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            requests.append(
                (
                    str(request.url),
                    request.headers.get("authorization"),
                    body["model"],
                )
            )
            if body.get("stream"):
                return httpx.Response(
                    200,
                    content=(
                        b'event: message_start\ndata: {"type":"message_start"}\n\n'
                    ),
                )
            return httpx.Response(200, json={"model": body["model"]})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            router = Router(
                _reload_race_config(
                    base_url="https://old.test", api_key="old-key", model="old-model"
                ),
                client,
            )
            attempt_ready = asyncio.Event()
            continue_attempt = asyncio.Event()
            original_try_acquire = router.circuit_breaker.try_acquire

            async def pause_first_attempt(*args, **kwargs):
                if not attempt_ready.is_set():
                    attempt_ready.set()
                    await continue_attempt.wait()
                return await original_try_acquire(*args, **kwargs)

            monkeypatch.setattr(
                router.circuit_breaker, "try_acquire", pause_first_attempt
            )

            async def route():
                body = {"model": "m", "messages": [], "stream": stream}
                if not stream:
                    return await router.route_non_stream(body)
                return b"".join([chunk async for chunk in router.route_stream(body)])

            task = asyncio.create_task(route())
            await asyncio.wait_for(attempt_ready.wait(), timeout=1.0)
            await router.reload_config(
                _reload_race_config(
                    base_url="https://new.test", api_key="new-key", model="new-model"
                )
            )
            continue_attempt.set()
            await asyncio.wait_for(task, timeout=1.0)

        assert requests == [
            ("https://new.test/v1/messages", "Bearer new-key", "new-model")
        ]

    async def test_reload_transparently_reroutes_queued_attempt(self):
        """Treat a stale queue wakeup as reconfiguration, not capacity loss."""
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(200, json={"ok": True})

        old_config = _reload_race_config(
            base_url="https://old.test",
            api_key="old-key",
            model="old-model",
            max_concurrent=1,
            max_queue=1,
        )
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            router = Router(old_config, client)
            holder_entered = asyncio.Event()
            release_holder = asyncio.Event()
            old_provider = old_config.models["m"].providers[0]

            async def hold_slot():
                async with router.provider_gate.slot(old_provider):
                    holder_entered.set()
                    await release_holder.wait()

            holder = asyncio.create_task(hold_slot())
            await holder_entered.wait()
            route = asyncio.create_task(
                router.route_non_stream({"model": "m", "messages": []})
            )
            async with asyncio.timeout(1.0):
                while router.provider_gate.snapshot()["p1"]["waiting"] != 1:
                    await asyncio.sleep(0)

            await router.reload_config(
                _reload_race_config(
                    base_url="https://new.test",
                    api_key="new-key",
                    model="new-model",
                    max_concurrent=1,
                    max_queue=1,
                )
            )
            release_holder.set()
            await asyncio.wait_for(holder, timeout=1.0)
            assert await asyncio.wait_for(route, timeout=1.0) == {"ok": True}

        assert requests == ["https://new.test/v1/messages"]

    @pytest.mark.parametrize("stream", [False, True])
    async def test_reset_does_not_restore_cooldown_from_in_flight_429(self, stream):
        upstream_entered = asyncio.Event()
        release_upstream = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            upstream_entered.set()
            await release_upstream.wait()
            return httpx.Response(429, text="limited", headers={"Retry-After": "60"})

        config = _reload_race_config(
            base_url="https://p1.test", api_key="key", model="model"
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            router = Router(config, client)

            async def route() -> None:
                body = {"model": "m", "max_tokens": 10, "messages": []}
                if stream:
                    async for _ in router.route_stream(body):
                        pass
                else:
                    await router.route_non_stream(body)

            task = asyncio.create_task(route())
            await asyncio.wait_for(upstream_entered.wait(), timeout=1.0)
            router.provider_gate.clear_cooldown("p1")
            release_upstream.set()
            with pytest.raises(NoProviderAvailableError):
                await asyncio.wait_for(task, timeout=1.0)
            assert not router.provider_gate.is_in_cooldown("p1")


class TestAllProvidersFailedError:
    def test_formatting(self):
        errors = [
            {"provider": "anthropic", "model": "m1", "error": "HTTP 429"},
            {"provider": "anthropic", "model": "m2", "error": "timeout"},
        ]
        exc = AllProvidersFailedError("test-model", errors)
        msg = str(exc)
        assert "test-model" in msg
        assert "HTTP 429" in msg
        assert "timeout" in msg


def _sse_error_buffer(error_type: str, message: str) -> bytes:
    """Helper to build an SSE error event buffer."""
    data = json.dumps(
        {"type": "error", "error": {"type": error_type, "message": message}}
    )
    return f"event: error\ndata: {data}\n\n".encode()


@pytest.mark.parametrize(
    ("error_type", "message", "rate_limited", "immediate_break"),
    [
        ("authentication_error", "Invalid API key", False, True),
        ("permission_error", "Access denied", False, True),
        ("rate_limit_error", "Too many requests", True, False),
        ("overloaded_error", "Server busy", True, False),
    ],
)
async def test_stream_error_classification_updates_provider_state(
    error_type, message, rate_limited, immediate_break
):
    """Route a real stream error through classification and provider accounting."""
    response = _sse_error_buffer(error_type, message)
    config = _reload_race_config(
        base_url="https://p1.test", api_key="key", model="model"
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=response)
        )
    ) as client:
        router = Router(config, client)
        expected_error = (
            NoProviderAvailableError if rate_limited else AllProvidersFailedError
        )
        with pytest.raises(expected_error) as exc_info:
            async for _ in router.route_stream({"model": "m", "messages": []}):
                pytest.fail("An upstream error event reached the client")

        assert error_type in exc_info.value.errors[0]["error"]
        assert exc_info.value.errors[0]["rate_limited"] is rate_limited
        assert router.provider_gate.is_in_cooldown("p1") is rate_limited
        assert await router.circuit_breaker.state("p1") is (
            CircuitState.OPEN if immediate_break else CircuitState.CLOSED
        )


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_sse_error_buffer("some_new_error", "Something weird"), "some_new_error"),
        (b"event: error\ndata: {broken json}\n\n", "Stream error"),
        (b'event: error\ndata: {"type":', "Invalid SSE stream"),
    ],
)
async def test_invalid_stream_error_rejected_by_routing(response, message):
    config = _reload_race_config(
        base_url="https://p1.test", api_key="key", model="model"
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=response)
        )
    ) as client:
        router = Router(config, client)
        with pytest.raises(NonRetryableError, match=message):
            async for _ in router.route_stream({"model": "m", "messages": []}):
                pytest.fail("An invalid upstream error event reached the client")


class TestRateLimitRouting:
    async def test_rate_limit_does_not_trip_circuit(self, http_client):
        """429 进入短冷却，不计入熔断连续失败."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(
                    429, text="rate limited", headers={"Retry-After": "60"}
                )
            return httpx.Response(
                200,
                json={
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "ok"}],
                    "model": "m2",
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            config = AppConfig(
                server=ServerConfig(),
                router=RouterConfig(mode="failover"),
                models={
                    "m": VirtualModelConfig(
                        providers=[
                            ProviderConfig(
                                type="anthropic",
                                name="p1",
                                model="m1",
                                api_key="k1",
                                base_url="https://p1.test",
                                priority=1,
                                failure_threshold=1,
                            ),
                            ProviderConfig(
                                type="anthropic",
                                name="p2",
                                model="m2",
                                api_key="k2",
                                base_url="https://p2.test",
                                priority=2,
                                input_price_per_million=1.0,
                                output_price_per_million=4.0,
                            ),
                        ]
                    )
                },
            )
            router = Router(config, client)
            outcome: dict = {}
            result = await router.route_non_stream(
                {"model": "m", "max_tokens": 10, "messages": []}, outcome
            )
            assert result["model"] == "m2"
            assert outcome["pricing"] == {
                "input": 1.0,
                "output": 4.0,
                "cache_read": None,
                "cache_write": None,
            }
            # p1 未熔断
            from routelet.circuit_breaker import CircuitState

            assert (await router.circuit_breaker.state("p1")) == CircuitState.CLOSED
            assert router.provider_gate.is_in_cooldown("p1")

    @pytest.mark.parametrize("stream", [False, True])
    @pytest.mark.parametrize("status_code", [500, 401])
    async def test_sticky_does_not_failover(self, stream, status_code):
        attempted_hosts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempted_hosts.append(request.url.host)
            return httpx.Response(status_code, text="boom")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            config = AppConfig(
                server=ServerConfig(),
                router=RouterConfig(mode="sticky"),
                models={
                    "m": VirtualModelConfig(
                        pinned_model=ModelRef(provider="p2", model="m2"),
                        providers=[
                            ProviderConfig(
                                type="anthropic",
                                name="p1",
                                model="m1",
                                api_key="k1",
                                base_url="https://p1.test",
                                priority=1,
                            ),
                            ProviderConfig(
                                type="anthropic",
                                name="p2",
                                model="m2",
                                api_key="k2",
                                base_url="https://p2.test",
                                priority=2,
                            ),
                        ],
                    )
                },
            )
            router = Router(config, client)
            outcome: dict = {}
            body = {"model": "m", "max_tokens": 10, "messages": []}
            with pytest.raises(UpstreamHTTPError) as exc:
                if stream:
                    async for _ in router.route_stream(body, outcome):
                        pass
                else:
                    await router.route_non_stream(body, outcome)
            assert exc.value.response.status_code == status_code
            assert exc.value.response.body == b"boom"
            assert outcome["_failures"][0]["provider"] == "p2"
            assert attempted_hosts == ["p2.test"]
            assert outcome["attempt"] == 1

    @pytest.mark.parametrize("stream", [False, True])
    @pytest.mark.parametrize("status_code", [401, 500])
    async def test_sticky_errors_do_not_open_circuit(self, stream, status_code):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(status_code, text="upstream error")

        config = _reload_race_config(
            base_url="https://p1.test", api_key="key", model="model"
        )
        config.router = RouterConfig(mode="sticky", failure_threshold=1)
        config.models["m"].pinned_model = ModelRef(provider="p1", model="model")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            router = Router(config, client)
            body = {"model": "m", "max_tokens": 10, "messages": []}
            for _ in range(2):
                with pytest.raises(UpstreamHTTPError):
                    if stream:
                        async for _ in router.route_stream(body):
                            pass
                    else:
                        await router.route_non_stream(body)
                assert await router.circuit_breaker.state("p1") == CircuitState.CLOSED
        assert calls == 2

    @pytest.mark.parametrize("stream", [False, True])
    async def test_disabling_failover_clears_circuit_and_tries_pinned_model(
        self, stream
    ):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if stream:
                return httpx.Response(
                    200,
                    content=b'event: message_start\ndata: {"type":"message_start"}\n\n',
                )
            return httpx.Response(200, json={"ok": True})

        config = _reload_race_config(
            base_url="https://p1.test", api_key="key", model="model"
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            router = Router(config, client)
            await router.circuit_breaker.record_failure("p1", immediate=True)
            assert await router.circuit_breaker.state("p1") == CircuitState.OPEN
            sticky_config = config.model_copy(deep=True)
            sticky_config.router.mode = "sticky"
            sticky_config.models["m"].pinned_model = ModelRef(
                provider="p1", model="model"
            )
            await router.reload_config(sticky_config)
            assert await router.circuit_breaker.state("p1") == CircuitState.CLOSED
            body = {"model": "m", "max_tokens": 10, "messages": []}
            if stream:
                assert b"message_start" in b"".join(
                    [chunk async for chunk in router.route_stream(body)]
                )
            else:
                assert await router.route_non_stream(body) == {"ok": True}
            assert calls == 1

    async def test_sticky_rate_limit_preserves_response(self, http_client):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="rl", headers={"Retry-After": "9"})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            config = AppConfig(
                server=ServerConfig(),
                router=RouterConfig(mode="sticky"),
                models={
                    "m": VirtualModelConfig(
                        pinned_model=ModelRef(provider="p1", model="m1"),
                        providers=[
                            ProviderConfig(
                                type="anthropic",
                                name="p1",
                                model="m1",
                                api_key="k1",
                                base_url="https://p1.test",
                                priority=1,
                            ),
                        ],
                    )
                },
            )
            router = Router(config, client)
            with pytest.raises(UpstreamHTTPError) as exc:
                await router.route_non_stream(
                    {"model": "m", "max_tokens": 10, "messages": []}
                )
            assert exc.value.response.status_code == 429
            assert exc.value.response.body == b"rl"
            assert router.provider_gate.cooldown_remaining("p1") == pytest.approx(
                9.0, abs=0.05
            )

    async def test_sticky_missing_pin_raises_clear_error(self, http_client):
        config = AppConfig(
            server=ServerConfig(),
            router=RouterConfig(mode="sticky"),
            models={
                "m": VirtualModelConfig(
                    providers=[
                        ProviderConfig(
                            type="anthropic",
                            name="p1",
                            model="m1",
                            api_key="k1",
                            base_url="https://p1.test",
                            priority=1,
                        ),
                    ]
                )
            },
        )
        # 绕过 load_config 校验，模拟运行时 pin 丢失
        config.models["m"].pinned_model = None
        router = Router(config, http_client)
        with pytest.raises(AllProvidersFailedError) as exc:
            await router._get_providers("m")
        assert "pinned_model" in str(exc.value)

    async def test_stream_does_not_failover_after_yield(self, http_client):
        """已向客户端发送字节后，流内错误不再切换 provider."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if "p1" in str(request.url):
                # 分 chunk 返回，确保 message_start 先 yield 后再出现流内错误
                async def body():
                    yield (
                        b"event: message_start\ndata: "
                        b'{"type":"message_start","message":{}}\n\n'
                    )
                    yield (
                        b'event: error\ndata: {"type":"error","error":'
                        b'{"type":"api_error","message":"mid"}}\n\n'
                    )

                return httpx.Response(200, content=body())
            return httpx.Response(
                200,
                content=b'event: message_start\ndata: {"type":"message_start"}\n\n',
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            config = AppConfig(
                server=ServerConfig(),
                router=RouterConfig(mode="failover"),
                models={
                    "m": VirtualModelConfig(
                        providers=[
                            ProviderConfig(
                                type="anthropic",
                                name="p1",
                                model="m1",
                                api_key="k1",
                                base_url="https://p1.test",
                                priority=1,
                            ),
                            ProviderConfig(
                                type="anthropic",
                                name="p2",
                                model="m2",
                                api_key="k2",
                                base_url="https://p2.test",
                                priority=2,
                            ),
                        ]
                    )
                },
            )
            router = Router(config, client)
            chunks: list[bytes] = []
            with pytest.raises(RetryableError, match="api_error"):
                async for chunk in router.route_stream(
                    {"model": "m", "max_tokens": 10, "messages": [], "stream": True}
                ):
                    chunks.append(chunk)
            assert chunks  # 已 yield 过
            assert calls["n"] == 1  # 未打到 p2

    async def test_stream_error_detected_before_buffer_trim(self, http_client):
        """大 chunk 中靠前的 event:error 在 trim 前仍应被检测到."""
        calls = {"n": 0}
        padding = b"x" * 9000
        error_event = (
            b'event: error\ndata: {"type":"error","error":'
            b'{"type":"api_error","message":"early"}}\n\n'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if "p1" in str(request.url):
                return httpx.Response(200, content=error_event + padding)
            return httpx.Response(
                200,
                content=b'event: message_start\ndata: {"type":"message_start"}\n\n',
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            config = AppConfig(
                server=ServerConfig(),
                router=RouterConfig(mode="failover"),
                models={
                    "m": VirtualModelConfig(
                        providers=[
                            ProviderConfig(
                                type="anthropic",
                                name="p1",
                                model="m1",
                                api_key="k1",
                                base_url="https://p1.test",
                                priority=1,
                            ),
                            ProviderConfig(
                                type="anthropic",
                                name="p2",
                                model="m2",
                                api_key="k2",
                                base_url="https://p2.test",
                                priority=2,
                            ),
                        ]
                    )
                },
            )
            router = Router(config, client)
            chunks: list[bytes] = []
            async for chunk in router.route_stream(
                {"model": "m", "max_tokens": 10, "messages": [], "stream": True}
            ):
                chunks.append(chunk)
            assert calls["n"] == 2
            assert b"message_start" in b"".join(chunks)

    async def test_long_stream_error_split_across_chunks_is_not_lost(self, http_client):
        """An event header must survive while a long split payload is incomplete."""
        calls = {"n": 0}
        error_prefix = (
            b'event: error\ndata: {"type":"error","error":'
            b'{"type":"api_error","message":"' + b"x" * 9000
        )

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if "p1" in str(request.url):

                async def body():
                    yield error_prefix
                    yield b'"}}\n\n'

                return httpx.Response(200, content=body())
            return httpx.Response(
                200,
                content=b'event: message_start\ndata: {"type":"message_start"}\n\n',
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            config = AppConfig(
                server=ServerConfig(),
                router=RouterConfig(mode="failover"),
                models={
                    "m": VirtualModelConfig(
                        providers=[
                            ProviderConfig(
                                type="anthropic",
                                name="p1",
                                model="m1",
                                api_key="k1",
                                base_url="https://p1.test",
                                priority=1,
                            ),
                            ProviderConfig(
                                type="anthropic",
                                name="p2",
                                model="m2",
                                api_key="k2",
                                base_url="https://p2.test",
                                priority=2,
                            ),
                        ]
                    )
                },
            )
            router = Router(config, client)
            chunks: list[bytes] = []
            async for chunk in router.route_stream(
                {"model": "m", "max_tokens": 10, "messages": [], "stream": True}
            ):
                chunks.append(chunk)

        assert calls["n"] == 2
        assert b"message_start" in b"".join(chunks)
        assert b"event: error" not in b"".join(chunks)

    async def test_stream_error_before_yield_allows_failover(self, http_client):
        """首包即为 event:error 时不应先发给客户端，应可 failover."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if "p1" in str(request.url):
                body = (
                    b'event: error\ndata: {"type":"error","error":'
                    b'{"type":"api_error","message":"first"}}\n\n'
                )
                return httpx.Response(200, content=body)
            return httpx.Response(
                200,
                content=b'event: message_start\ndata: {"type":"message_start"}\n\n',
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            config = AppConfig(
                server=ServerConfig(),
                router=RouterConfig(mode="failover"),
                models={
                    "m": VirtualModelConfig(
                        providers=[
                            ProviderConfig(
                                type="anthropic",
                                name="p1",
                                model="m1",
                                api_key="k1",
                                base_url="https://p1.test",
                                priority=1,
                            ),
                            ProviderConfig(
                                type="anthropic",
                                name="p2",
                                model="m2",
                                api_key="k2",
                                base_url="https://p2.test",
                                priority=2,
                            ),
                        ]
                    )
                },
            )
            router = Router(config, client)
            chunks: list[bytes] = []
            async for chunk in router.route_stream(
                {"model": "m", "max_tokens": 10, "messages": [], "stream": True}
            ):
                chunks.append(chunk)
            assert calls["n"] == 2
            assert chunks
            assert b"message_start" in chunks[0]
            assert b"event: error" not in b"".join(chunks)


@pytest.mark.parametrize("split", [1, 12, 40, -1])
@pytest.mark.parametrize("started", [False, True])
async def test_split_stream_error_never_leaks_partial_event(
    sample_config, split, started
):
    """Retry initial errors, but preserve the chosen stream after useful output."""
    first_event = b'event: message_start\ndata: {"type":"message_start"}\n\n'
    error_event = (
        b'event: error\ndata: {"type":"error","error":'
        b'{"type":"api_error","message":"busy"}}\n\n'
    )
    calls: list[str] = []

    async def body():
        yield b": keepalive\n\n"
        if started:
            yield first_event
        yield error_event[:split]
        yield error_event[split:]

    def handler(request):
        calls.append(request.url.host)
        if len(calls) == 1:
            return httpx.Response(200, content=body())
        return httpx.Response(200, content=first_event)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        router = Router(sample_config, client)
        chunks: list[bytes] = []

        async def consume():
            async for chunk in router.route_stream({"model": "haiku-router"}):
                chunks.append(chunk)

        if started:
            with pytest.raises(RetryableError, match="busy"):
                await consume()
        else:
            await consume()

    assert len(calls) == (1 if started else 2)
    assert b"".join(chunks) == first_event


async def test_combined_stream_events_commit_before_later_error(sample_config):
    """Respect event order when a data event and an error share a network chunk."""
    first_event = b"event: message_start\ndata: {}\n\n"
    error_event = b'event: error\ndata: {"error":{"type":"api_error"}}\n\n'
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=first_event + error_event)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        router = Router(sample_config, client)
        chunks: list[bytes] = []
        with pytest.raises(RetryableError):
            async for chunk in router.route_stream({"model": "haiku-router"}):
                chunks.append(chunk)

    assert calls == 1
    assert chunks == [first_event]


async def test_bom_prefixed_stream_error_allows_failover(sample_config):
    """Recognize an initial error even with a split UTF-8 stream signature."""
    failed = b'\xef\xbb\xbfevent: error\ndata: {"error":{"type":"api_error"}}\n\n'
    success = b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    calls = 0

    async def failed_body():
        for byte in failed:
            yield bytes((byte,))

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=failed_body() if calls == 1 else success)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        router = Router(sample_config, client)
        outcome = {}
        chunks = [
            chunk
            async for chunk in router.route_stream({"model": "haiku-router"}, outcome)
        ]

    assert calls == outcome["attempt"] == 2
    assert chunks == [success]

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agent_router.circuit_breaker import CircuitBreaker, CircuitState
from agent_router.config import (
    AppConfig,
    ProviderConfig,
    RouterConfig,
    ServerConfig,
    VirtualModelConfig,
)
from agent_router.providers.base import NonRetryableError
from agent_router.routing import (
    AllProvidersFailedError,
    NoProviderAvailableError,
    Router,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _single_provider_config() -> AppConfig:
    return AppConfig(
        server=ServerConfig(),
        router=RouterConfig(mode="failover", failure_threshold=1),
        models={
            "m": VirtualModelConfig(
                providers=[
                    ProviderConfig(
                        type="anthropic",
                        name="p1",
                        model="upstream-model",
                        api_key="test-key",
                        base_url="https://p1.test",
                        priority=1,
                        failure_threshold=1,
                    )
                ]
            )
        },
    )


def _success_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "ok"}],
            "model": "upstream-model",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    )


class TestCircuitBreakerUnit:
    async def test_initial_state_is_closed(self):
        cb = CircuitBreaker()
        assert await cb.state("p1") == CircuitState.CLOSED
        assert await cb.is_available("p1")

    async def test_consecutive_failures_open_circuit(self):
        cb = CircuitBreaker(failure_threshold=3)
        await cb.record_failure("p1")
        assert await cb.state("p1") == CircuitState.CLOSED
        await cb.record_failure("p1")
        assert await cb.state("p1") == CircuitState.CLOSED
        await cb.record_failure("p1")
        assert await cb.state("p1") == CircuitState.OPEN
        assert not await cb.is_available("p1")

    async def test_immediate_failure_opens_circuit(self):
        cb = CircuitBreaker()
        await cb.record_failure("p1", immediate=True)
        assert await cb.state("p1") == CircuitState.OPEN
        assert not await cb.is_available("p1")

    async def test_success_resets_circuit(self):
        cb = CircuitBreaker(failure_threshold=2)
        await cb.record_failure("p1")
        await cb.record_failure("p1")
        assert await cb.state("p1") == CircuitState.OPEN
        await cb.record_success("p1")
        assert await cb.state("p1") == CircuitState.CLOSED
        assert await cb.is_available("p1")

    async def test_success_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3)
        await cb.record_failure("p1")
        await cb.record_failure("p1")
        await cb.record_success("p1")
        await cb.record_failure("p1")
        await cb.record_failure("p1")
        assert await cb.state("p1") == CircuitState.CLOSED

    async def test_recovery_timeout_transitions_to_half_open(self):
        clock = _Clock()
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout=5.0,
            clock=clock,
        )
        await cb.record_failure("p1")
        assert await cb.state("p1") == CircuitState.OPEN
        clock.advance(5.0)
        assert await cb.state("p1") == CircuitState.HALF_OPEN
        assert await cb.is_available("p1")

    async def test_half_open_success_closes_circuit(self):
        clock = _Clock()
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout=5.0,
            clock=clock,
        )
        await cb.record_failure("p1")
        clock.advance(5.0)
        permit = await cb.try_acquire("p1")
        assert permit is not None and permit.probe
        await cb.record_success("p1", permit=permit)
        assert await cb.state("p1") == CircuitState.CLOSED

    async def test_half_open_failure_reopens_circuit(self):
        clock = _Clock()
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout=5.0,
            clock=clock,
        )
        await cb.record_failure("p1")
        clock.advance(5.0)
        permit = await cb.try_acquire("p1")
        assert permit is not None and permit.probe
        await cb.record_failure("p1", permit=permit)
        assert await cb.state("p1") == CircuitState.OPEN

    async def test_providers_are_independent(self):
        cb = CircuitBreaker(failure_threshold=1)
        await cb.record_failure("p1")
        assert await cb.state("p1") == CircuitState.OPEN
        assert await cb.state("p2") == CircuitState.CLOSED
        assert not await cb.is_available("p1")
        assert await cb.is_available("p2")

    async def test_reset_clears_state(self):
        cb = CircuitBreaker(failure_threshold=1)
        await cb.record_failure("p1")
        assert await cb.state("p1") == CircuitState.OPEN
        await cb.reset("p1")
        assert await cb.state("p1") == CircuitState.CLOSED
        assert await cb.is_available("p1")

    async def test_per_provider_failure_threshold(self):
        """Per-provider threshold overrides global default."""
        cb = CircuitBreaker(failure_threshold=5)
        # p1 uses per-provider threshold of 2
        await cb.record_failure("p1", failure_threshold=2)
        assert await cb.state("p1") == CircuitState.CLOSED
        await cb.record_failure("p1", failure_threshold=2)
        assert await cb.state("p1") == CircuitState.OPEN
        # p2 still uses global threshold of 5
        await cb.record_failure("p2")
        await cb.record_failure("p2")
        assert await cb.state("p2") == CircuitState.CLOSED

    async def test_per_provider_recovery_timeout(self):
        """Per-provider recovery_timeout overrides global default."""
        clock = _Clock()
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout=10.0,
            clock=clock,
        )
        await cb.record_failure("p1")
        assert await cb.state("p1") == CircuitState.OPEN
        # Global timeout (10s) hasn't passed
        assert await cb.state("p1") == CircuitState.OPEN
        # Per-provider timeout (5s) should allow transition
        clock.advance(5.0)
        assert await cb.state("p1", recovery_timeout=5.0) == CircuitState.HALF_OPEN

    async def test_per_provider_recovery_timeout_shorter_than_global(self):
        """Shorter per-provider timeout triggers transition before global."""
        clock = _Clock()
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout=10.0,
            clock=clock,
        )
        await cb.record_failure("p1")
        await cb.record_failure("p2")
        clock.advance(5.0)
        # p1 uses short per-provider timeout → HALF_OPEN
        assert await cb.state("p1", recovery_timeout=5.0) == CircuitState.HALF_OPEN
        # p2 uses global timeout (10s) → still OPEN
        assert await cb.state("p2") == CircuitState.OPEN

    async def test_per_provider_is_available(self):
        """is_available respects per-provider recovery_timeout."""
        clock = _Clock()
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout=10.0,
            clock=clock,
        )
        await cb.record_failure("p1")
        assert not await cb.is_available("p1")
        clock.advance(5.0)
        assert not await cb.is_available("p1")  # global timeout
        assert await cb.is_available("p1", recovery_timeout=5.0)  # per-provider timeout

    async def test_half_open_issues_one_probe_and_release_allows_replacement(self):
        clock = _Clock()
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout=5.0,
            clock=clock,
        )
        await cb.record_failure("p1", immediate=True)
        clock.advance(5.0)

        permits = await asyncio.gather(*(cb.try_acquire("p1") for _ in range(16)))
        probes = [permit for permit in permits if permit is not None]

        assert len(probes) == 1
        assert probes[0].probe
        assert await cb.state("p1") == CircuitState.HALF_OPEN

        await cb.release(probes[0])
        replacement = await cb.try_acquire("p1")
        assert replacement is not None and replacement.probe
        await cb.release(replacement)

    async def test_state_observation_does_not_consume_probe(self):
        clock = _Clock()
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout=5.0,
            clock=clock,
        )
        await cb.record_failure("p1", immediate=True)
        clock.advance(5.0)

        assert await cb.state("p1") == CircuitState.HALF_OPEN
        assert await cb.is_available("p1")
        assert await cb.state("p1") == CircuitState.HALF_OPEN

        permit = await cb.try_acquire("p1")
        assert permit is not None and permit.probe
        await cb.release(permit)

    async def test_released_probe_cannot_complete_its_replacement(self):
        clock = _Clock()
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout=5.0,
            clock=clock,
        )
        await cb.record_failure("p1", immediate=True)
        clock.advance(5.0)
        released = await cb.try_acquire("p1")
        assert released is not None
        await cb.release(released)
        replacement = await cb.try_acquire("p1")
        assert replacement is not None

        await cb.record_success("p1", permit=released)
        assert await cb.state("p1") == CircuitState.HALF_OPEN

        await cb.record_success("p1", permit=replacement)
        assert await cb.state("p1") == CircuitState.CLOSED

    async def test_open_generation_ignores_older_in_flight_success(self):
        cb = CircuitBreaker(failure_threshold=1)
        old = await cb.try_acquire("p1")
        trigger = await cb.try_acquire("p1")
        assert old is not None
        assert trigger is not None

        await cb.record_failure("p1", immediate=True, permit=trigger)
        await cb.record_success("p1", permit=old)

        assert await cb.state("p1") == CircuitState.OPEN

    async def test_new_probe_success_ignores_failure_from_older_generation(self):
        clock = _Clock()
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout=5.0,
            clock=clock,
        )
        old = await cb.try_acquire("p1")
        trigger = await cb.try_acquire("p1")
        assert old is not None
        assert trigger is not None
        await cb.record_failure("p1", immediate=True, permit=trigger)

        clock.advance(5.0)
        probe = await cb.try_acquire("p1")
        assert probe is not None and probe.probe
        await cb.record_success("p1", permit=probe)
        await cb.record_failure("p1", immediate=True, permit=old)

        assert await cb.state("p1") == CircuitState.CLOSED

    async def test_probe_result_is_consumed_once(self):
        clock = _Clock()
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout=5.0,
            clock=clock,
        )
        await cb.record_failure("p1", immediate=True)
        clock.advance(5.0)
        failed_probe = await cb.try_acquire("p1")
        assert failed_probe is not None

        await cb.record_failure("p1", permit=failed_probe)
        await cb.record_success("p1", permit=failed_probe)
        assert await cb.state("p1") == CircuitState.OPEN

        clock.advance(5.0)
        successful_probe = await cb.try_acquire("p1")
        assert successful_probe is not None
        await cb.record_success("p1", permit=successful_probe)
        await cb.record_failure("p1", immediate=True, permit=successful_probe)
        assert await cb.state("p1") == CircuitState.CLOSED

    async def test_normal_permit_result_is_consumed_once(self):
        cb = CircuitBreaker(failure_threshold=2)
        permit = await cb.try_acquire("p1")
        assert permit is not None

        await cb.record_failure("p1", permit=permit)
        await cb.record_failure("p1", permit=permit)

        assert await cb.state("p1") == CircuitState.CLOSED

    async def test_reset_invalidates_in_flight_permits_and_snapshot_entry(self):
        cb = CircuitBreaker(failure_threshold=1)
        permit = await cb.try_acquire("p1")
        assert permit is not None

        await cb.reset("p1")
        await cb.record_failure("p1", immediate=True, permit=permit)

        assert await cb.state("p1") == CircuitState.CLOSED
        assert "p1" not in await cb.get_all_states()
        replacement = await cb.try_acquire("p1")
        assert replacement is not None
        await cb.release(replacement)


class TestCircuitBreakerRouterIntegration:
    """Test that circuit breaker filters providers in Router._get_providers."""

    async def test_open_circuit_excludes_provider(self, sample_config, http_client):
        router = Router(sample_config, http_client)
        providers = await router._get_providers("haiku-router")
        assert len(providers) == 2

        await router.circuit_breaker.record_failure("anthropic", immediate=True)
        providers = await router._get_providers("haiku-router")
        assert len(providers) == 1
        assert providers[0].name == "zhipu"

    async def test_all_open_raises_error(self, sample_config, http_client):
        router = Router(sample_config, http_client)
        await router.circuit_breaker.record_failure("anthropic", immediate=True)
        await router.circuit_breaker.record_failure("zhipu", immediate=True)
        with pytest.raises(AllProvidersFailedError) as exc:
            await router._get_providers("haiku-router")
        assert "熔断" in str(exc.value)
        assert len(exc.value.errors) == 2

    async def test_recovery_restores_provider(self, sample_config, http_client):
        router = Router(sample_config, http_client)
        await router.circuit_breaker.record_failure("anthropic", immediate=True)
        assert len(await router._get_providers("haiku-router")) == 1

        await router.circuit_breaker.record_success("anthropic")
        assert len(await router._get_providers("haiku-router")) == 2

    async def test_candidate_enumeration_does_not_consume_probe(
        self, sample_config, http_client
    ):
        clock = _Clock()
        router = Router(sample_config, http_client)
        router.circuit_breaker = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout=5.0,
            clock=clock,
        )
        await router.circuit_breaker.record_failure("anthropic", immediate=True)
        clock.advance(5.0)

        first = await router._get_providers("haiku-router")
        second = await router._get_providers("haiku-router")

        assert [provider.name for provider in first] == ["anthropic", "zhipu"]
        assert [provider.name for provider in second] == ["anthropic", "zhipu"]
        permit = await router.circuit_breaker.try_acquire("anthropic")
        assert permit is not None and permit.probe
        await router.circuit_breaker.release(permit)

    async def test_concurrent_half_open_routes_send_one_probe(self):
        clock = _Clock()
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
            return _success_response()

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            router = Router(_single_provider_config(), client)
            router.circuit_breaker = CircuitBreaker(
                failure_threshold=1,
                recovery_timeout=5.0,
                clock=clock,
            )
            await router.circuit_breaker.record_failure("p1", immediate=True)
            clock.advance(5.0)

            first_task = asyncio.create_task(
                router.route_non_stream({"model": "m", "messages": []})
            )
            await asyncio.wait_for(entered.wait(), timeout=1.0)

            second_was_blocked = False
            try:
                await router.route_non_stream({"model": "m", "messages": []})
            except AllProvidersFailedError:
                second_was_blocked = True
            finally:
                release.set()

            first_result = await asyncio.wait_for(first_task, timeout=1.0)

        assert second_was_blocked
        assert calls == 1
        assert first_result["id"] == "msg_1"
        assert await router.circuit_breaker.state("p1") == CircuitState.CLOSED

    async def test_route_ignores_success_from_generation_before_open(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if body.get("metadata", {}).get("request") == "slow":
                entered.set()
                await release.wait()
                return _success_response()
            return httpx.Response(500, text="boom")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            router = Router(_single_provider_config(), client)
            slow_task = asyncio.create_task(
                router.route_non_stream(
                    {
                        "model": "m",
                        "messages": [],
                        "metadata": {"request": "slow"},
                    }
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=1.0)

            fast_failed = False
            try:
                await router.route_non_stream(
                    {
                        "model": "m",
                        "messages": [],
                        "metadata": {"request": "fast"},
                    }
                )
            except AllProvidersFailedError:
                fast_failed = True
            finally:
                release.set()

            slow_result = await asyncio.wait_for(slow_task, timeout=1.0)

        assert fast_failed
        assert slow_result["id"] == "msg_1"
        assert await router.circuit_breaker.state("p1") == CircuitState.OPEN

    async def test_non_retryable_probe_releases_permit(self):
        clock = _Clock()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="bad request")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            router = Router(_single_provider_config(), client)
            router.circuit_breaker = CircuitBreaker(
                failure_threshold=1,
                recovery_timeout=5.0,
                clock=clock,
            )
            await router.circuit_breaker.record_failure("p1", immediate=True)
            clock.advance(5.0)

            with pytest.raises(NonRetryableError):
                await router.route_non_stream({"model": "m", "messages": []})

            replacement = await router.circuit_breaker.try_acquire("p1")

        assert replacement is not None and replacement.probe
        await router.circuit_breaker.release(replacement)

    async def test_rate_limited_probe_releases_permit(self):
        clock = _Clock()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                text="rate limited",
                headers={"Retry-After": "0"},
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            router = Router(_single_provider_config(), client)
            router.circuit_breaker = CircuitBreaker(
                failure_threshold=1,
                recovery_timeout=5.0,
                clock=clock,
            )
            await router.circuit_breaker.record_failure("p1", immediate=True)
            clock.advance(5.0)

            with pytest.raises(NoProviderAvailableError):
                await router.route_non_stream({"model": "m", "messages": []})

            replacement = await router.circuit_breaker.try_acquire("p1")

        assert replacement is not None and replacement.probe
        await router.circuit_breaker.release(replacement)

    async def test_closing_stream_releases_probe_permit(self):
        clock = _Clock()

        async def body():
            yield b'event: message_start\ndata: {"type":"message_start"}\n\n'
            yield b'event: message_stop\ndata: {"type":"message_stop"}\n\n'

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=body())

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            router = Router(_single_provider_config(), client)
            router.circuit_breaker = CircuitBreaker(
                failure_threshold=1,
                recovery_timeout=5.0,
                clock=clock,
            )
            await router.circuit_breaker.record_failure("p1", immediate=True)
            clock.advance(5.0)

            outcome: dict = {}
            stream = router.route_stream(
                {"model": "m", "messages": []},
                outcome,
            )
            first_chunk = await anext(stream)
            await stream.aclose()
            replacement = await router.circuit_breaker.try_acquire("p1")

        assert b"message_start" in first_chunk
        assert outcome["provider_name"] == "p1"
        assert outcome["provider_model"] == "upstream-model"
        assert outcome["attempt"] == 1
        assert replacement is not None and replacement.probe
        await router.circuit_breaker.release(replacement)

"""Per-provider local concurrency limits, bounded queueing, and short cooldowns."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import structlog

from agent_router.config import ProviderConfig, VirtualModelConfig

logger = structlog.get_logger(__name__)


class ProviderCapacityError(Exception):
    """本地并发/队列已满或等待超时."""

    def __init__(
        self, provider: str, message: str, *, retry_after: float | None = None
    ) -> None:
        self.provider = provider
        self.retry_after = retry_after
        super().__init__(message)


class ProviderCooldownError(Exception):
    """Provider 处于短冷却（上游限流/过载）."""

    def __init__(self, provider: str, retry_after: float) -> None:
        self.provider = provider
        self.retry_after = retry_after
        super().__init__(
            f"provider '{provider}' 处于限流冷却，约 {retry_after:.1f}s 后可重试"
        )


@dataclass
class _GateState:
    max_concurrent: int = 0
    max_queue: int = 0
    queue_wait_timeout: float = 30.0
    rate_limit_cooldown: float = 30.0
    in_flight: int = 0
    waiting: int = 0
    cooldown_until: float = 0.0
    configured: bool = False
    active: bool = True
    generation: int = 0
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)


class ProviderGate:
    """按 provider name 管理本地并发、排队与短冷却."""

    def __init__(self) -> None:
        self._states: dict[str, _GateState] = {}

    def _get(self, name: str) -> _GateState:
        if name not in self._states:
            self._states[name] = _GateState()
        return self._states[name]

    def _apply_limits(self, state: _GateState, provider: ProviderConfig) -> None:
        state.max_concurrent = provider.max_concurrent
        state.max_queue = provider.max_queue
        state.queue_wait_timeout = provider.queue_wait_timeout
        state.rate_limit_cooldown = provider.rate_limit_cooldown

    def _schedule_notify(self, state: _GateState) -> None:
        """在事件循环中唤醒排队 waiter（热重载放宽限制时使用）."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _do() -> None:
            async with state.condition:
                state.condition.notify_all()

        loop.create_task(_do())

    def configure(self, providers: list[ProviderConfig]) -> None:
        """Apply authoritative limits and invalidate obsolete queued attempts.

        Calls that already own a slot may finish. Waiting calls are awakened
        whenever their Provider configuration changes and must re-enter routing
        instead of invoking an updated or removed Provider with stale settings.

        Args:
            providers: Complete active Provider set from the new configuration.
        """
        seen: set[str] = set()
        for p in providers:
            seen.add(p.name)
            state = self._get(p.name)
            self._apply_limits(state, p)
            state.configured = True
            state.active = True
            state.generation += 1
            if state.waiting:
                self._schedule_notify(state)

        for name, state in self._states.items():
            if name not in seen and state.active:
                state.configured = True
                state.active = False
                state.generation += 1
                self._schedule_notify(state)

    def configure_from_models(self, models: dict[str, VirtualModelConfig]) -> None:
        """从 AppConfig.models 收集全部 ProviderConfig 并配置."""
        by_name: dict[str, ProviderConfig] = {}
        for vm in models.values():
            for p in vm.providers:
                by_name.setdefault(p.name, p)
        self.configure(list(by_name.values()))

    def is_in_cooldown(self, name: str) -> bool:
        return self.cooldown_remaining(name) > 0

    def cooldown_remaining(self, name: str) -> float:
        state = self._get(name)
        return max(0.0, state.cooldown_until - time.monotonic())

    def enter_cooldown(self, name: str, seconds: float | None = None) -> float:
        """进入短冷却，返回当前剩余冷却秒数（含已有更长冷却）.

        ``seconds=None`` 使用配置默认值；显式 ``0`` 表示立即可重试（不套默认冷却）。
        """
        state = self._get(name)
        duration = state.rate_limit_cooldown if seconds is None else max(0.0, seconds)
        until = time.monotonic() + duration
        if until > state.cooldown_until:
            state.cooldown_until = until
        remaining = self.cooldown_remaining(name)
        logger.info(
            "provider.cooldown",
            provider=name,
            cooldown_seconds=round(duration, 2),
            remaining_seconds=round(remaining, 2),
        )
        # 唤醒排队者，使其重新检查冷却并退出队列
        self._schedule_notify(state)
        return remaining

    def clear_cooldown(self, name: str) -> None:
        self._get(name).cooldown_until = 0.0

    def snapshot(self) -> dict[str, dict]:
        """供监控 API 使用的状态快照."""
        now = time.monotonic()
        result: dict[str, dict] = {}
        for name, state in self._states.items():
            remaining = max(0.0, state.cooldown_until - now)
            result[name] = {
                "max_concurrent": state.max_concurrent,
                "max_queue": state.max_queue,
                "in_flight": state.in_flight,
                "waiting": state.waiting,
                "in_cooldown": remaining > 0,
                "cooldown_remaining": round(remaining, 2),
            }
        return result

    def _has_slot(self, state: _GateState) -> bool:
        return state.max_concurrent <= 0 or state.in_flight < state.max_concurrent

    def _ready_or_cooling(self, name: str, state: _GateState, generation: int) -> bool:
        """Return whether a waiter must acquire, cool down, or leave its generation."""
        return (
            state.generation != generation
            or not state.active
            or self._has_slot(state)
            or self.cooldown_remaining(name) > 0
        )

    def _raise_if_stale(self, name: str, state: _GateState, generation: int) -> None:
        """Reject a queued attempt invalidated by Provider reconfiguration."""
        if state.generation != generation or not state.active:
            raise ProviderCapacityError(
                name,
                f"provider '{name}' 配置已更新或移除，请重新路由",
                retry_after=0.0,
            )

    @asynccontextmanager
    async def slot(self, provider: ProviderConfig) -> AsyncIterator[None]:
        """Acquire one concurrency slot, queueing only within one config generation.

        Providers used outside a configured Router initialize their limits on
        first use. Once :meth:`configure` has established authoritative state,
        request-local snapshots cannot overwrite it.

        Args:
            provider: Provider snapshot associated with the routing attempt.

        Yields:
            Control while one Provider concurrency slot is held.

        Raises:
            ProviderCooldownError: If the Provider is in short cooldown.
            ProviderCapacityError: If capacity is unavailable, waiting times
                out, or hot reload invalidates the queued attempt.
        """
        name = provider.name
        state = self._get(name)

        async with state.condition:
            if not state.configured:
                self._apply_limits(state, provider)
                state.configured = True
            self._raise_if_stale(name, state, state.generation)
            generation = state.generation

            remaining = self.cooldown_remaining(name)
            if remaining > 0:
                raise ProviderCooldownError(name, remaining)

            if self._has_slot(state):
                state.in_flight += 1
            else:
                if state.max_queue <= 0:
                    raise ProviderCapacityError(
                        name,
                        f"provider '{name}' 并发已满且未启用排队",
                        retry_after=state.queue_wait_timeout,
                    )
                if state.waiting >= state.max_queue:
                    raise ProviderCapacityError(
                        name,
                        f"provider '{name}' 等待队列已满 (max_queue={state.max_queue})",
                        retry_after=state.queue_wait_timeout,
                    )
                state.waiting += 1
                wait_timeout = state.queue_wait_timeout
                try:
                    await asyncio.wait_for(
                        state.condition.wait_for(
                            lambda: self._ready_or_cooling(name, state, generation)
                        ),
                        timeout=wait_timeout,
                    )
                except TimeoutError:
                    raise ProviderCapacityError(
                        name,
                        f"provider '{name}' 排队等待超时 ({wait_timeout}s)",
                        retry_after=wait_timeout,
                    ) from None
                finally:
                    # 超时、取消、成功唤醒都要减 waiting，避免计数泄漏
                    state.waiting = max(0, state.waiting - 1)

                # 唤醒后重新检查冷却（持有 slot 的请求可能刚触发 429）
                self._raise_if_stale(name, state, generation)
                remaining = self.cooldown_remaining(name)
                if remaining > 0:
                    raise ProviderCooldownError(name, remaining)
                if not self._has_slot(state):
                    raise ProviderCapacityError(
                        name,
                        f"provider '{name}' 并发已满且未启用排队",
                        retry_after=state.queue_wait_timeout,
                    )
                state.in_flight += 1

        try:
            yield
        finally:
            async with state.condition:
                state.in_flight = max(0, state.in_flight - 1)
                state.condition.notify()

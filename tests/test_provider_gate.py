"""Tests for ProviderGate: concurrency, queue, cooldown."""

from __future__ import annotations

import asyncio

import pytest

from agent_router.config import ProviderConfig
from agent_router.provider_gate import (
    ProviderCapacityError,
    ProviderCooldownError,
    ProviderGate,
)


def _cfg(
    name: str = "p1",
    *,
    max_concurrent: int = 0,
    max_queue: int = 0,
    queue_wait_timeout: float = 0.2,
    rate_limit_cooldown: float = 1.0,
) -> ProviderConfig:
    return ProviderConfig(
        type="anthropic",
        name=name,
        model="m",
        api_key="k",
        base_url="https://example.com",
        priority=1,
        max_concurrent=max_concurrent,
        max_queue=max_queue,
        queue_wait_timeout=queue_wait_timeout,
        rate_limit_cooldown=rate_limit_cooldown,
    )


class TestProviderGateConcurrency:
    async def test_request_cannot_overwrite_authoritative_hot_reload_limits(self):
        gate = ProviderGate()
        gate.configure([_cfg(max_concurrent=3, max_queue=4)])

        async with gate.slot(_cfg(max_concurrent=1, max_queue=0)):
            snapshot = gate.snapshot()["p1"]

        assert snapshot["max_concurrent"] == 3
        assert snapshot["max_queue"] == 4

    async def test_removing_provider_rejects_queued_request(self):
        gate = ProviderGate()
        cfg = _cfg(max_concurrent=1, max_queue=1, queue_wait_timeout=2.0)
        gate.configure([cfg])
        entered = asyncio.Event()
        release = asyncio.Event()

        async def holder():
            async with gate.slot(cfg):
                entered.set()
                await release.wait()

        async def waiter():
            async with gate.slot(cfg):
                raise AssertionError("removed provider must not receive queued work")

        hold_task = asyncio.create_task(holder())
        await entered.wait()
        wait_task = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)

        gate.configure([])

        with pytest.raises(ProviderCapacityError, match="配置已更新或移除"):
            await wait_task
        release.set()
        await hold_task
        assert gate.snapshot()["p1"]["waiting"] == 0

    async def test_unlimited_by_default(self):
        gate = ProviderGate()
        cfg = _cfg(max_concurrent=0)
        async with gate.slot(cfg):
            async with gate.slot(cfg):
                pass

    async def test_max_concurrent_blocks_without_queue(self):
        gate = ProviderGate()
        cfg = _cfg(max_concurrent=1, max_queue=0)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def holder():
            async with gate.slot(cfg):
                entered.set()
                await release.wait()

        task = asyncio.create_task(holder())
        await entered.wait()
        with pytest.raises(ProviderCapacityError) as exc:
            async with gate.slot(cfg):
                pass
        assert "并发已满" in str(exc.value)
        release.set()
        await task

    async def test_queue_overflow(self):
        gate = ProviderGate()
        cfg = _cfg(max_concurrent=1, max_queue=1, queue_wait_timeout=2.0)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def holder():
            async with gate.slot(cfg):
                entered.set()
                await release.wait()

        async def waiter():
            async with gate.slot(cfg):
                pass

        task = asyncio.create_task(holder())
        await entered.wait()
        wait_task = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)
        with pytest.raises(ProviderCapacityError) as exc:
            async with gate.slot(cfg):
                pass
        assert "等待队列已满" in str(exc.value)
        release.set()
        await task
        await wait_task

    async def test_queue_wait_timeout(self):
        gate = ProviderGate()
        cfg = _cfg(max_concurrent=1, max_queue=2, queue_wait_timeout=0.05)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def holder():
            async with gate.slot(cfg):
                entered.set()
                await release.wait()

        task = asyncio.create_task(holder())
        await entered.wait()
        with pytest.raises(ProviderCapacityError) as exc:
            async with gate.slot(cfg):
                pass
        assert "排队等待超时" in str(exc.value)
        release.set()
        await task


class TestProviderGateCooldown:
    async def test_enter_and_block(self):
        gate = ProviderGate()
        cfg = _cfg(rate_limit_cooldown=30.0)
        gate.enter_cooldown("p1", 30.0)
        assert gate.is_in_cooldown("p1")
        with pytest.raises(ProviderCooldownError) as exc:
            async with gate.slot(cfg):
                pass
        assert exc.value.retry_after > 0

    async def test_cooldown_expires(self):
        gate = ProviderGate()
        cfg = _cfg()
        gate.enter_cooldown("p1", 0.05)
        # Windows 的事件循环时钟分辨率可能为 15.625ms，定时回调可能提前一拍。
        await asyncio.sleep(0.1)
        assert not gate.is_in_cooldown("p1")
        async with gate.slot(cfg):
            pass

    async def test_default_cooldown_from_config(self):
        gate = ProviderGate()
        cfg = _cfg(rate_limit_cooldown=12.0)
        gate.configure([cfg])
        duration = gate.enter_cooldown("p1")
        assert duration == pytest.approx(12.0, abs=0.05)

    async def test_enter_cooldown_returns_longer_remaining(self):
        gate = ProviderGate()
        gate.enter_cooldown("p1", 30.0)
        remaining = gate.enter_cooldown("p1", 5.0)
        assert remaining > 25.0

    async def test_enter_cooldown_honors_explicit_zero(self):
        """Retry-After: 0 不应套用配置默认冷却."""
        gate = ProviderGate()
        cfg = _cfg(rate_limit_cooldown=30.0)
        gate.configure([cfg])
        remaining = gate.enter_cooldown("p1", 0.0)
        assert remaining == 0.0
        assert not gate.is_in_cooldown("p1")
        async with gate.slot(cfg):
            pass

    async def test_configure_unlimited_invalidates_waiter(self):
        gate = ProviderGate()
        cfg = _cfg(max_concurrent=1, max_queue=2, queue_wait_timeout=2.0)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def holder():
            async with gate.slot(cfg):
                entered.set()
                await release.wait()

        async def waiter():
            async with gate.slot(cfg):
                raise AssertionError("stale waiter must re-enter routing")

        task = asyncio.create_task(holder())
        await entered.wait()
        wait_task = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)
        # 即使热重载放宽限制，旧 waiter 也可能携带旧 URL/key，必须重新路由。
        gate.configure([_cfg(max_concurrent=0, max_queue=0)])
        with pytest.raises(ProviderCapacityError, match="配置已更新或移除"):
            await wait_task
        release.set()
        await task

    async def test_queued_waiter_rechecks_cooldown(self):
        """持有者进入冷却并释放槽位后，排队者应收到冷却错误而非立刻打上游."""
        gate = ProviderGate()
        cfg = _cfg(max_concurrent=1, max_queue=2, queue_wait_timeout=2.0)
        entered = asyncio.Event()

        async def holder():
            async with gate.slot(cfg):
                entered.set()
                await asyncio.sleep(0.05)
                gate.enter_cooldown("p1", 30.0)

        task = asyncio.create_task(holder())
        await entered.wait()
        with pytest.raises(ProviderCooldownError):
            async with gate.slot(cfg):
                pass
        await task
        assert gate.snapshot()["p1"]["waiting"] == 0

    async def test_cancel_while_queued_decrements_waiting(self):
        gate = ProviderGate()
        cfg = _cfg(max_concurrent=1, max_queue=2, queue_wait_timeout=5.0)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def holder():
            async with gate.slot(cfg):
                entered.set()
                await release.wait()

        async def waiter():
            async with gate.slot(cfg):
                pass

        hold_task = asyncio.create_task(holder())
        await entered.wait()
        wait_task = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)
        assert gate.snapshot()["p1"]["waiting"] == 1
        wait_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await wait_task
        assert gate.snapshot()["p1"]["waiting"] == 0
        release.set()
        await hold_task

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from structlog import get_logger

logger = get_logger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class CircuitPermit:
    """Identify one provider attempt against a circuit generation."""

    provider: str
    generation: int
    sequence: int
    probe: bool


class CircuitBreaker:
    """Per-provider circuit breaker.

    CLOSED  → normal operation, requests pass through.
    OPEN    → circuit tripped, requests rejected until recovery_timeout.
    HALF_OPEN → one probe request allowed to test recovery.

    Parameters
    ----------
    failure_threshold:
        Consecutive failures before opening the circuit.
    recovery_timeout:
        Seconds to wait in OPEN state before transitioning to HALF_OPEN.
    clock:
        Monotonic clock used to evaluate recovery timeouts.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 600.0,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._clock = clock or time.monotonic
        self._failure_counts: dict[str, int] = {}
        self._states: dict[str, CircuitState] = {}
        self._last_failure_time: dict[str, float] = {}
        self._generations: dict[str, int] = {}
        self._active_permits: dict[str, set[CircuitPermit]] = {}
        self._active_probes: dict[str, CircuitPermit] = {}
        self._permit_sequence = 0
        self._lock = asyncio.Lock()

    def _state_locked(self, provider: str, recovery_timeout: float) -> CircuitState:
        current = self._states.get(provider, CircuitState.CLOSED)
        if current == CircuitState.OPEN:
            elapsed = self._clock() - self._last_failure_time.get(provider, 0)
            if elapsed >= recovery_timeout:
                current = CircuitState.HALF_OPEN
                self._states[provider] = current
                logger.info(
                    "circuit.half_open",
                    provider=provider,
                    recovery_timeout=recovery_timeout,
                )
        return current

    def _advance_generation_locked(self, provider: str) -> None:
        self._generations[provider] = self._generations.get(provider, 0) + 1
        self._active_permits.pop(provider, None)
        self._active_probes.pop(provider, None)

    def _permit_is_current_locked(self, provider: str, permit: CircuitPermit) -> bool:
        if permit.provider != provider:
            return False
        if permit.generation != self._generations.get(provider, 0):
            return False
        if permit not in self._active_permits.get(provider, set()):
            return False
        if permit.probe:
            return (
                self._states.get(provider, CircuitState.CLOSED)
                == CircuitState.HALF_OPEN
                and self._active_probes.get(provider) == permit
            )
        return self._states.get(provider, CircuitState.CLOSED) == CircuitState.CLOSED

    def _consume_permit_locked(self, permit: CircuitPermit) -> None:
        active = self._active_permits.get(permit.provider)
        if active is not None:
            active.discard(permit)
            if not active:
                self._active_permits.pop(permit.provider, None)
        if self._active_probes.get(permit.provider) == permit:
            self._active_probes.pop(permit.provider, None)

    def _open_locked(
        self,
        provider: str,
        *,
        failures: int,
        threshold: int,
        reason: str,
    ) -> None:
        self._failure_counts[provider] = failures
        self._states[provider] = CircuitState.OPEN
        self._last_failure_time[provider] = self._clock()
        self._advance_generation_locked(provider)
        logger.warning(
            "circuit.opened",
            provider=provider,
            reason=reason,
            failures=failures,
            threshold=threshold,
            recovery_timeout=self.recovery_timeout,
            msg="provider circuit breaker opened",
        )

    async def state(
        self, provider: str, recovery_timeout: float | None = None
    ) -> CircuitState:
        """Return the provider state, applying an elapsed recovery timeout."""
        timeout = (
            recovery_timeout if recovery_timeout is not None else self.recovery_timeout
        )
        async with self._lock:
            return self._state_locked(provider, timeout)

    async def is_available(
        self, provider: str, recovery_timeout: float | None = None
    ) -> bool:
        """Return whether a request may proceed to permit acquisition.

        This is an observation hint, not permission to call the provider. Callers
        must still use :meth:`try_acquire` immediately before a real attempt.
        """
        return (
            await self.state(provider, recovery_timeout=recovery_timeout)
            != CircuitState.OPEN
        )

    async def try_acquire(
        self, provider: str, recovery_timeout: float | None = None
    ) -> CircuitPermit | None:
        """Acquire permission for one real provider attempt.

        CLOSED circuits issue generation-bound permits freely. HALF_OPEN circuits
        issue exactly one probe permit until that probe completes or releases it.
        OPEN circuits return ``None`` until their recovery timeout elapses.

        Args:
            provider: Provider whose circuit guards the attempt.
            recovery_timeout: Optional provider-specific OPEN timeout.

        Returns:
            A one-use attempt permit, or ``None`` while OPEN or while another
            HALF_OPEN probe is active.
        """
        timeout = (
            recovery_timeout if recovery_timeout is not None else self.recovery_timeout
        )
        async with self._lock:
            current = self._state_locked(provider, timeout)
            if current == CircuitState.OPEN:
                return None

            if current == CircuitState.HALF_OPEN and provider in self._active_probes:
                return None

            generation = self._generations.get(provider, 0)
            self._permit_sequence += 1
            permit = CircuitPermit(
                provider=provider,
                generation=generation,
                sequence=self._permit_sequence,
                probe=current == CircuitState.HALF_OPEN,
            )
            self._active_permits.setdefault(provider, set()).add(permit)
            if permit.probe:
                self._active_probes[provider] = permit
            return permit

    async def release(self, permit: CircuitPermit) -> None:
        """Release an unfinished attempt without changing circuit state.

        Releasing an obsolete or already consumed permit is a no-op. In
        particular, releasing a HALF_OPEN probe allows a later request to probe.

        Args:
            permit: The one-use permit returned by :meth:`try_acquire`.
        """
        async with self._lock:
            self._consume_permit_locked(permit)

    async def record_success(
        self, provider: str, *, permit: CircuitPermit | None = None
    ) -> None:
        """Record a successful attempt and consume its permit.

        Obsolete, released, and previously consumed permits are ignored. Omitting
        ``permit`` performs an authoritative control-plane update and invalidates
        existing permits when it closes the circuit.

        Args:
            provider: Provider whose attempt succeeded.
            permit: Permit associated with the completed attempt, if any.
        """
        async with self._lock:
            if permit is not None and not self._permit_is_current_locked(
                provider, permit
            ):
                return
            if permit is not None:
                self._consume_permit_locked(permit)
            prev = self._states.get(provider, CircuitState.CLOSED)
            self._failure_counts.pop(provider, None)
            self._states[provider] = CircuitState.CLOSED
            self._last_failure_time.pop(provider, None)
            if permit is None or permit.probe:
                self._advance_generation_locked(provider)
        if prev != CircuitState.CLOSED:
            logger.info(
                "circuit.closed",
                provider=provider,
                prev_state=prev.value,
                msg="provider recovered, circuit breaker closed",
            )

    async def record_failure(
        self,
        provider: str,
        *,
        immediate: bool = False,
        failure_threshold: int | None = None,
        permit: CircuitPermit | None = None,
    ) -> None:
        """Record a failed attempt and consume its permit.

        Obsolete, released, and previously consumed permits are ignored. A valid
        HALF_OPEN failure reopens immediately; a CLOSED failure opens at the
        selected threshold. Omitting ``permit`` is an authoritative update used
        outside a routed attempt.

        Args:
            provider: Provider whose attempt failed.
            immediate: Whether to open immediately, such as for an auth failure.
            failure_threshold: Optional provider-specific failure threshold.
            permit: Permit associated with the completed attempt, if any.
        """
        threshold = (
            failure_threshold
            if failure_threshold is not None
            else self.failure_threshold
        )
        async with self._lock:
            if permit is not None and not self._permit_is_current_locked(
                provider, permit
            ):
                return
            if permit is not None:
                self._consume_permit_locked(permit)

            current = self._states.get(provider, CircuitState.CLOSED)
            if current == CircuitState.HALF_OPEN:
                self._open_locked(
                    provider,
                    failures=max(self._failure_counts.get(provider, 0), threshold),
                    threshold=threshold,
                    reason="probe_failure",
                )
                return

            if immediate:
                self._open_locked(
                    provider,
                    failures=threshold,
                    threshold=threshold,
                    reason="auth_error",
                )
                return

            count = self._failure_counts.get(provider, 0) + 1
            self._failure_counts[provider] = count

            if count >= threshold:
                self._open_locked(
                    provider,
                    failures=count,
                    threshold=threshold,
                    reason="consecutive_failures",
                )
            else:
                logger.debug(
                    "circuit.failure_counted",
                    provider=provider,
                    consecutive_failures=count,
                    threshold=threshold,
                )

    async def reset(self, provider: str) -> None:
        """Reset one provider and invalidate all permits issued before the reset."""
        async with self._lock:
            self._advance_generation_locked(provider)
            self._failure_counts.pop(provider, None)
            self._states.pop(provider, None)
            self._last_failure_time.pop(provider, None)

    async def get_all_states(self) -> dict[str, CircuitState]:
        """Return current state of all known providers."""
        async with self._lock:
            return {
                p: self._states.get(p, CircuitState.CLOSED)
                for p in set(self._states) | set(self._failure_counts)
            }

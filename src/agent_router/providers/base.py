from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator

import httpx

from agent_router.config import ProviderConfig


class NonRetryableError(Exception):
    """Represent an upstream error that failover cannot safely retry.

    ``status_code`` is preserved only when the upstream response is a client
    error that can be returned to the caller. Protocol and decoding errors
    leave it unset and remain gateway failures.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RetryableError(Exception):
    """可重试的错误 (5xx、429、超时、连接错误等)."""

    def __init__(
        self,
        message: str,
        *,
        immediate_break: bool = False,
        rate_limited: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.immediate_break = immediate_break
        self.rate_limited = rate_limited
        self.retry_after = retry_after


class BaseProvider(ABC):
    def __init__(self, config: ProviderConfig, http_client: httpx.AsyncClient) -> None:
        self.config = config
        self.http = http_client

    @abstractmethod
    async def send(self, request_body: dict) -> dict:
        """非流式请求，返回完整响应 JSON."""

    @abstractmethod
    def send_stream(self, request_body: dict) -> AsyncGenerator[bytes, None]:
        """Return an SSE byte stream that callers must close after consumption."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from email.utils import parsedate_to_datetime
from math import isfinite
from time import time
from typing import Final

import httpx
from structlog import get_logger

from agent_router.providers.base import BaseProvider, NonRetryableError, RetryableError

logger = get_logger(__name__)

RATE_LIMIT_STATUSES: set[int] = {429, 529}
AUTH_STATUSES: set[int] = {401, 403}
RETRYABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)
MAX_RETRY_AFTER_SECONDS = 86_400.0
FORWARDED_ANTHROPIC_HEADERS_KEY: Final = "_agent_router_anthropic_headers"
DEFAULT_ANTHROPIC_VERSION: Final = "2023-06-01"


def parse_retry_after(value: str | None) -> float | None:
    """Parse and bound a Retry-After delay to at most 24 hours.

    Return ``None`` for missing, malformed, negative, or non-finite values so
    the configured provider cooldown remains the safe fallback.
    """
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
        if isfinite(seconds) and seconds >= 0:
            return min(seconds, MAX_RETRY_AFTER_SECONDS)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        delay = dt.timestamp() - time()
        if not isfinite(delay):
            return None
        return min(max(0.0, delay), MAX_RETRY_AFTER_SECONDS)
    except (TypeError, ValueError, OverflowError):
        return None


def _classify_error(e: httpx.HTTPStatusError) -> Exception:
    status = e.response.status_code
    body = e.response.text[:500]
    if status in AUTH_STATUSES:
        return RetryableError(
            f"HTTP {status}: {body}",
            immediate_break=True,
        )
    if status in RATE_LIMIT_STATUSES:
        retry_after = parse_retry_after(e.response.headers.get("Retry-After"))
        return RetryableError(
            f"HTTP {status}: {body}",
            rate_limited=True,
            retry_after=retry_after,
        )
    if 500 <= status < 600:
        return RetryableError(f"HTTP {status}: {body}")
    return NonRetryableError(
        f"HTTP {status}: {body}",
        status_code=status if 400 <= status < 500 else None,
    )


def _classify_exception(e: Exception) -> Exception:
    if isinstance(e, RETRYABLE_EXCEPTIONS):
        return RetryableError(str(e))
    return NonRetryableError(str(e))


class AnthropicCompatProvider(BaseProvider):
    """Anthropic Messages API 兼容直通适配器.

    适用于 Anthropic 官方 API 及兼容 Anthropic 格式的第三方 API (如智谱 GLM).
    """

    async def send(self, request_body: dict) -> dict:
        url = f"{self.config.base_url}/v1/messages"
        headers = self._build_headers(request_body)
        modified_body = self._prepare_body(request_body)

        try:
            response = await self.http.post(
                url,
                json=modified_body,
                headers=headers,
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise _classify_error(e) from e
        except Exception as e:
            raise _classify_exception(e) from e

        try:
            return response.json()
        except Exception as e:
            raise NonRetryableError("响应不是有效的 JSON") from e

    async def send_stream(self, request_body: dict) -> AsyncGenerator[bytes, None]:
        """Yield upstream SSE bytes and close the HTTP response when finalized."""
        url = f"{self.config.base_url}/v1/messages"
        headers = self._build_headers(request_body)
        headers["Accept-Encoding"] = "identity"
        modified_body = {**self._prepare_body(request_body), "stream": True}

        try:
            async with self.http.stream(
                "POST",
                url,
                json=modified_body,
                headers=headers,
                timeout=self.config.timeout_seconds,
            ) as response:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as e:
                    await e.response.aread()
                    raise _classify_error(e) from e

                async for chunk in response.aiter_bytes():
                    yield chunk
        except (NonRetryableError, RetryableError):
            raise
        except Exception as e:
            raise _classify_exception(e) from e

    def _build_headers(self, request_body: dict) -> dict[str, str]:
        """Build authenticated upstream headers from trusted router metadata."""
        forwarded = request_body.get(FORWARDED_ANTHROPIC_HEADERS_KEY)
        version = request_body.get("anthropic_version", DEFAULT_ANTHROPIC_VERSION)
        if not isinstance(version, str) or not version:
            version = DEFAULT_ANTHROPIC_VERSION
        beta: str | None = None
        if isinstance(forwarded, dict):
            forwarded_version = forwarded.get("anthropic-version")
            if isinstance(forwarded_version, str) and forwarded_version:
                version = forwarded_version
            forwarded_beta = forwarded.get("anthropic-beta")
            if isinstance(forwarded_beta, str) and forwarded_beta:
                beta = forwarded_beta

        headers = {
            "Content-Type": "application/json",
            "anthropic-version": version,
        }
        if beta is not None:
            headers["anthropic-beta"] = beta
        # 尝试多种认证 header 格式
        if self.config.api_key.startswith("sk-ant"):
            headers["x-api-key"] = self.config.api_key
        else:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _prepare_body(self, request_body: dict) -> dict:
        """Replace the virtual model and remove router-only metadata."""
        body = {**request_body}
        body.pop(FORWARDED_ANTHROPIC_HEADERS_KEY, None)
        # 兼容旧的 body 内版本提示，但不把非标准字段发送给上游。
        body.pop("anthropic_version", None)
        body["model"] = self.config.model
        return body

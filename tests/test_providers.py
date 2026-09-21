from __future__ import annotations

import json
from typing import cast

import httpx
import pytest
from agent_router.config import ProviderConfig
from agent_router.providers.anthropic_compat import (
    FORWARDED_ANTHROPIC_HEADERS_KEY,
    MAX_RETRY_AFTER_SECONDS,
    AnthropicCompatProvider,
    parse_retry_after,
)
from agent_router.providers.base import RetryableError


@pytest.fixture
def provider(http_client):
    config = ProviderConfig(
        type="anthropic",
        model="claude-haiku-4-5-20251001",
        api_key="sk-ant-test-key",
        base_url="https://api.anthropic.com",
        priority=1,
    )
    return AnthropicCompatProvider(config, http_client)


class TestAnthropicCompatProvider:
    def test_prepare_body_replaces_model(self, provider):
        body = {
            "model": "haiku-router",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hello"}],
        }
        result = provider._prepare_body(body)
        assert result["model"] == "claude-haiku-4-5-20251001"
        assert result["max_tokens"] == 100
        assert result["messages"] == body["messages"]

    def test_prepare_body_does_not_mutate_original(self, provider):
        body = {"model": "original", "max_tokens": 100, "messages": []}
        provider._prepare_body(body)
        assert body["model"] == "original"

    def test_build_headers_with_anthropic_key(self, provider):
        body = {}
        headers = provider._build_headers(body)
        assert headers["x-api-key"] == "sk-ant-test-key"
        assert headers["Content-Type"] == "application/json"
        assert "Accept-Encoding" not in headers

    def test_build_headers_with_bearer_key(self, http_client):
        config = ProviderConfig(
            type="anthropic",
            model="test",
            api_key="not-anthropic-key-format",
            base_url="https://test.com",
            priority=1,
        )
        provider = AnthropicCompatProvider(config, http_client)
        headers = provider._build_headers({})
        assert headers["Authorization"] == "Bearer not-anthropic-key-format"

    def test_build_headers_forwards_version_and_beta_without_body_leak(self, provider):
        body = {
            "model": "virtual",
            "anthropic_version": "legacy-version",
            FORWARDED_ANTHROPIC_HEADERS_KEY: {
                "anthropic-version": "2026-07-01",
                "anthropic-beta": "context-1m-2025-08-07",
            },
        }

        headers = provider._build_headers(body)
        prepared = provider._prepare_body(body)

        assert headers["anthropic-version"] == "2026-07-01"
        assert headers["anthropic-beta"] == "context-1m-2025-08-07"
        assert FORWARDED_ANTHROPIC_HEADERS_KEY not in prepared
        assert "anthropic_version" not in prepared

    @pytest.mark.asyncio
    async def test_send_retryable_429(self):
        """测试 HTTP 429 触发可重试限流错误."""
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                429, text="rate limited", headers={"Retry-After": "15"}
            )
        )
        config = ProviderConfig(
            type="anthropic",
            model="test",
            api_key="sk-test",
            base_url="https://api.example.com",
            priority=1,
        )
        async with httpx.AsyncClient(transport=transport) as client:
            provider = AnthropicCompatProvider(config, client)
            with pytest.raises(RetryableError) as exc:
                await provider.send({"model": "test", "max_tokens": 10, "messages": []})
            assert exc.value.rate_limited is True
            assert exc.value.retry_after == 15.0

    @pytest.mark.asyncio
    async def test_send_retryable_529(self):
        """测试 HTTP 529 触发可重试限流错误."""
        transport = httpx.MockTransport(
            lambda request: httpx.Response(529, text="overloaded")
        )
        config = ProviderConfig(
            type="anthropic",
            model="test",
            api_key="sk-test",
            base_url="https://api.example.com",
            priority=1,
        )
        async with httpx.AsyncClient(transport=transport) as client:
            provider = AnthropicCompatProvider(config, client)
            with pytest.raises(RetryableError) as exc:
                await provider.send({"model": "test", "max_tokens": 10, "messages": []})
            assert exc.value.rate_limited is True

    @pytest.mark.parametrize("status", [500, 501, 507, 599])
    @pytest.mark.asyncio
    async def test_send_retries_every_server_error(self, status):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(status, text="upstream unavailable")
        )
        config = ProviderConfig(
            type="anthropic",
            model="test",
            api_key="sk-test",
            base_url="https://api.example.com",
            priority=1,
        )
        async with httpx.AsyncClient(transport=transport) as client:
            provider = AnthropicCompatProvider(config, client)
            with pytest.raises(RetryableError, match=f"HTTP {status}"):
                await provider.send({"model": "test", "max_tokens": 10, "messages": []})

    @pytest.mark.parametrize(
        "error_type",
        [httpx.WriteTimeout, httpx.ReadError, httpx.WriteError],
    )
    @pytest.mark.asyncio
    async def test_send_retries_transient_transport_errors(self, error_type):
        def handler(request: httpx.Request) -> httpx.Response:
            raise error_type("transient transport failure", request=request)

        transport = httpx.MockTransport(handler)
        config = ProviderConfig(
            type="anthropic",
            model="test",
            api_key="sk-test",
            base_url="https://api.example.com",
            priority=1,
        )
        async with httpx.AsyncClient(transport=transport) as client:
            provider = AnthropicCompatProvider(config, client)
            with pytest.raises(RetryableError, match="transient transport failure"):
                await provider.send({"model": "test", "max_tokens": 10, "messages": []})

    @pytest.mark.asyncio
    async def test_send_retryable_401(self):
        """测试 HTTP 401 (鉴权失败) 触发可重试错误，允许路由切换 provider."""
        transport = httpx.MockTransport(
            lambda request: httpx.Response(401, text="unauthorized")
        )
        config = ProviderConfig(
            type="anthropic",
            model="test",
            api_key="invalid",
            base_url="https://api.example.com",
            priority=1,
        )
        async with httpx.AsyncClient(transport=transport) as client:
            provider = AnthropicCompatProvider(config, client)
            with pytest.raises(RetryableError):
                await provider.send({"model": "test", "max_tokens": 10, "messages": []})

    @pytest.mark.asyncio
    async def test_send_uses_default_accept_encoding(self):
        """非流式请求不强制 identity，避免大 JSON 响应失去压缩。"""
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = dict(request.headers)
            return httpx.Response(200, json={"id": "msg_1", "usage": {}})

        transport = httpx.MockTransport(handler)
        config = ProviderConfig(
            type="anthropic",
            model="real-model",
            api_key="sk-test",
            base_url="https://api.example.com",
            priority=1,
        )
        async with httpx.AsyncClient(transport=transport) as client:
            provider = AnthropicCompatProvider(config, client)
            await provider.send({"model": "virtual", "max_tokens": 10, "messages": []})

        headers = seen["headers"]
        assert isinstance(headers, dict)
        assert cast(dict[str, str], headers)["accept-encoding"] != "identity"

    @pytest.mark.asyncio
    async def test_send_stream_requests_identity_encoding(self):
        """流式上游请求禁用压缩，避免 SSE 在解压层被聚合后才下发。"""
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = dict(request.headers)
            seen["body"] = request.read()
            return httpx.Response(
                200,
                content=b"event: message_start\ndata: {}\n\n",
                headers={"content-type": "text/event-stream"},
            )

        transport = httpx.MockTransport(handler)
        config = ProviderConfig(
            type="anthropic",
            model="real-model",
            api_key="sk-test",
            base_url="https://api.example.com",
            priority=1,
        )
        async with httpx.AsyncClient(transport=transport) as client:
            provider = AnthropicCompatProvider(config, client)
            chunks = [
                chunk
                async for chunk in provider.send_stream(
                    {"model": "virtual", "max_tokens": 10, "messages": []}
                )
            ]

        headers = seen["headers"]
        body = seen["body"]
        assert isinstance(headers, dict)
        assert isinstance(body, bytes)
        assert cast(dict[str, str], headers)["accept-encoding"] == "identity"
        assert json.loads(body)["stream"] is True
        assert b"".join(chunks) == b"event: message_start\ndata: {}\n\n"

    def test_strip_trailing_slash(self):
        config = ProviderConfig(
            type="anthropic",
            model="test",
            api_key="k",
            base_url="https://api.com/",
            priority=1,
        )
        assert config.base_url == "https://api.com"


@pytest.mark.parametrize("value", ["inf", "1e309", "-1", "not-a-delay"])
def test_parse_retry_after_rejects_unsafe_values(value):
    assert parse_retry_after(value) is None


def test_parse_retry_after_caps_large_delays():
    assert parse_retry_after("999999") == MAX_RETRY_AFTER_SECONDS

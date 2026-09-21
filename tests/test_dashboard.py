from __future__ import annotations

import json
from typing import cast

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from agent_router import dashboard as dashboard_module
from agent_router.dashboard import create_dashboard_app, find_dashboard_dist


def _create_dist(tmp_path):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        '<html><script type="module" src="/assets/app.js"></script></html>',
        encoding="utf-8",
    )
    (assets / "app.js").write_text("console.log('ok')", encoding="utf-8")
    return dist


def test_find_dashboard_dist_accepts_explicit_path(tmp_path):
    dist = _create_dist(tmp_path)

    assert find_dashboard_dist(dist) == dist.resolve()


def test_find_dashboard_dist_rejects_invalid_explicit_path_without_fallback(
    tmp_path, monkeypatch
):
    _create_dist(tmp_path / "dashboard")
    broken_dist = tmp_path / "broken-dist"
    broken_dist.mkdir()
    monkeypatch.chdir(tmp_path)

    assert find_dashboard_dist(broken_dist) is None


@pytest.mark.asyncio
async def test_dashboard_serves_spa_and_assets(tmp_path):
    dist = _create_dist(tmp_path)
    app = create_dashboard_app(dist)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        index = await client.get("/")
        nested = await client.get("/config/providers")
        asset = await client.get("/assets/app.js")

    assert index.status_code == 200
    assert "/assets/app.js" in index.text
    assert nested.status_code == 200
    assert "/assets/app.js" in nested.text
    assert asset.status_code == 200
    assert "console.log" in asset.text


@pytest.mark.asyncio
async def test_dashboard_proxies_router_api(tmp_path, httpx_mock):
    dist = _create_dist(tmp_path)
    httpx_mock.add_response(
        method="GET",
        url="http://router.local/api/metrics/summary",
        json={"total_calls": 0},
    )
    app = create_dashboard_app(dist, router_base_url="http://router.local")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/metrics/summary")

    assert response.status_code == 200, response.text
    assert response.json() == {"total_calls": 0}


@pytest.mark.asyncio
async def test_dashboard_proxy_strips_connection_nominated_headers(
    tmp_path, httpx_mock
):
    dist = _create_dist(tmp_path)
    httpx_mock.add_response(
        method="GET",
        url="http://router.local/api/metrics/summary",
        json={"total_calls": 0},
        headers={
            "Connection": "x-router-only",
            "X-Router-Only": "response-secret",
        },
    )
    app = create_dashboard_app(dist, router_base_url="http://router.local")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/metrics/summary",
            headers={
                "Connection": "keep-alive, x-dashboard-only",
                "X-Dashboard-Only": "request-secret",
            },
        )

    upstream_request = httpx_mock.get_request()
    assert upstream_request is not None
    assert upstream_request.headers.get("connection") != "keep-alive, x-dashboard-only"
    assert "x-dashboard-only" not in upstream_request.headers
    assert "connection" not in response.headers
    assert "x-router-only" not in response.headers


@pytest.mark.asyncio
async def test_dashboard_rejects_chunked_body_over_limit(tmp_path, monkeypatch):
    dist = _create_dist(tmp_path)
    monkeypatch.setattr(dashboard_module, "_MAX_PROXY_BODY_BYTES", 8)

    class FakeRouterClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def request(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("oversized body must not reach the router")

        async def aclose(self) -> None:
            pass

    async def oversized_body():
        yield b'{"model"'
        yield b':"router"}'

    monkeypatch.setattr(dashboard_module.httpx, "AsyncClient", FakeRouterClient)
    app = create_dashboard_app(dist, router_base_url="http://router.local")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/messages", content=oversized_body())

    assert response.status_code == 413
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert "8 字节" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_dashboard_proxy_strips_decoded_content_encoding(tmp_path, monkeypatch):
    dist = _create_dist(tmp_path)
    seen: dict[str, object] = {}

    class FakeRouterResponse:
        content = b'{"total_calls":0}'
        status_code = 200
        headers = {
            "content-encoding": "gzip",
            "content-type": "application/json",
        }

    class FakeRouterClient:
        def __init__(self, **kwargs: object) -> None:
            seen["init"] = kwargs

        async def request(
            self,
            method: str,
            path: str,
            *,
            content: bytes,
            headers: list[tuple[str, str]],
        ) -> FakeRouterResponse:
            seen["request"] = {
                "method": method,
                "path": path,
                "content": content,
                "headers": headers,
            }
            return FakeRouterResponse()

        async def aclose(self) -> None:
            seen["closed"] = True

    monkeypatch.setattr(dashboard_module.httpx, "AsyncClient", FakeRouterClient)
    app = create_dashboard_app(dist, router_base_url="http://router.local")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/metrics/summary")

    upstream_request = cast(dict[str, object], seen["request"])
    assert isinstance(upstream_request, dict)
    upstream_headers = cast(list[tuple[str, str]], upstream_request["headers"])
    request_headers = {key.lower(): value for key, value in upstream_headers}
    assert request_headers["accept-encoding"] == "identity"
    assert response.status_code == 200, response.text
    assert "content-encoding" not in response.headers
    assert response.json() == {"total_calls": 0}


@pytest.mark.asyncio
async def test_dashboard_streams_v1_messages_without_buffering(tmp_path, monkeypatch):
    dist = _create_dist(tmp_path)
    seen: dict[str, object] = {}

    class FakeRouterStreamResponse:
        status_code = 200
        headers = {
            "content-encoding": "gzip",
            "content-type": "text/event-stream",
        }

        async def aiter_bytes(self):
            seen["iterated"] = True
            yield b"event: message_start\n"
            yield b"data: {}\n\n"

    class FakeRouterStream:
        async def __aenter__(self) -> FakeRouterStreamResponse:
            seen["entered"] = True
            return FakeRouterStreamResponse()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            seen["closed"] = True

    class FakeRouterClient:
        def __init__(self, **kwargs: object) -> None:
            seen["init"] = kwargs

        async def request(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("streaming messages must not use buffered request()")

        def stream(
            self,
            method: str,
            path: str,
            *,
            content: bytes,
            headers: list[tuple[str, str]],
        ) -> FakeRouterStream:
            seen["stream"] = {
                "method": method,
                "path": path,
                "content": content,
                "headers": headers,
            }
            return FakeRouterStream()

        async def aclose(self) -> None:
            seen["client_closed"] = True

    monkeypatch.setattr(dashboard_module.httpx, "AsyncClient", FakeRouterClient)
    app = create_dashboard_app(dist, router_base_url="http://router.local")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST",
            "/v1/messages",
            json={"model": "claude", "stream": True},
        ) as response:
            chunks = [chunk async for chunk in response.aiter_bytes()]

    upstream_request = cast(dict[str, object], seen["stream"])
    upstream_headers = cast(list[tuple[str, str]], upstream_request["headers"])
    request_headers = {key.lower(): value for key, value in upstream_headers}
    body = json.loads(cast(bytes, upstream_request["content"]))

    assert upstream_request["method"] == "POST"
    assert upstream_request["path"] == "/v1/messages"
    assert body["stream"] is True
    assert request_headers["accept-encoding"] == "identity"
    assert seen["entered"] is True
    assert seen["iterated"] is True
    assert seen["closed"] is True
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "content-encoding" not in response.headers
    assert b"".join(chunks) == b"event: message_start\ndata: {}\n\n"


@pytest.mark.asyncio
async def test_dashboard_returns_502_when_router_is_unavailable(tmp_path, httpx_mock):
    dist = _create_dist(tmp_path)
    httpx_mock.add_exception(
        httpx.ConnectError("connection refused"),
        method="GET",
        url="http://router.local/api/metrics/summary",
    )
    app = create_dashboard_app(dist, router_base_url="http://router.local")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/metrics/summary")

    assert response.status_code == 502
    assert "无法连接到 router API" in response.json()["detail"]

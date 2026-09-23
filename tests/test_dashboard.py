from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from routelet.app import create_app
from routelet.cli.config_io import load_startup_config
from routelet.dashboard import find_dashboard_dist, mount_dashboard
from routelet.db import CallStore


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


@asynccontextmanager
async def _client(tmp_path):
    path = tmp_path / "config.toml"
    config = load_startup_config(str(path), env_file="", no_env_file=True)
    app = create_app(
        config, CallStore(str(tmp_path / "calls.db")), config_path=str(path)
    )
    mount_dashboard(app, _create_dist(tmp_path))
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


def test_find_dashboard_dist_accepts_explicit_path(tmp_path):
    dist = _create_dist(tmp_path)
    assert find_dashboard_dist(dist) == dist.resolve()


def test_invalid_explicit_dist_does_not_fall_back(tmp_path, monkeypatch):
    _create_dist(tmp_path / "dashboard")
    monkeypatch.chdir(tmp_path)
    assert find_dashboard_dist(tmp_path / "missing") is None


async def test_same_app_serves_spa_assets_and_live_apis(tmp_path):
    async with _client(tmp_path) as client:
        for path in ["/", "/calls", "/config/providers", "/config/models"]:
            page = await client.get(path)
            assert page.status_code == 200
            assert "/assets/app.js" in page.text
        asset = await client.get("/assets/app.js")
        assert "console.log" in asset.text
        assert (await client.head("/assets/app.js")).status_code == 200
        assert (await client.get("/health")).status_code == 200
        summary = await client.get("/api/metrics/summary")
        assert summary.status_code == 200
        assert summary.json()["total_calls"] == 0
        assert (await client.get("/api/config")).status_code == 200
        assert (await client.get("/v1/models")).status_code == 200
        assert (await client.get("/openapi.json")).status_code == 200


async def test_unknown_api_and_missing_assets_do_not_return_spa(tmp_path):
    async with _client(tmp_path) as client:
        for path in [
            "/api",
            "/api/missing",
            "/v1",
            "/v1/missing",
            "/assets/missing.js",
            "/assets/missing",
            "/missing.css",
        ]:
            response = await client.get(path)
            assert response.status_code == 404, path
            assert "<html>" not in response.text
        response = await client.post(
            "/v1/messages", json={"model": "missing", "messages": []}
        )
        assert response.status_code == 400
        assert "error" in response.json()


async def test_dashboard_can_save_first_provider_and_model(tmp_path):
    async with _client(tmp_path) as client:
        response = await client.put(
            "/api/config",
            json={
                "providers": {
                    "test": {
                        "type": "anthropic",
                        "api_key": "test-secret",
                        "base_url": "https://example.test",
                        "models": {"real": {}},
                    }
                },
                "models": {
                    "virtual": {
                        "models": [{"provider": "test", "model": "real"}],
                        "pinned_model": {"provider": "test", "model": "real"},
                    }
                },
            },
        )
        assert response.status_code == 200, response.text
        models = await client.get("/v1/models")
        assert models.json()["data"][0]["id"] == "virtual"
        assert "test-secret" not in (await client.get("/api/config")).text
    assert '"real"' in (tmp_path / "config.toml").read_text(encoding="utf-8")


async def test_static_requests_cannot_read_files_outside_dist(tmp_path):
    (tmp_path / "secret.txt").write_text("outside-secret", encoding="utf-8")
    async with _client(tmp_path) as client:
        for path in ["/%2e%2e/secret.txt", "/..%5csecret.txt", "/%2e%2e%2fsecret.txt"]:
            response = await client.get(path)
            assert response.status_code == 404
            assert "outside-secret" not in response.text


def test_mount_rejects_missing_index(tmp_path):
    from fastapi import FastAPI

    with pytest.raises(ValueError, match="index.html"):
        mount_dashboard(FastAPI(), tmp_path)

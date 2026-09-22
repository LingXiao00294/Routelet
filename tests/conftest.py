from __future__ import annotations

import httpx
import pytest

from routelet.config import (
    AppConfig,
    ProviderConfig,
    RouterConfig,
    ServerConfig,
    VirtualModelConfig,
)


def _vm(*providers: ProviderConfig) -> VirtualModelConfig:
    return VirtualModelConfig(providers=list(providers))


@pytest.fixture
def sample_config() -> AppConfig:
    return AppConfig(
        server=ServerConfig(host="127.0.0.1", port=9456),
        router=RouterConfig(mode="failover"),
        models={
            "haiku-router": _vm(
                ProviderConfig(
                    type="anthropic",
                    name="anthropic",
                    model="claude-haiku-4-5-20251001",
                    api_key="test-key-1",
                    base_url="https://api.anthropic.com",
                    priority=1,
                ),
                ProviderConfig(
                    type="anthropic",
                    name="zhipu",
                    model="glm-5.1",
                    api_key="test-key-2",
                    base_url="https://api.z.ai/api/anthropic",
                    priority=2,
                ),
            ),
            "sonnet-router": _vm(
                ProviderConfig(
                    type="anthropic",
                    name="anthropic",
                    model="claude-sonnet-4-5-20250929",
                    api_key="test-key-3",
                    base_url="https://api.anthropic.com",
                    priority=1,
                ),
            ),
        },
    )


@pytest.fixture
async def http_client():
    async with httpx.AsyncClient() as client:
        yield client

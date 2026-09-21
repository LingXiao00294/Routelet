from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from agent_router.db import CallStore


def create_metrics_router(store: CallStore) -> APIRouter:
    router = APIRouter(tags=["metrics"])

    @router.get("/api/metrics/summary")
    async def summary():
        return await store.summary()

    @router.get("/api/metrics/by-model")
    async def by_model():
        return await store.by_model()

    @router.get("/api/metrics/by-real-model")
    async def by_real_model():
        return await store.by_real_model()

    @router.get("/api/metrics/by-provider")
    async def by_provider():
        return await store.by_provider()

    @router.get("/api/metrics/daily")
    async def daily_trend(days: int = Query(default=30, ge=1, le=365)):
        return await store.daily_trend(days)

    @router.get("/api/calls")
    async def list_calls(
        page: int = Query(default=1, ge=1),
        size: int = Query(default=50, ge=1, le=200),
        model: str | None = None,
        status: str | None = None,
        provider: str | None = None,
        provider_model: str | None = None,
    ):
        """Return one page of call summaries without persisted detail bodies."""
        calls, total = await store.list_calls(
            page=page,
            size=size,
            model=model,
            status=status,
            provider=provider,
            provider_model=provider_model,
        )
        return {
            "data": calls,
            "total": total,
            "page": page,
            "size": size,
            "pages": max(1, (total + size - 1) // size),
        }

    @router.get("/api/calls/{call_id}")
    async def get_call(call_id: str):
        """Return the complete persisted detail for one call."""
        call = await store.get_call(call_id)
        if call is None:
            raise HTTPException(status_code=404, detail="call not found")
        return call

    return router

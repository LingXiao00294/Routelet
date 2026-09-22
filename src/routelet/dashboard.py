from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


def find_dashboard_dist(explicit_path: str | Path | None = None) -> Path | None:
    """Return the first usable dashboard dist directory."""
    if explicit_path is not None:
        candidate = Path(explicit_path)
        return candidate.resolve() if (candidate / "index.html").is_file() else None
    package_root = Path(__file__).resolve().parent
    candidates = [
        package_root / "dashboard_dist",
        package_root.parent.parent / "dashboard" / "dist",
        Path.cwd() / "dashboard" / "dist",
    ]
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate.resolve()
    return None


class DashboardFiles(StaticFiles):
    """Serve SPA history routes without masking API or missing asset errors."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        first = path.replace("\\", "/").split("/", 1)[0]
        if first in {"api", "v1", "health", "docs", "redoc", "openapi.json"}:
            raise HTTPException(404)
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or first == "assets" or Path(path).suffix:
                raise
            return await super().get_response("index.html", scope)


def mount_dashboard(app: FastAPI, dist_dir: str | Path) -> None:
    """Mount last, so existing API routes keep their behavior and lifespan."""
    dist = Path(dist_dir).resolve()
    if not (dist / "index.html").is_file():
        raise ValueError(f"Dashboard dist 不完整，缺少 index.html: {dist}")
    app.mount("/", DashboardFiles(directory=dist, html=True), name="dashboard")

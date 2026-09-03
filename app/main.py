from __future__ import annotations

from pathlib import Path
import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .api import router as api_router
from .store import SandboxStore


def create_app() -> FastAPI:
    app = FastAPI(
        title="Bifurcate",
        description="Purpose-to-consumption resource lineage control plane for CubeSandbox agents.",
        version="0.3.0",
    )
    app.state.store = SandboxStore(os.getenv("BIFURCATE_STATE_PATH"))
    write_token = os.getenv("BIFURCATE_WRITE_TOKEN")
    app.include_router(api_router)

    @app.middleware("http")
    async def persist_mutations(request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and write_token:
            if request.headers.get("authorization") != f"Bearer {write_token}":
                return JSONResponse({"detail": "write authorization required"}, status_code=401)
        response = await call_next(request)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and response.status_code < 400:
            app.state.store.persist()
        return response

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/console", StaticFiles(directory=static_dir, html=True), name="console")

    @app.get("/", include_in_schema=False)
    def root_redirect():
        return RedirectResponse("/console/")

    @app.get("/console", include_in_schema=False)
    def console_no_slash():
        return FileResponse(static_dir / "index.html")

    @app.get("/trace", include_in_schema=False)
    def trace_console():
        return FileResponse(static_dir / "trace.html")

    @app.get("/healthz")
    def healthz():
        return {
            "status": "ok",
            "app_name": "bifurcate",
            "sandbox_backend": "claim",
            "sandbox_claim_class": "sandbox.microvm.standard",
            "model_gateway": "litellm",
            "model_protocol": "openai-compatible/v1",
            "public_access": "read-only",
        }

    return app


app = create_app()

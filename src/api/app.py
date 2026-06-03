"""FastAPI application factory for nanoCursor.

Creates and configures the FastAPI app with CORS, middleware, exception handlers,
and modular route includes. ``src.api.server`` is the public ASGI entrypoint;
``api_server.py`` remains as a legacy compatibility module during migration.
"""

from __future__ import annotations

import os
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from fastapi.exceptions import RequestValidationError

from src.api.errors import ApiError, http_status_to_code


class ErrorResponse(BaseModel):
    """Unified error response shape."""
    code: str
    message: str
    hint: str = ""
    details: dict | None = None
    request_id: str


def _error_body(code: str, message: str, request_id: str, hint: str = "", details: dict | None = None) -> dict:
    return {"error": ErrorResponse(
        code=code,
        message=message,
        hint=hint,
        details=details,
        request_id=request_id,
    ).model_dump()}


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application."""
    app = FastAPI(
        title="nanoCursor API",
        description="nanoCursor 智能体框架的后端 API 服务",
        version="2.0.0",
    )

    # ---- CORS ----
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- Request ID middleware ----
    @app.middleware("http")
    async def add_request_id(request, call_next):
        req_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = req_id
        response = await call_next(request)
        response.headers["x-request-id"] = req_id
        return response

    # ---- Exception handlers ----
    @app.exception_handler(ApiError)
    async def api_error_handler(request, exc: ApiError):
        req_id = getattr(request.state, "request_id", "unknown")
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(
                code=exc.code.value,
                message=exc.message,
                hint=exc.hint,
                details=exc.details,
                request_id=req_id,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request, exc: RequestValidationError):
        req_id = getattr(request.state, "request_id", "unknown")
        errors = exc.errors()
        detail = errors[0] if errors else {}
        field = detail.get("loc", ["body"])[-1] if detail.get("loc") else "body"
        msg = detail.get("msg", "Validation error")
        return JSONResponse(
            status_code=400,
            content=_error_body(
                code="invalid_request",
                message=f"{field}: {msg}",
                hint="请按 API 文档修正请求体字段类型或必填项。",
                details={"validation_errors": errors},
                request_id=req_id,
            ),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request, exc: HTTPException):
        req_id = getattr(request.state, "request_id", "unknown")
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(
                code=http_status_to_code(exc.status_code),
                message=str(exc.detail),
                request_id=req_id,
            ),
        )

    # ---- Slow request logging ----
    @app.middleware("http")
    async def slow_request_middleware(request, call_next):
        import time
        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = round((time.monotonic() - start) * 1000)
        if elapsed_ms > 5000:
            from src.infra.logging import get_logger
            logger = get_logger()
            logger.error(
                f"慢请求: {request.method} {request.url.path}",
                extra={
                    "request_id": getattr(request.state, "request_id", ""),
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": elapsed_ms,
                },
            )
        elif elapsed_ms > 1000:
            from src.infra.logging import get_logger
            logger = get_logger()
            logger.warning(
                f"慢请求: {request.method} {request.url.path}",
                extra={
                    "request_id": getattr(request.state, "request_id", ""),
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": elapsed_ms,
                },
            )
        return response

    # ---- Health / Ready / Version ----
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def ready():
        try:
            from src.agent.engine import MODEL
            return {"status": "ready", "llm": "available", "model": MODEL}
        except Exception as exc:
            return {"status": "degraded", "llm": "unavailable", "error": str(exc)}

    @app.get("/version")
    async def version():
        import subprocess
        commit_sha = os.getenv("COMMIT_SHA", "")
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                commit_sha = result.stdout.strip()
        except Exception:
            pass
        return {"version": "2.1.0", "commit": commit_sha or "dev"}

    # ---- Modular routers ----
    from src.api.routes.evals import router as evals_router
    from src.api.routes.runs import (
        legacy_runtime_router as legacy_run_runtime_router,
        router as runs_router,
        runtime_router as run_runtime_router,
    )
    from src.api.routes.system import router as system_router
    from src.api.routes.workspaces import router as workspaces_router
    from src.api.routes.data import router as data_router
    from src.api.routes.config import router as config_router
    from src.api.routes.conversations import router as conversations_router
    from src.api.routes.capabilities import router as capabilities_router
    from src.api.routes.run_analytics import router as run_analytics_router
    from src.api.routes.recovery import router as recovery_router
    from src.api.routes.approvals import router as approvals_router
    from src.api.routes.benchmarks import router as benchmarks_router
    from src.api.routes.demo_runs import router as demo_runs_router
    from src.api.routes.run_entry import router as run_entry_router

    app.include_router(system_router)
    app.include_router(evals_router)
    app.include_router(run_entry_router)
    app.include_router(run_runtime_router)
    app.include_router(legacy_run_runtime_router)
    app.include_router(runs_router)
    app.include_router(benchmarks_router)
    app.include_router(demo_runs_router)
    app.include_router(workspaces_router)
    app.include_router(data_router)
    app.include_router(config_router)
    app.include_router(conversations_router)
    app.include_router(capabilities_router)
    app.include_router(run_analytics_router)
    app.include_router(recovery_router)
    app.include_router(approvals_router)

    return app

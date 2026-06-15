"""FastAPI 应用入口。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic_core import ValidationError as PydanticCoreValidationError
from starlette.responses import Response

from app.api.auth import router as auth_router
from app.api.supervisor import router as supervisor_router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import configure_logging
from app.core.response import fail
from app.database.bootstrap import close_database, init_database


LOGGER = logging.getLogger(__name__)

API_PREFIX = "/admin/api"
CORS_ALLOW_METHODS = "GET,POST,PUT,DELETE,OPTIONS"
CORS_ALLOW_HEADERS = "Authorization, Content-Type, Accept, X-Requested-With, Cache-Control, Pragma"
CORS_EXPOSE_HEADERS = "Authorization"


def _log_validation_failure(request: Request, exc: Exception, *, label: str) -> None:
    """统一记录参数校验失败上下文，便于定位前端实际发包与契约不一致的问题。"""
    errors = exc.errors() if hasattr(exc, "errors") else str(exc)
    LOGGER.debug(
        "%s: method=%s url=%s content_type=%s errors=%s",
        label,
        request.method,
        str(request.url),
        request.headers.get("content-type"),
        errors,
    )


def _apply_cors_headers(request: Request, response: Response) -> Response:
    """为管理 API 统一补齐跨域响应头。"""
    origin = request.headers.get("origin")
    response.headers["Access-Control-Allow-Origin"] = origin or "*"
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = CORS_ALLOW_METHODS
    response.headers["Access-Control-Max-Age"] = "3600"
    response.headers["Access-Control-Allow-Headers"] = CORS_ALLOW_HEADERS
    response.headers["Access-Control-Expose-Headers"] = CORS_EXPOSE_HEADERS
    return response


def create_app() -> FastAPI:
    """创建并装配应用。"""
    settings = get_settings()
    configure_logging(settings.app.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await init_database(settings)
        try:
            yield
        finally:
            await close_database()

    app = FastAPI(
        title="be-supervisor-model",
        description="面向运维场景的 Supervisor 配置、登录鉴权与进程管理服务。",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def apply_api_cors(request: Request, call_next):
        if not request.url.path.startswith(API_PREFIX):
            return await call_next(request)

        # 前端的 Authorization 与 application/json 会触发浏览器预检，不能继续落到鉴权或业务路由。
        if request.method == "OPTIONS":
            return _apply_cors_headers(request, Response(status_code=200))

        response = await call_next(request)
        # 对齐 be-vita：有 Origin 时原样回写，没有 Origin 时回 *，保持前端跨源错误响应可读。
        return _apply_cors_headers(request, response)

    app.include_router(auth_router)
    app.include_router(supervisor_router)

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError):
        return fail(exc.http_status, exc.code, exc.msg, exc.data)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        _log_validation_failure(request, exc, label="request validation failed")
        return fail(400, 40000, "请求参数非法")

    @app.exception_handler(PydanticCoreValidationError)
    async def handle_pydantic_core_validation_error(request: Request, exc: PydanticCoreValidationError):
        _log_validation_failure(request, exc, label="pydantic-core validation failed")
        return fail(400, 40000, "请求参数非法")

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception):
        LOGGER.exception("unexpected error", exc_info=exc)
        return fail(500, 50000, "服务内部错误")

    return app


app = create_app()

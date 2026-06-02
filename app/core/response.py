"""统一响应封装。"""
from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def ok(data: Any = None, msg: str = "success") -> JSONResponse:
    """返回成功响应。"""
    return JSONResponse(status_code=200, content={"code": 200, "msg": msg, "data": data})


def fail(http_status: int, code: int, msg: str, data: Any = None) -> JSONResponse:
    """返回失败响应。"""
    return JSONResponse(status_code=http_status, content={"code": code, "msg": msg, "data": data})

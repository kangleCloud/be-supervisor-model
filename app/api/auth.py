"""登录认证 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.config import Settings, get_settings
from app.core.response import ok
from app.core.security import verify_jwt_dependency
from app.schemas.auth import LoginRequest
from app.services.auth_service import AuthService, AuthenticatedUser


router = APIRouter(
    prefix="/admin/api/auth",
    tags=["登录认证"],
)


def get_auth_service(settings: Settings = Depends(get_settings)) -> AuthService:
    """构造认证服务。"""
    return AuthService(settings)


@router.post(
    "/login",
    summary="账号登录",
    description="校验用户名密码后签发 JWT，并将当前登录令牌写入数据库。",
    response_description="登录结果，包含 Bearer Token 与当前用户信息。",
)
async def login(payload: LoginRequest, request: Request, auth_service: AuthService = Depends(get_auth_service)):
    client_ip = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")
    return ok(await auth_service.login(payload.username, payload.password, client_ip, user_agent), msg="登录成功")


@router.get(
    "/profile",
    summary="查询当前登录用户资料",
    description="根据 Bearer JWT 与服务端令牌表返回当前登录用户的资料信息。",
    response_description="当前登录用户资料。",
)
async def get_profile(current_user: AuthenticatedUser = Depends(verify_jwt_dependency)):
    return ok(current_user.to_auth_profile(), msg="查询当前用户资料成功")


@router.post(
    "/logout",
    summary="退出登录",
    description="注销当前 JWT 令牌，使后续同一 Token 请求立即失效。",
    response_description="退出结果。",
)
async def logout(
    current_user: AuthenticatedUser = Depends(verify_jwt_dependency),
    auth_service: AuthService = Depends(get_auth_service),
):
    await auth_service.logout(current_user)
    return ok(msg="退出登录成功")

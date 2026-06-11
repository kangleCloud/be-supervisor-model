"""登录认证服务。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import jwt
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.exceptions import ParamError, UnauthorizedError
from app.core.jwt_service import create_access_token, decode_access_token
from app.core.passwords import verify_password
from app.database.repositories.auth import LoginLogRepository
from app.services.token_service import TokenService, to_db_datetime
from app.services.user_service import UserRecord, UserService


@dataclass(frozen=True)
class AuthenticatedUser:
    """当前登录态。"""

    user_id: int
    username: str
    display_name: str
    roles: tuple[str, ...]
    permissions: tuple[str, ...]
    token_id: int
    token_jti: str

    def to_auth_profile(self) -> dict[str, object]:
        return {
            "username": self.username,
            "displayName": self.display_name,
            "roles": list(self.roles),
            "permissions": list(self.permissions),
        }


class AuthService:
    """统一处理登录、鉴权、注销与登录审计。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.user_service = UserService(settings)
        self.token_service = TokenService(settings)
        self.login_log_repository = LoginLogRepository()

    async def login(self, username: str, password: str, client_ip: str, user_agent: str) -> dict[str, object]:
        """校验账号密码，签发 JWT，并写入令牌与审计日志。"""
        normalized_username = (username or "").strip()
        normalized_password = (password or "").strip()
        if not normalized_username or not normalized_password:
            raise ParamError("用户名和密码不能为空")

        user = await self.user_service.get_by_username(normalized_username)
        password_valid = bool(user) and await run_in_threadpool(verify_password, normalized_password, user.password)
        if user is None or not password_valid:
            await self._write_login_log(
                user_id=user.id if user else None,
                token_id=None,
                user_name=normalized_username,
                ip_address=client_ip,
                user_agent=user_agent,
                success=False,
                message="用户名或密码错误",
                token_jti=None,
            )
            raise UnauthorizedError("用户名或密码错误")

        if user.status != 1:
            await self._write_login_log(
                user_id=user.id,
                token_id=None,
                user_name=user.user_name,
                ip_address=client_ip,
                user_agent=user_agent,
                success=False,
                message="账号已禁用",
                token_jti=None,
            )
            raise UnauthorizedError("账号已禁用")

        token, issued_at, expires_at, token_jti = create_access_token(
            user.id,
            user.user_name,
            self.settings.auth.jwt_secret,
            self.settings.auth.access_token_expire_minutes,
        )
        token_id = await self.token_service.create_token(
            user_id=user.id,
            user_name=user.user_name,
            token=token,
            token_jti=token_jti,
            login_ip=client_ip,
            user_agent=user_agent,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        await self.user_service.update_login_info(user.id, user.user_name, to_db_datetime(issued_at), client_ip)
        await self._write_login_log(
            user_id=user.id,
            token_id=token_id,
            user_name=user.user_name,
            ip_address=client_ip,
            user_agent=user_agent,
            success=True,
            message="登录成功",
            token_jti=token_jti,
        )
        return {
            "accessToken": token,
            "tokenType": "Bearer",
            "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
            "user": user.to_auth_profile(),
        }

    async def authenticate_access_token(self, token: str) -> AuthenticatedUser:
        """校验 JWT 与令牌表状态，并返回当前用户上下文。"""
        if not token:
            raise UnauthorizedError("缺少登录凭证")

        try:
            payload = decode_access_token(token, self.settings.auth.jwt_secret)
        except jwt.ExpiredSignatureError as exc:
            raise UnauthorizedError("登录状态已过期") from exc
        except jwt.PyJWTError as exc:
            raise UnauthorizedError("登录凭证无效") from exc

        try:
            user_id = int(payload["sub"])
            username = str(payload["username"])
            token_jti = str(payload["jti"])
        except (KeyError, TypeError, ValueError) as exc:
            raise UnauthorizedError("登录凭证无效") from exc

        token_record = await self.token_service.get_active_token(user_id, token_jti)
        if token_record is None:
            raise UnauthorizedError("登录令牌已失效")
        if token_record.token_digest != self.token_service.build_token_digest(token):
            raise UnauthorizedError("登录凭证无效")

        user = await self.user_service.get_by_id(user_id)
        if user is None:
            raise UnauthorizedError("用户不存在")
        if user.status != 1:
            raise UnauthorizedError("账号已禁用")
        return self._build_authenticated_user(user, token_record.id, username=user.user_name, token_jti=token_jti)

    async def logout(self, current_user: AuthenticatedUser) -> None:
        """注销当前令牌。"""
        await self.token_service.revoke_token(current_user.token_id, current_user.user_id, current_user.username)

    async def _write_login_log(
        self,
        user_id: int | None,
        token_id: int | None,
        user_name: str,
        ip_address: str,
        user_agent: str,
        success: bool,
        message: str,
        token_jti: str | None,
    ) -> None:
        browser_name, os_name = parse_user_agent(user_agent)
        await self.login_log_repository.create_log(
            user_id=user_id,
            token_id=token_id,
            user_name=user_name,
            ip_address=ip_address,
            browser=browser_name,
            os_name=os_name,
            success=success,
            message=message,
            token_jti=token_jti,
            login_time=to_db_datetime_from_now(),
        )

    @staticmethod
    def _build_authenticated_user(user: UserRecord, token_id: int, username: str, token_jti: str) -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=user.id,
            username=username,
            display_name=user.display_name,
            roles=tuple(user.roles),
            permissions=tuple(user.permissions),
            token_id=token_id,
            token_jti=token_jti,
        )


def parse_user_agent(user_agent: str) -> tuple[str, str]:
    """内网工具只做轻量 UA 归类，不引入额外解析依赖。"""
    normalized = (user_agent or "").lower()

    if "edg/" in normalized:
        browser_name = "Edge"
    elif "chrome/" in normalized:
        browser_name = "Chrome"
    elif "firefox/" in normalized:
        browser_name = "Firefox"
    elif "safari/" in normalized and "chrome/" not in normalized:
        browser_name = "Safari"
    else:
        browser_name = "Unknown"

    if "windows" in normalized:
        os_name = "Windows"
    elif "mac os" in normalized or "macintosh" in normalized:
        os_name = "macOS"
    elif "linux" in normalized:
        os_name = "Linux"
    else:
        os_name = "Unknown"

    return browser_name, os_name


def to_db_datetime_from_now() -> datetime:
    """记录登录审计时统一取当前 UTC 时间。"""
    return to_db_datetime(datetime.now(timezone.utc))

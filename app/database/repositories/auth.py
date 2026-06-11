"""认证相关仓储。"""
from __future__ import annotations

from datetime import datetime

from tortoise.expressions import F

from app.database.models.auth import LoginLogModel, LoginTokenModel, UserModel


class UserRepository:
    """用户表访问。"""

    async def find_by_username(self, username: str) -> UserModel | None:
        return await UserModel.filter(user_name=username, is_deleted=0).first()

    async def find_by_id(self, user_id: int) -> UserModel | None:
        return await UserModel.filter(id=user_id, is_deleted=0).first()

    async def update_login_info(self, user_id: int, username: str, login_time: datetime, login_address: str) -> None:
        await UserModel.filter(id=user_id, is_deleted=0).update(
            login_time=login_time,
            login_address=login_address,
            update_by_id=user_id,
            update_by=username,
            version=F("version") + 1,
        )


class TokenRepository:
    """登录令牌表访问。"""

    async def create_token(
        self,
        *,
        user_id: int,
        user_name: str,
        token_jti: str,
        token_digest: str,
        login_ip: str,
        user_agent: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> int:
        record = await LoginTokenModel.create(
            user_id=user_id,
            user_name=user_name,
            token_jti=token_jti,
            token_digest=token_digest,
            login_ip=login_ip,
            user_agent=user_agent,
            issued_at=issued_at,
            expires_at=expires_at,
            create_by_id=user_id,
            create_by=user_name,
            update_by_id=user_id,
            update_by=user_name,
            remark="JWT登录令牌",
        )
        return int(record.id)

    async def find_active_token(self, user_id: int, token_jti: str) -> LoginTokenModel | None:
        return await LoginTokenModel.filter(
            user_id=user_id,
            token_jti=token_jti,
            is_deleted=0,
            revoked_time=None,
        ).first()

    async def revoke_token(self, token_id: int, user_id: int, username: str, revoked_time: datetime) -> None:
        await LoginTokenModel.filter(id=token_id, revoked_time=None).update(
            revoked_time=revoked_time,
            update_by_id=user_id,
            update_by=username,
            version=F("version") + 1,
        )


class LoginLogRepository:
    """登录日志表访问。"""

    async def create_log(
        self,
        *,
        user_id: int | None,
        token_id: int | None,
        user_name: str,
        ip_address: str,
        browser: str,
        os_name: str,
        success: bool,
        message: str,
        token_jti: str | None,
        login_time: datetime,
    ) -> int:
        record = await LoginLogModel.create(
            user_id=user_id,
            token_id=token_id,
            user_name=user_name,
            ipaddr=ip_address,
            login_location="内网",
            browser=browser,
            os=os_name,
            status="0" if success else "1",
            msg=message,
            token_jti=token_jti,
            login_time=login_time,
            create_by_id=user_id,
            create_by=user_name,
            update_by_id=user_id,
            update_by=user_name,
            remark="登录审计记录",
        )
        return int(record.id)

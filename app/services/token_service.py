"""登录令牌服务。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.config import Settings
from app.database.repositories.auth import TokenRepository


@dataclass(frozen=True)
class LoginTokenRecord:
    """活动登录令牌。"""

    id: int
    user_id: int
    user_name: str
    token_jti: str
    token_digest: str
    revoked_time: datetime | None


class TokenService:
    """JWT 令牌写库与注销服务。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.repository = TokenRepository()

    async def create_token(
        self,
        user_id: int,
        user_name: str,
        token: str,
        token_jti: str,
        login_ip: str,
        user_agent: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> int:
        """写入有效登录令牌。"""
        token_digest = self.build_token_digest(token)
        return await self.repository.create_token(
            user_id=user_id,
            user_name=user_name,
            token_jti=token_jti,
            token_digest=token_digest,
            login_ip=login_ip,
            user_agent=user_agent[:500],
            issued_at=to_db_datetime(issued_at),
            expires_at=to_db_datetime(expires_at),
        )

    async def get_active_token(self, user_id: int, token_jti: str) -> LoginTokenRecord | None:
        """按 user_id 与 jti 查询未注销令牌。"""
        row = await self.repository.find_active_token(user_id, token_jti)
        if row is None:
            return None
        return LoginTokenRecord(
            id=int(row.id),
            user_id=int(row.user_id),
            user_name=str(row.user_name),
            token_jti=str(row.token_jti),
            token_digest=str(row.token_digest),
            revoked_time=coerce_datetime(row.revoked_time),
        )

    async def revoke_token(self, token_id: int, user_id: int, username: str) -> None:
        """注销指定令牌，保证后续请求立即失效。"""
        await self.repository.revoke_token(token_id, user_id, username, to_db_datetime(datetime.now(timezone.utc)))

    @staticmethod
    def build_token_digest(token: str) -> str:
        """对原始 token 做单向摘要，避免明文落库。"""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()


def to_db_datetime(value: datetime) -> datetime:
    """统一把 UTC 时间写成无时区 datetime，兼容 ORM DATETIME 字段。"""
    return value.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)


def coerce_datetime(value: object) -> datetime | None:
    """兼容数据库驱动返回 datetime 或字符串两种情况。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc)
        return value.replace(tzinfo=timezone.utc)
    parsed = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    return parsed.replace(tzinfo=timezone.utc)

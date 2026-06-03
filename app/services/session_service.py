"""登录会话服务。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.config import Settings
from app.core.database import get_connection


@dataclass(frozen=True)
class LoginSessionRecord:
    """活动登录会话。"""

    id: int
    user_id: int
    user_name: str
    token_jti: str
    token_digest: str
    revoked_time: datetime | None


class SessionService:
    """JWT 会话写库与注销服务。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    def create_session(
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
        """写入有效登录会话。"""
        token_digest = self.build_token_digest(token)
        with get_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO sys_login_session(
                        user_id, user_name, token_jti, token_digest, login_ip, user_agent,
                        issued_at, expires_at, create_by_id, create_by, update_by_id, update_by, remark
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        user_name,
                        token_jti,
                        token_digest,
                        login_ip,
                        user_agent[:500],
                        to_db_datetime(issued_at),
                        to_db_datetime(expires_at),
                        user_id,
                        user_name,
                        user_id,
                        user_name,
                        "JWT登录会话",
                    ),
                )
                session_id = int(cursor.lastrowid)
            connection.commit()
        return session_id

    def get_active_session(self, user_id: int, token_jti: str) -> LoginSessionRecord | None:
        """按 user_id 与 jti 查询未注销会话。"""
        with get_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, user_id, user_name, token_jti, token_digest, revoked_time
                    FROM sys_login_session
                    WHERE user_id = %s
                      AND token_jti = %s
                      AND is_deleted = 0
                      AND revoked_time IS NULL
                    LIMIT 1
                    """,
                    (user_id, token_jti),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return LoginSessionRecord(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            user_name=str(row["user_name"]),
            token_jti=str(row["token_jti"]),
            token_digest=str(row["token_digest"]),
            revoked_time=coerce_datetime(row.get("revoked_time")),
        )

    def revoke_session(self, session_id: int, user_id: int, username: str) -> None:
        """注销指定会话，保证后续请求立即失效。"""
        revoked_time = to_db_datetime(datetime.now(timezone.utc))
        with get_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE sys_login_session
                    SET revoked_time = %s,
                        update_time = CURRENT_TIMESTAMP,
                        update_by_id = %s,
                        update_by = %s,
                        version = version + 1
                    WHERE id = %s AND revoked_time IS NULL
                    """,
                    (revoked_time, user_id, username, session_id),
                )
            connection.commit()

    @staticmethod
    def build_token_digest(token: str) -> str:
        """对原始 token 做单向摘要，避免明文落库。"""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()


def to_db_datetime(value: datetime) -> str:
    """统一把 UTC 时间写成 MySQL DATETIME。"""
    utc_value = value.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)
    return utc_value.strftime("%Y-%m-%d %H:%M:%S")


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

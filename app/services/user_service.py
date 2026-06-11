"""用户查询服务。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.core.config import Settings
from app.database.repositories.auth import UserRepository


@dataclass(frozen=True)
class UserRecord:
    """登录用户的最小业务视图。"""

    id: int
    user_name: str
    nick_name: str | None
    password: str
    status: int
    is_super_admin: int

    @property
    def display_name(self) -> str:
        return (self.nick_name or "").strip() or self.user_name

    @property
    def roles(self) -> list[str]:
        if self.is_super_admin:
            return ["SUPER_ADMIN"]
        return ["OPERATOR"]

    @property
    def permissions(self) -> list[str]:
        return ["supervisor:manage"]

    def to_auth_profile(self) -> dict[str, object]:
        """转换成前端约定的登录用户结构。"""
        return {
            "username": self.user_name,
            "displayName": self.display_name,
            "roles": self.roles,
            "permissions": self.permissions,
        }


class UserService:
    """用户读取与登录信息更新服务。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.repository = UserRepository()

    async def get_by_username(self, username: str) -> UserRecord | None:
        """按用户名读取未删除用户。"""
        return self._build_user(await self.repository.find_by_username(username))

    async def get_by_id(self, user_id: int) -> UserRecord | None:
        """按用户 ID 读取未删除用户。"""
        return self._build_user(await self.repository.find_by_id(user_id))

    async def update_login_info(self, user_id: int, username: str, login_time: datetime, login_address: str) -> None:
        """记录最近一次成功登录时间与地址。"""
        await self.repository.update_login_info(user_id, username, login_time, login_address)

    @staticmethod
    def _build_user(row) -> UserRecord | None:
        if row is None:
            return None
        return UserRecord(
            id=int(row.id),
            user_name=str(row.user_name),
            nick_name=str(row.nick_name) if row.nick_name is not None else None,
            password=str(row.password),
            status=int(row.status),
            is_super_admin=int(row.is_super_admin),
        )

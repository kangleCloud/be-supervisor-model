"""认证请求模型。"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    """登录请求体。"""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., description="登录用户名")
    password: str = Field(..., description="登录密码")

    @field_validator("username", "password")
    @classmethod
    def validate_required_text(cls, value: str, info) -> str:
        raw_value = (value or "").strip()
        if not raw_value:
            raise ValueError(f"{info.field_name} 不能为空")
        return raw_value

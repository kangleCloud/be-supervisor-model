"""JWT 生成与解析。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt


def create_access_token(
    user_id: int,
    username: str,
    jwt_secret: str,
    expire_minutes: int,
) -> tuple[str, datetime, datetime, str]:
    """生成访问令牌，并返回 token、签发时间、过期时间和 jti。"""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=expire_minutes)
    token_jti = uuid4().hex
    payload = {
        "sub": str(user_id),
        "username": username,
        "jti": token_jti,
        "exp": expires_at,
    }
    token = jwt.encode(payload, jwt_secret, algorithm="HS256")
    return token, now, expires_at, token_jti


def decode_access_token(token: str, jwt_secret: str) -> dict[str, object]:
    """解析访问令牌。"""
    return jwt.decode(token, jwt_secret, algorithms=["HS256"])

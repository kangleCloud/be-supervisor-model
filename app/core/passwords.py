"""密码哈希工具。"""
from __future__ import annotations

import bcrypt


def hash_password(plain_password: str) -> str:
    """生成 bcrypt 密码哈希。"""
    raw_password = plain_password.encode("utf-8")
    return bcrypt.hashpw(raw_password, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验明文密码与 bcrypt 哈希是否匹配。"""
    if not hashed_password:
        return False
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))

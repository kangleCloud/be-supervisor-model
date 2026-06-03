"""生成 bcrypt 密码哈希，供运维手工建号时使用。"""
from __future__ import annotations

import getpass

from app.core.passwords import hash_password


def main() -> None:
    plain_password = getpass.getpass("请输入待加密密码: ").strip()
    if not plain_password:
        raise SystemExit("密码不能为空")
    print(hash_password(plain_password))


if __name__ == "__main__":
    main()

"""认证相关 ORM 模型。"""
from __future__ import annotations

from tortoise import fields
from tortoise.models import Model


class UserModel(Model):
    """sys_user。"""

    id = fields.BigIntField(pk=True, generated=False)
    tenant_id = fields.BigIntField(null=True)
    user_name = fields.CharField(max_length=50, unique=True)
    nick_name = fields.CharField(max_length=50, null=True)
    password = fields.CharField(max_length=255)
    status = fields.IntField(default=1)
    is_super_admin = fields.IntField(default=0)
    login_time = fields.DatetimeField(null=True)
    login_address = fields.CharField(max_length=128, null=True)
    pwd_update_date = fields.DatetimeField(null=True)
    create_time = fields.DatetimeField(auto_now_add=True)
    update_time = fields.DatetimeField(auto_now=True)
    is_deleted = fields.IntField(default=0)
    create_by_id = fields.BigIntField(null=True)
    create_by = fields.CharField(max_length=50, null=True)
    update_by_id = fields.BigIntField(null=True)
    update_by = fields.CharField(max_length=50, null=True)
    version = fields.BigIntField(default=0)
    remark = fields.CharField(max_length=500, null=True)

    class Meta:
        table = "sys_user"
        table_description = "用户信息表"
        indexes = (("user_name",),)


class LoginLogModel(Model):
    """sys_login_log。"""

    id = fields.BigIntField(pk=True)
    tenant_id = fields.BigIntField(null=True)
    user_id = fields.BigIntField(null=True)
    token_id = fields.BigIntField(null=True)
    user_name = fields.CharField(max_length=50, default="")
    ipaddr = fields.CharField(max_length=128, default="")
    login_location = fields.CharField(max_length=255, default="")
    browser = fields.CharField(max_length=50, default="")
    os = fields.CharField(max_length=50, default="")
    status = fields.CharField(max_length=1, default="0")
    msg = fields.CharField(max_length=255, default="")
    token_jti = fields.CharField(max_length=64, null=True)
    login_time = fields.DatetimeField(null=True)
    create_time = fields.DatetimeField(auto_now_add=True)
    update_time = fields.DatetimeField(auto_now=True)
    is_deleted = fields.IntField(default=0)
    create_by_id = fields.BigIntField(null=True)
    create_by = fields.CharField(max_length=50, null=True)
    update_by_id = fields.BigIntField(null=True)
    update_by = fields.CharField(max_length=50, null=True)
    version = fields.BigIntField(default=0)
    remark = fields.CharField(max_length=500, null=True)

    class Meta:
        table = "sys_login_log"
        table_description = "登录审计日志表"
        indexes = (("status",), ("login_time",), ("user_id",), ("token_id",))


class LoginTokenModel(Model):
    """sys_login_token。"""

    id = fields.BigIntField(pk=True)
    tenant_id = fields.BigIntField(null=True)
    user_id = fields.BigIntField()
    user_name = fields.CharField(max_length=50)
    token_jti = fields.CharField(max_length=64, unique=True)
    token_digest = fields.CharField(max_length=64)
    login_ip = fields.CharField(max_length=128, default="")
    user_agent = fields.CharField(max_length=500, default="")
    issued_at = fields.DatetimeField()
    expires_at = fields.DatetimeField()
    revoked_time = fields.DatetimeField(null=True)
    create_time = fields.DatetimeField(auto_now_add=True)
    update_time = fields.DatetimeField(auto_now=True)
    is_deleted = fields.IntField(default=0)
    create_by_id = fields.BigIntField(null=True)
    create_by = fields.CharField(max_length=50, null=True)
    update_by_id = fields.BigIntField(null=True)
    update_by = fields.CharField(max_length=50, null=True)
    version = fields.BigIntField(default=0)
    remark = fields.CharField(max_length=500, null=True)

    class Meta:
        table = "sys_login_token"
        table_description = "JWT登录令牌表"
        indexes = (("user_id",), ("expires_at",))

"""数据库初始化与鉴权落库测试。"""
from __future__ import annotations

from pathlib import Path

from app.core.database import initialize_database


def test_database_initialization_is_idempotent(settings, fake_mysql):
    initialize_database(settings)
    initialize_database(settings)

    assert settings.database.database in fake_mysql.databases
    assert "sys_user" in fake_mysql.tables
    assert "sys_login_log" in fake_mysql.tables
    assert "sys_login_token" in fake_mysql.tables
    assert len(fake_mysql.tables["sys_schema_migration"]) == 1
    assert fake_mysql.tables["sys_schema_migration"][0]["version"] == 1


def test_database_recreates_missing_auth_tables(settings, fake_mysql):
    """迁移版本已存在但关键表缺失时，启动阶段仍需自动补建认证基线表。"""
    initialize_database(settings)

    del fake_mysql.tables["sys_login_token"]

    initialize_database(settings)

    assert "sys_login_token" in fake_mysql.tables
    assert len(fake_mysql.tables["sys_schema_migration"]) == 1


def test_login_persists_token_and_log(client, seed_user, fake_mysql):
    seed_user()

    response = client.post(
        "/admin/api/auth/login",
        json={
            "username": "ops",
            "password": "secret",
        },
    )

    assert response.status_code == 200
    assert len(fake_mysql.tables["sys_login_token"]) == 1
    assert fake_mysql.tables["sys_login_token"][0]["token_digest"]
    assert len(fake_mysql.tables["sys_login_log"]) == 1
    assert fake_mysql.tables["sys_login_log"][0]["token_id"] == fake_mysql.tables["sys_login_token"][0]["id"]
    assert fake_mysql.tables["sys_login_log"][0]["token_jti"] == fake_mysql.tables["sys_login_token"][0]["token_jti"]


def test_auth_migration_sql_contains_token_comments():
    """认证基线 SQL 需要显式声明令牌表和关键中文字段注释。"""
    migration_path = Path(__file__).resolve().parents[1] / "app" / "database" / "migrations" / "001_init_auth_tables.sql"
    migration_sql = migration_path.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS `sys_login_token`" in migration_sql
    assert "`token_id` BIGINT DEFAULT NULL COMMENT '登录令牌ID'" in migration_sql
    assert "`user_name` VARCHAR(50) NOT NULL COMMENT '用户名'" in migration_sql
    assert "COMMENT='JWT登录令牌表'" in migration_sql

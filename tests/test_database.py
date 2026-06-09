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
    assert "sys_supervisor_service" in fake_mysql.tables
    assert [row["version"] for row in fake_mysql.tables["sys_schema_migration"]] == [1]


def test_database_bootstraps_single_super_admin(settings, fake_mysql):
    initialize_database(settings)
    initialize_database(settings)

    admin_users = [row for row in fake_mysql.tables["sys_user"] if row["user_name"] == "admin" and row["is_deleted"] == 0]

    assert len(admin_users) == 1
    assert admin_users[0]["is_super_admin"] == 1
    assert admin_users[0]["status"] == 1


def test_database_recreates_missing_auth_tables(settings, fake_mysql):
    """单基线初始化后，关键认证表缺失时仍需自动补建。"""
    initialize_database(settings)

    del fake_mysql.tables["sys_login_token"]

    initialize_database(settings)

    assert "sys_login_token" in fake_mysql.tables
    assert [row["version"] for row in fake_mysql.tables["sys_schema_migration"]] == [1]


def test_database_recreates_missing_supervisor_table(settings, fake_mysql):
    """Supervisor 主数据表缺失时，启动阶段也应通过单基线补建。"""
    initialize_database(settings)

    del fake_mysql.tables["sys_supervisor_service"]

    initialize_database(settings)

    assert "sys_supervisor_service" in fake_mysql.tables
    assert [row["version"] for row in fake_mysql.tables["sys_schema_migration"]] == [1]


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


def test_init_schema_sql_contains_auth_tables_and_admin_seed():
    """单基线 SQL 需要同时包含认证表和默认超级管理员初始化语句。"""
    migration_path = Path(__file__).resolve().parents[1] / "app" / "database" / "migrations" / "001_init_schema.sql"
    migration_sql = migration_path.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS `sys_user`" in migration_sql
    assert "CREATE TABLE IF NOT EXISTS `sys_login_log`" in migration_sql
    assert "CREATE TABLE IF NOT EXISTS `sys_login_token`" in migration_sql
    assert "`token_id` BIGINT DEFAULT NULL COMMENT '登录令牌ID'" in migration_sql
    assert "`user_name` VARCHAR(50) NOT NULL COMMENT '用户名'" in migration_sql
    assert "COMMENT='JWT登录令牌表'" in migration_sql
    assert "INSERT INTO `sys_user`(" in migration_sql
    assert "'admin'" in migration_sql
    assert "'超级管理员'" in migration_sql
    assert "'系统初始化超级管理员，请尽快重置默认密码'" in migration_sql


def test_init_schema_sql_contains_final_supervisor_service_schema():
    """单基线 SQL 需要直接声明 Supervisor 主数据表最终结构。"""
    migration_path = Path(__file__).resolve().parents[1] / "app" / "database" / "migrations" / "001_init_schema.sql"
    migration_sql = migration_path.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS `sys_supervisor_service`" in migration_sql
    assert "`config_path` VARCHAR(500) NOT NULL" in migration_sql
    assert "`file_name` VARCHAR(255) NOT NULL" in migration_sql
    assert "`content_program_name` VARCHAR(255) NOT NULL" in migration_sql
    assert "`manage_mode` VARCHAR(32) NOT NULL DEFAULT 'TEMPLATE_MANAGED'" in migration_sql
    assert "`baseline_content` MEDIUMTEXT DEFAULT NULL" in migration_sql
    assert "`metadata_complete` TINYINT(1) NOT NULL DEFAULT 1" in migration_sql
    assert "`parse_warnings` TEXT DEFAULT NULL" in migration_sql
    assert "UNIQUE KEY `uk_supervisor_host_config_path` (`host_ip`, `config_path`)" in migration_sql
    assert "KEY `idx_supervisor_host_program` (`host_ip`, `program_name`)" in migration_sql
    assert "KEY `idx_supervisor_host_manage_mode` (`host_ip`, `manage_mode`)" in migration_sql

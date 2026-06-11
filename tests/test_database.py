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
    assert sorted(row["version"] for row in fake_mysql.tables["sys_schema_migration"]) == [1, 2, 3, 4]


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
    assert sorted(row["version"] for row in fake_mysql.tables["sys_schema_migration"]) == [1, 2, 3, 4]


def test_database_recreates_missing_supervisor_table(settings, fake_mysql):
    """Supervisor 主数据表缺失时，启动阶段也应通过单基线补建。"""
    initialize_database(settings)

    del fake_mysql.tables["sys_supervisor_service"]

    initialize_database(settings)

    assert "sys_supervisor_service" in fake_mysql.tables
    assert sorted(row["version"] for row in fake_mysql.tables["sys_schema_migration"]) == [1, 2, 3, 4]


def test_database_upgrades_legacy_supervisor_table_runtime_columns(settings, fake_mysql):
    """旧库只有 001 版本时，002 需要按缺列状态安全补齐运行时字段。"""
    fake_mysql.databases.add(settings.database.database)
    fake_mysql.tables["sys_user"] = []
    fake_mysql.tables["sys_login_log"] = []
    fake_mysql.tables["sys_login_token"] = []
    fake_mysql.tables["sys_supervisor_service"] = []
    fake_mysql.tables["sys_schema_migration"] = [{"version": 1, "name": "001_init_schema.sql"}]
    fake_mysql.set_table_schema(
        "sys_supervisor_service",
        columns={
            "id",
            "host_ip",
            "config_path",
            "file_name",
            "content_program_name",
            "manage_mode",
            "baseline_content",
            "metadata_complete",
            "parse_warnings",
            "job_name",
            "module_name",
            "program_name",
            "config_name",
            "java_path",
            "active_profile",
            "port",
            "jar_name",
            "xms",
            "xmx",
            "run_user",
            "create_time",
            "update_time",
            "create_by_id",
            "create_by",
            "update_by_id",
            "update_by",
            "remark",
        },
        indexes={
            "uk_supervisor_host_config_path",
            "idx_supervisor_host_program",
            "idx_supervisor_host_manage_mode",
        },
    )

    initialize_database(settings)

    assert sorted(row["version"] for row in fake_mysql.tables["sys_schema_migration"]) == [1, 2, 3, 4]
    assert {"status", "pid", "uptime", "status_sync_time"} <= fake_mysql.table_columns["sys_supervisor_service"]
    assert "idx_supervisor_host_status" in fake_mysql.table_indexes["sys_supervisor_service"]


def test_database_upgrades_legacy_supervisor_table_archive_columns(settings, fake_mysql):
    """旧库只有前两版结构时，003 需要按缺列状态安全补齐归档字段。"""
    fake_mysql.databases.add(settings.database.database)
    fake_mysql.tables["sys_user"] = []
    fake_mysql.tables["sys_login_log"] = []
    fake_mysql.tables["sys_login_token"] = []
    fake_mysql.tables["sys_supervisor_service"] = []
    fake_mysql.tables["sys_schema_migration"] = [
        {"version": 1, "name": "001_init_schema.sql"},
        {"version": 2, "name": "002_add_supervisor_service_runtime_columns.sql"},
    ]
    fake_mysql.set_table_schema(
        "sys_supervisor_service",
        columns={
            "id",
            "host_ip",
            "config_path",
            "file_name",
            "content_program_name",
            "manage_mode",
            "baseline_content",
            "metadata_complete",
            "parse_warnings",
            "job_name",
            "module_name",
            "program_name",
            "config_name",
            "java_path",
            "active_profile",
            "port",
            "jar_name",
            "xms",
            "xmx",
            "run_user",
            "status",
            "pid",
            "uptime",
            "status_sync_time",
            "create_time",
            "update_time",
            "create_by_id",
            "create_by",
            "update_by_id",
            "update_by",
            "remark",
        },
        indexes={
            "uk_supervisor_host_config_path",
            "idx_supervisor_host_program",
            "idx_supervisor_host_manage_mode",
            "idx_supervisor_host_status",
        },
    )

    initialize_database(settings)

    assert sorted(row["version"] for row in fake_mysql.tables["sys_schema_migration"]) == [1, 2, 3, 4]
    assert {"is_archived", "archived_at", "restored_at"} <= fake_mysql.table_columns["sys_supervisor_service"]
    assert "idx_supervisor_host_archived" in fake_mysql.table_indexes["sys_supervisor_service"]


def test_database_upgrades_legacy_supervisor_table_detail_sync_columns(settings, fake_mysql):
    """旧库只有前三版结构时，004 需要按缺列状态安全补齐详情同步字段。"""
    fake_mysql.databases.add(settings.database.database)
    fake_mysql.tables["sys_user"] = []
    fake_mysql.tables["sys_login_log"] = []
    fake_mysql.tables["sys_login_token"] = []
    fake_mysql.tables["sys_supervisor_service"] = []
    fake_mysql.tables["sys_schema_migration"] = [
        {"version": 1, "name": "001_init_schema.sql"},
        {"version": 2, "name": "002_add_supervisor_service_runtime_columns.sql"},
        {"version": 3, "name": "003_add_supervisor_archive_columns.sql"},
    ]
    fake_mysql.set_table_schema(
        "sys_supervisor_service",
        columns={
            "id",
            "host_ip",
            "config_path",
            "file_name",
            "content_program_name",
            "manage_mode",
            "baseline_content",
            "metadata_complete",
            "parse_warnings",
            "job_name",
            "module_name",
            "program_name",
            "config_name",
            "java_path",
            "active_profile",
            "port",
            "jar_name",
            "xms",
            "xmx",
            "run_user",
            "status",
            "pid",
            "uptime",
            "status_sync_time",
            "is_archived",
            "archived_at",
            "restored_at",
            "create_time",
            "update_time",
            "create_by_id",
            "create_by",
            "update_by_id",
            "update_by",
            "remark",
        },
        indexes={
            "uk_supervisor_host_config_path",
            "idx_supervisor_host_program",
            "idx_supervisor_host_manage_mode",
            "idx_supervisor_host_status",
            "idx_supervisor_host_archived",
        },
    )

    initialize_database(settings)

    assert sorted(row["version"] for row in fake_mysql.tables["sys_schema_migration"]) == [1, 2, 3, 4]
    assert {
        "command",
        "directory",
        "stdout_logfile",
        "has_backup",
        "config_content",
        "backup_config_content",
        "last_sync_at",
        "sync_status",
        "sync_error",
    } <= fake_mysql.table_columns["sys_supervisor_service"]


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
    assert "`command` VARCHAR(2000) DEFAULT NULL" in migration_sql
    assert "`directory` VARCHAR(1000) DEFAULT NULL" in migration_sql
    assert "`stdout_logfile` VARCHAR(1000) DEFAULT NULL" in migration_sql
    assert "`has_backup` TINYINT(1) NOT NULL DEFAULT 0" in migration_sql
    assert "`config_content` MEDIUMTEXT DEFAULT NULL" in migration_sql
    assert "`backup_config_content` MEDIUMTEXT DEFAULT NULL" in migration_sql
    assert "`last_sync_at` DATETIME DEFAULT NULL" in migration_sql
    assert "`sync_status` VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN'" in migration_sql
    assert "`sync_error` VARCHAR(1000) DEFAULT NULL" in migration_sql
    assert "`is_archived` TINYINT(1) NOT NULL DEFAULT 0" in migration_sql
    assert "`archived_at` DATETIME DEFAULT NULL" in migration_sql
    assert "`restored_at` DATETIME DEFAULT NULL" in migration_sql
    assert "UNIQUE KEY `uk_supervisor_host_config_path` (`host_ip`, `config_path`)" in migration_sql
    assert "KEY `idx_supervisor_host_program` (`host_ip`, `program_name`)" in migration_sql
    assert "KEY `idx_supervisor_host_manage_mode` (`host_ip`, `manage_mode`)" in migration_sql
    assert "KEY `idx_supervisor_host_archived` (`host_ip`, `is_archived`)" in migration_sql

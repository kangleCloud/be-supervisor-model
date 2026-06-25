"""数据库启动、模型覆盖与手工 SQL 基线测试。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.database.bootstrap import (
    MySQLIndexMetadata,
    _validate_mysql_supervisor_schema,
    build_sqlite_test_config,
    close_database,
    find_supervisor_service_schema_problems,
    init_database,
)
from app.database.models.auth import LoginLogModel, LoginTokenModel, UserModel
from app.database.models.supervisor import SupervisorImportStagingModel, SupervisorServiceModel


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_MIGRATIONS_DIR = PROJECT_ROOT / "app" / "database" / "migrations"
BASELINE_SQL_PATH = DATABASE_MIGRATIONS_DIR / "001_init_schema.sql"
LEGACY_FIX_SQL_PATH = DATABASE_MIGRATIONS_DIR / "002_fix_supervisor_service_legacy_schema.sql"


def test_database_init_and_close_with_sqlite(settings, test_environment, run_db):
    async def scenario():
        await close_database()
        await init_database(settings, tortoise_config=build_sqlite_test_config(f"sqlite://{test_environment['db_path']}"))
        await close_database()
        await init_database(settings, tortoise_config=build_sqlite_test_config(f"sqlite://{test_environment['db_path']}"))

        assert await UserModel.filter(user_name="admin", is_deleted=0).count() == 1
        assert await UserModel.all().count() >= 1

    run_db(scenario)


def test_database_bootstraps_single_super_admin(run_db):
    async def scenario():
        admins = await UserModel.filter(user_name="admin", is_deleted=0).all()
        assert len(admins) == 1
        assert admins[0].is_super_admin == 1
        assert admins[0].status == 1

    run_db(scenario)


def test_database_schema_contains_required_tables(run_db):
    async def scenario():
        assert await UserModel.all().count() >= 1
        assert await LoginLogModel.all().count() == 0
        assert await LoginTokenModel.all().count() == 0
        assert await SupervisorServiceModel.all().count() == 0
        assert await SupervisorImportStagingModel.all().count() == 0

    run_db(scenario)


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


def test_models_cover_final_schema_fields():
    auth_model_fields = set(UserModel._meta.fields_map.keys())
    token_model_fields = set(LoginTokenModel._meta.fields_map.keys())
    service_model_fields = set(SupervisorServiceModel._meta.fields_map.keys())
    staging_model_fields = set(SupervisorImportStagingModel._meta.fields_map.keys())

    assert {"user_name", "password", "status", "is_super_admin", "is_deleted"} <= auth_model_fields
    assert {"user_id", "token_jti", "token_digest", "expires_at", "revoked_time"} <= token_model_fields
    assert {
        "host_ip", "config_path", "file_name", "content_program_name", "manage_mode",
        "baseline_content", "metadata_complete", "parse_warnings", "command", "directory",
        "stdout_logfile", "has_backup", "config_content", "backup_config_content", "last_sync_at",
        "sync_status", "sync_error", "is_archived", "archived_at", "restored_at",
    } <= service_model_fields
    assert {
        "batch_id", "host_ip", "operator_id", "operator_name", "config_path", "file_name",
        "content_program_name", "baseline_content", "metadata_complete", "parse_warnings", "result", "message",
    } <= staging_model_fields


def test_manual_baseline_sql_exists_and_covers_final_schema():
    migration_sql = BASELINE_SQL_PATH.read_text(encoding="utf-8")

    assert BASELINE_SQL_PATH.exists()
    assert "新库初始化基线 SQL" in migration_sql
    assert "CREATE TABLE IF NOT EXISTS `sys_user`" in migration_sql
    assert "CREATE TABLE IF NOT EXISTS `sys_login_log`" in migration_sql
    assert "CREATE TABLE IF NOT EXISTS `sys_login_token`" in migration_sql
    assert "CREATE TABLE IF NOT EXISTS `sys_supervisor_service`" in migration_sql
    assert "CREATE TABLE IF NOT EXISTS `sys_supervisor_import_staging`" in migration_sql
    assert "UNIQUE KEY `uk_supervisor_host_config_path`" in migration_sql
    assert "KEY `idx_supervisor_host_program`" in migration_sql
    assert "INSERT INTO `sys_user`" in migration_sql


def test_supervisor_schema_problem_detector_flags_legacy_columns_and_indexes():
    problems = find_supervisor_service_schema_problems(
        {
            "host_ip",
            "config_path",
            "file_name",
            "content_program_name",
            "program_name",
            "config_name",
        },
        [
            MySQLIndexMetadata(name="uk_old_program", columns=("host_ip", "program_name"), unique=True),
            MySQLIndexMetadata(name="uk_old_port", columns=("host_ip", "port"), unique=True),
        ],
    )

    assert any("旧兼容字段" in item for item in problems)
    assert any("uk_old_program" in item for item in problems)
    assert any("uk_old_port" in item for item in problems)


def test_supervisor_schema_problem_detector_accepts_final_schema():
    problems = find_supervisor_service_schema_problems(
        {"host_ip", "config_path", "file_name", "content_program_name"},
        [
            MySQLIndexMetadata(name="uk_supervisor_host_config_path", columns=("host_ip", "config_path"), unique=True),
            MySQLIndexMetadata(name="idx_supervisor_host_program", columns=("host_ip", "content_program_name"), unique=False),
        ],
    )

    assert problems == []


def test_validate_mysql_supervisor_schema_raises_explicit_baseline_sql_error():
    class _FakeMySQLConnection:
        schema_generator = type("_SchemaGenerator", (), {"DIALECT": "mysql"})

        async def execute_query_dict(self, query, values):  # noqa: ANN001
            if "information_schema.TABLES" in query:
                return []
            raise AssertionError(f"unexpected query: {query}")

    try:
        asyncio.run(_validate_mysql_supervisor_schema(_FakeMySQLConnection()))
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected schema validation to fail")

    assert "001_init_schema.sql" in message
    assert "请先手工执行" in message


def test_validate_mysql_supervisor_schema_raises_explicit_legacy_fix_sql_error():
    class _FakeMySQLConnection:
        schema_generator = type("_SchemaGenerator", (), {"DIALECT": "mysql"})

        async def execute_query_dict(self, query, values):  # noqa: ANN001
            if "information_schema.TABLES" in query:
                return [{"TABLE_NAME": values[0]}]
            if "information_schema.COLUMNS" in query:
                return [
                    {"COLUMN_NAME": "host_ip"},
                    {"COLUMN_NAME": "config_path"},
                    {"COLUMN_NAME": "file_name"},
                    {"COLUMN_NAME": "content_program_name"},
                    {"COLUMN_NAME": "program_name"},
                ]
            if "information_schema.STATISTICS" in query:
                return [
                    {"INDEX_NAME": "uk_old_program", "COLUMN_NAME": "host_ip", "NON_UNIQUE": 0, "SEQ_IN_INDEX": 1},
                    {"INDEX_NAME": "uk_old_program", "COLUMN_NAME": "program_name", "NON_UNIQUE": 0, "SEQ_IN_INDEX": 2},
                ]
            raise AssertionError(f"unexpected query: {query}")

    try:
        asyncio.run(_validate_mysql_supervisor_schema(_FakeMySQLConnection()))
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected schema validation to fail")

    assert "002_fix_supervisor_service_legacy_schema.sql" in message
    assert "请先手工执行" in message
    assert "program_name" in message


def test_manual_legacy_fix_sql_exists_and_is_idempotent():
    migration_sql = LEGACY_FIX_SQL_PATH.read_text(encoding="utf-8")

    assert LEGACY_FIX_SQL_PATH.exists()
    assert "可重复执行" in migration_sql
    assert "information_schema.COLUMNS" in migration_sql
    assert "information_schema.STATISTICS" in migration_sql
    assert "DROP COLUMN `program_name`" in migration_sql
    assert "DROP COLUMN `config_name`" in migration_sql
    assert "uk_supervisor_host_config_path" in migration_sql
    assert "idx_supervisor_host_program" in migration_sql

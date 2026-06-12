"""数据库启动、模型覆盖与 Aerich 基线测试。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.database.bootstrap import (
    MySQLIndexMetadata,
    _load_db_config_for_aerich,
    _validate_mysql_supervisor_schema,
    build_sqlite_test_config,
    close_database,
    find_supervisor_service_schema_problems,
    init_database,
)
from app.database.models.auth import LoginLogModel, LoginTokenModel, UserModel
from app.database.models.supervisor import SupervisorImportStagingModel, SupervisorServiceModel


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = PROJECT_ROOT / "migrations" / "models"


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


def test_aerich_baseline_files_exist_and_are_real():
    assert MIGRATIONS_DIR.is_dir()
    assert (MIGRATIONS_DIR / "__init__.py").exists()

    migration_files = sorted(MIGRATIONS_DIR.glob("0_*_init.py"))
    assert migration_files, "缺少 Aerich baseline migration 文件"

    migration_text = migration_files[0].read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS `sys_user`" in migration_text
    assert "CREATE TABLE IF NOT EXISTS `sys_login_log`" in migration_text
    assert "CREATE TABLE IF NOT EXISTS `sys_login_token`" in migration_text
    assert "CREATE TABLE IF NOT EXISTS `sys_supervisor_service`" in migration_text
    assert "CREATE TABLE IF NOT EXISTS `sys_supervisor_import_staging`" in migration_text
    assert "INSERT INTO `sys_user`" in migration_text
    assert "'admin'" in migration_text


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


def test_validate_mysql_supervisor_schema_raises_explicit_upgrade_error():
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

    assert "请先执行 Aerich 升级" in message
    assert "program_name" in message


def test_load_db_config_for_aerich_uses_app_env(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "database:",
                "  host: 127.0.0.1",
                "  port: 3306",
                "  name: from_yaml",
                "  user: yaml_user",
            ]
        ),
        encoding="utf-8",
    )
    env_file = tmp_path / ".env.dev"
    env_file.write_text(
        "\n".join(
            [
                "DATABASE_PASSWORD=test-password",
                "DATABASE_HOST=10.9.8.7",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("app.database.bootstrap._repo_root", lambda: tmp_path)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("APP_ENV_FILE", raising=False)
    monkeypatch.delenv("APP_CONFIG_PATH", raising=False)
    monkeypatch.delenv("DATABASE_PASSWORD", raising=False)
    monkeypatch.delenv("DATABASE_HOST", raising=False)

    host, port, database, user, password = _load_db_config_for_aerich()

    assert host == "10.9.8.7"
    assert port == 3306
    assert database == "from_yaml"
    assert user == "yaml_user"
    assert password == "test-password"


def test_load_db_config_for_aerich_uses_app_env_file(monkeypatch, tmp_path):
    config_path = tmp_path / "custom.yaml"
    config_path.write_text(
        "\n".join(
            [
                "database:",
                "  host: 192.168.1.10",
                "  port: 3307",
                "  name: custom_db",
                "  user: custom_user",
            ]
        ),
        encoding="utf-8",
    )
    env_file = tmp_path / "custom.env"
    env_file.write_text(
        "\n".join(
            [
                "DATABASE_PASSWORD=env-file-password",
                f"APP_CONFIG_PATH={config_path}",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("APP_ENV_FILE", str(env_file))
    monkeypatch.delenv("DATABASE_PASSWORD", raising=False)

    host, port, database, user, password = _load_db_config_for_aerich()

    assert host == "192.168.1.10"
    assert port == 3307
    assert database == "custom_db"
    assert user == "custom_user"
    assert password == "env-file-password"


def test_incremental_supervisor_schema_fix_migration_exists():
    migration_path = MIGRATIONS_DIR / "1_20260612170000_fix_supervisor_service_legacy_schema.py"
    migration_text = migration_path.read_text(encoding="utf-8")

    assert migration_path.exists()
    assert "DROP COLUMN `program_name`" in migration_text
    assert "DROP COLUMN `config_name`" in migration_text
    assert "information_schema.COLUMNS" in migration_text
    assert "information_schema.STATISTICS" in migration_text
    assert "uk_supervisor_host_config_path" in migration_text


def test_legacy_sql_snapshot_is_reference_only():
    migration_path = PROJECT_ROOT / "app" / "database" / "migrations" / "001_init_schema.sql"
    migration_sql = migration_path.read_text(encoding="utf-8")

    assert migration_path.exists()
    assert "Historical SQL snapshot only." in migration_sql
    assert "Runtime startup no longer executes this file" in migration_sql

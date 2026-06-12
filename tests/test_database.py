"""数据库启动、模型覆盖与 Aerich 基线测试。"""
from __future__ import annotations

from pathlib import Path

from app.database.bootstrap import build_sqlite_test_config, close_database, init_database
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


def test_legacy_sql_snapshot_is_reference_only():
    migration_path = PROJECT_ROOT / "app" / "database" / "migrations" / "001_init_schema.sql"
    migration_sql = migration_path.read_text(encoding="utf-8")

    assert migration_path.exists()
    assert "Historical SQL snapshot only." in migration_sql
    assert "Runtime startup no longer executes this file" in migration_sql

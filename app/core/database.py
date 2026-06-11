"""MySQL 数据库连接与迁移。"""
from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pymysql
from pymysql.connections import Connection
from pymysql.cursors import Cursor, DictCursor

from app.core.config import Settings


MIGRATION_FILE_PATTERN = re.compile(r"^(?P<version>\d+)_.*\.sql$")
DATABASE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_]+$")
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "database" / "migrations"
REQUIRED_TABLE_MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: ("sys_user", "sys_login_log", "sys_login_token", "sys_supervisor_service"),
}
LEGACY_RUNTIME_MIGRATION_VERSION = 2
LEGACY_RUNTIME_MIGRATION_NAME = "002_add_supervisor_service_runtime_columns.sql"
LEGACY_ARCHIVE_MIGRATION_VERSION = 3
LEGACY_ARCHIVE_MIGRATION_NAME = "003_add_supervisor_archive_columns.sql"
LEGACY_DETAIL_SYNC_MIGRATION_VERSION = 4
LEGACY_DETAIL_SYNC_MIGRATION_NAME = "004_add_supervisor_detail_sync_columns.sql"
SUPERVISOR_SERVICE_TABLE = "sys_supervisor_service"
SUPERVISOR_RUNTIME_COLUMNS: tuple[tuple[str, str], ...] = (
    (
        "status",
        "ALTER TABLE `sys_supervisor_service` "
        "ADD COLUMN `status` VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN' "
        "COMMENT '运行状态快照：RUNNING/STOPPED/FATAL/BACKOFF/STARTING/STOPPING/EXITED/UNKNOWN' "
        "AFTER `run_user`",
    ),
    (
        "pid",
        "ALTER TABLE `sys_supervisor_service` "
        "ADD COLUMN `pid` VARCHAR(32) DEFAULT NULL COMMENT '进程PID' AFTER `status`",
    ),
    (
        "uptime",
        "ALTER TABLE `sys_supervisor_service` "
        "ADD COLUMN `uptime` VARCHAR(64) DEFAULT NULL COMMENT '运行时长' AFTER `pid`",
    ),
    (
        "status_sync_time",
        "ALTER TABLE `sys_supervisor_service` "
        "ADD COLUMN `status_sync_time` DATETIME DEFAULT NULL COMMENT '最近状态同步时间' AFTER `uptime`",
    ),
)
SUPERVISOR_RUNTIME_INDEXES: tuple[tuple[str, str], ...] = (
    (
        "idx_supervisor_host_status",
        "ALTER TABLE `sys_supervisor_service` ADD KEY `idx_supervisor_host_status` (`host_ip`, `status`)",
    ),
)
SUPERVISOR_ARCHIVE_COLUMNS: tuple[tuple[str, str], ...] = (
    (
        "is_archived",
        "ALTER TABLE `sys_supervisor_service` "
        "ADD COLUMN `is_archived` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否已归档 0否 1是' "
        "AFTER `status_sync_time`",
    ),
    (
        "archived_at",
        "ALTER TABLE `sys_supervisor_service` "
        "ADD COLUMN `archived_at` DATETIME DEFAULT NULL COMMENT '归档时间' AFTER `is_archived`",
    ),
    (
        "restored_at",
        "ALTER TABLE `sys_supervisor_service` "
        "ADD COLUMN `restored_at` DATETIME DEFAULT NULL COMMENT '最近还原时间' AFTER `archived_at`",
    ),
)
SUPERVISOR_ARCHIVE_INDEXES: tuple[tuple[str, str], ...] = (
    (
        "idx_supervisor_host_archived",
        "ALTER TABLE `sys_supervisor_service` ADD KEY `idx_supervisor_host_archived` (`host_ip`, `is_archived`)",
    ),
)
SUPERVISOR_DETAIL_SYNC_COLUMNS: tuple[tuple[str, str], ...] = (
    (
        "command",
        "ALTER TABLE `sys_supervisor_service` "
        "ADD COLUMN `command` VARCHAR(2000) DEFAULT NULL COMMENT '最近同步到的 command 原文' "
        "AFTER `status_sync_time`",
    ),
    (
        "directory",
        "ALTER TABLE `sys_supervisor_service` "
        "ADD COLUMN `directory` VARCHAR(1000) DEFAULT NULL COMMENT '最近同步到的工作目录' "
        "AFTER `command`",
    ),
    (
        "stdout_logfile",
        "ALTER TABLE `sys_supervisor_service` "
        "ADD COLUMN `stdout_logfile` VARCHAR(1000) DEFAULT NULL COMMENT '最近同步到的 stdout_logfile' "
        "AFTER `directory`",
    ),
    (
        "has_backup",
        "ALTER TABLE `sys_supervisor_service` "
        "ADD COLUMN `has_backup` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '当前配置是否存在 .bak 备份' "
        "AFTER `stdout_logfile`",
    ),
    (
        "config_content",
        "ALTER TABLE `sys_supervisor_service` "
        "ADD COLUMN `config_content` MEDIUMTEXT DEFAULT NULL COMMENT '最近同步到的当前配置原文' "
        "AFTER `has_backup`",
    ),
    (
        "backup_config_content",
        "ALTER TABLE `sys_supervisor_service` "
        "ADD COLUMN `backup_config_content` MEDIUMTEXT DEFAULT NULL COMMENT '最近同步到的备份配置原文' "
        "AFTER `config_content`",
    ),
    (
        "last_sync_at",
        "ALTER TABLE `sys_supervisor_service` "
        "ADD COLUMN `last_sync_at` DATETIME DEFAULT NULL COMMENT '最近执行详情同步时间' "
        "AFTER `backup_config_content`",
    ),
    (
        "sync_status",
        "ALTER TABLE `sys_supervisor_service` "
        "ADD COLUMN `sync_status` VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN' COMMENT '详情同步状态：SUCCESS/FAILED/UNKNOWN' "
        "AFTER `last_sync_at`",
    ),
    (
        "sync_error",
        "ALTER TABLE `sys_supervisor_service` "
        "ADD COLUMN `sync_error` VARCHAR(1000) DEFAULT NULL COMMENT '最近一次详情同步错误摘要' "
        "AFTER `sync_status`",
    ),
)


def _connect(settings: Settings, *, include_database: bool) -> Connection:
    database_name = settings.database.database if include_database else None
    return pymysql.connect(
        host=settings.database.host,
        port=settings.database.port,
        user=settings.database.user,
        password=settings.database.password,
        database=database_name,
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=settings.database.connect_timeout_seconds,
        cursorclass=DictCursor,
    )


def connect_server(settings: Settings) -> Connection:
    """连接到 MySQL 实例，不指定业务库。"""
    return _connect(settings, include_database=False)


def connect_database(settings: Settings) -> Connection:
    """连接到业务数据库。"""
    return _connect(settings, include_database=True)


@contextmanager
def get_connection(settings: Settings) -> Iterator[Connection]:
    """按需获取数据库连接，避免在进程内长期持有连接状态。"""
    connection = connect_database(settings)
    try:
        yield connection
    finally:
        connection.close()


def initialize_database(settings: Settings) -> None:
    """启动时初始化数据库与迁移版本。"""
    database_name = settings.database.database
    if not DATABASE_NAME_PATTERN.fullmatch(database_name):
        raise ValueError("DATABASE_NAME 只允许字母、数字和下划线")

    server_connection = connect_server(settings)
    try:
        with server_connection.cursor() as cursor:
            # 数据库名来自受控配置，先校验再拼接，避免动态标识符注入。
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database_name}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
            )
        server_connection.commit()
    finally:
        server_connection.close()

    database_connection = connect_database(settings)
    try:
        with database_connection.cursor() as cursor:
            _ensure_migration_table(cursor)
            migrations = _iter_migrations()
            applied_versions = _load_applied_versions(cursor)
            for version, migration_path in migrations:
                if version in applied_versions:
                    continue
                _apply_migration(cursor, settings, version, migration_path)
                cursor.execute(
                    "INSERT INTO sys_schema_migration(version, name) VALUES (%s, %s)",
                    (version, migration_path.name),
                )
            # 历史版本记录只能说明“迁移曾尝试执行”，不能说明关键表一定真实存在。
            _ensure_required_tables(cursor, migrations)
        database_connection.commit()
    finally:
        database_connection.close()


def _ensure_migration_table(cursor: Cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sys_schema_migration (
            version INT NOT NULL PRIMARY KEY COMMENT '迁移版本号',
            name VARCHAR(255) NOT NULL COMMENT '迁移文件名',
            applied_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '执行时间'
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        COMMENT='数据库迁移记录表'
        """
    )


def _load_applied_versions(cursor: Cursor) -> set[int]:
    cursor.execute("SELECT version FROM sys_schema_migration")
    return {int(row["version"]) for row in cursor.fetchall()}


def _iter_migrations() -> list[tuple[int, Path]]:
    migrations: list[tuple[int, Path]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = MIGRATION_FILE_PATTERN.fullmatch(path.name)
        if match is None:
            raise ValueError(f"非法迁移文件名: {path.name}")
        migrations.append((int(match.group("version")), path))
    return migrations


def _execute_script(cursor: Cursor, script: str) -> None:
    for statement in _split_sql_statements(script):
        cursor.execute(statement)


def _apply_migration(cursor: Cursor, settings: Settings, version: int, migration_path: Path) -> None:
    """兼容旧库升级迁移，避免新库重复补列。"""
    if version == LEGACY_RUNTIME_MIGRATION_VERSION and migration_path.name == LEGACY_RUNTIME_MIGRATION_NAME:
        _apply_supervisor_runtime_columns_migration(cursor, settings.database.database)
        return
    if version == LEGACY_ARCHIVE_MIGRATION_VERSION and migration_path.name == LEGACY_ARCHIVE_MIGRATION_NAME:
        _apply_supervisor_archive_columns_migration(cursor, settings.database.database)
        return
    if version == LEGACY_DETAIL_SYNC_MIGRATION_VERSION and migration_path.name == LEGACY_DETAIL_SYNC_MIGRATION_NAME:
        _apply_supervisor_detail_sync_columns_migration(cursor, settings.database.database)
        return
    _execute_script(cursor, migration_path.read_text(encoding="utf-8"))


def _apply_supervisor_runtime_columns_migration(cursor: Cursor, database_name: str) -> None:
    """仅在旧库缺少运行时字段时补建，避免新库重复 ADD COLUMN。"""
    if not _table_exists(cursor, SUPERVISOR_SERVICE_TABLE):
        return

    for column_name, statement in SUPERVISOR_RUNTIME_COLUMNS:
        if _column_exists(cursor, database_name, SUPERVISOR_SERVICE_TABLE, column_name):
            continue
        cursor.execute(statement)

    for index_name, statement in SUPERVISOR_RUNTIME_INDEXES:
        if _index_exists(cursor, SUPERVISOR_SERVICE_TABLE, index_name):
            continue
        cursor.execute(statement)


def _apply_supervisor_archive_columns_migration(cursor: Cursor, database_name: str) -> None:
    """仅在旧库缺少归档字段时补建，避免新库重复 ADD COLUMN。"""
    if not _table_exists(cursor, SUPERVISOR_SERVICE_TABLE):
        return

    for column_name, statement in SUPERVISOR_ARCHIVE_COLUMNS:
        if _column_exists(cursor, database_name, SUPERVISOR_SERVICE_TABLE, column_name):
            continue
        cursor.execute(statement)

    for index_name, statement in SUPERVISOR_ARCHIVE_INDEXES:
        if _index_exists(cursor, SUPERVISOR_SERVICE_TABLE, index_name):
            continue
        cursor.execute(statement)


def _apply_supervisor_detail_sync_columns_migration(cursor: Cursor, database_name: str) -> None:
    """仅在旧库缺少详情同步字段时补建，避免新库重复 ADD COLUMN。"""
    if not _table_exists(cursor, SUPERVISOR_SERVICE_TABLE):
        return

    for column_name, statement in SUPERVISOR_DETAIL_SYNC_COLUMNS:
        if _column_exists(cursor, database_name, SUPERVISOR_SERVICE_TABLE, column_name):
            continue
        cursor.execute(statement)


def _ensure_required_tables(cursor: Cursor, migrations: list[tuple[int, Path]]) -> None:
    """校验关键业务表存在，缺表时重放单基线 SQL 补建。"""
    for version, table_names in REQUIRED_TABLE_MIGRATIONS.items():
        missing_tables = _find_missing_tables(cursor, table_names)
        if not missing_tables:
            continue

        migration_path = _find_migration_path(migrations, version)
        if migration_path is None:
            raise RuntimeError(f"缺少关键迁移文件，无法自动补建表: version={version}")

        _execute_script(cursor, migration_path.read_text(encoding="utf-8"))
        missing_tables = _find_missing_tables(cursor, table_names)
        if missing_tables:
            missing_text = ", ".join(missing_tables)
            raise RuntimeError(f"关键业务表创建失败: {missing_text}")


def _find_missing_tables(cursor: Cursor, table_names: tuple[str, ...]) -> list[str]:
    """逐个检查关键表是否存在，避免只依赖迁移版本号。"""
    missing_tables: list[str] = []
    for table_name in table_names:
        if not _table_exists(cursor, table_name):
            missing_tables.append(table_name)
    return missing_tables


def _table_exists(cursor: Cursor, table_name: str) -> bool:
    cursor.execute("SHOW TABLES LIKE %s", (table_name,))
    return cursor.fetchone() is not None


def _column_exists(cursor: Cursor, database_name: str, table_name: str, column_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s
        LIMIT 1
        """,
        (database_name, table_name, column_name),
    )
    return cursor.fetchone() is not None


def _index_exists(cursor: Cursor, table_name: str, index_name: str) -> bool:
    # 索引名来自受控常量，使用 SHOW INDEX 与真实 MySQL 保持一致。
    cursor.execute(f"SHOW INDEX FROM `{table_name}`")
    return any((row.get("Key_name") or row.get("key_name")) == index_name for row in cursor.fetchall())


def _find_migration_path(migrations: list[tuple[int, Path]], version: int) -> Path | None:
    for migration_version, migration_path in migrations:
        if migration_version == version:
            return migration_path
    return None


def _split_sql_statements(script: str) -> list[str]:
    statements: list[str] = []
    current_lines: list[str] = []
    for raw_line in script.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current_lines.append(raw_line)
        if stripped.endswith(";"):
            statement = "\n".join(current_lines).rstrip().rstrip(";").strip()
            if statement:
                statements.append(statement)
            current_lines = []

    trailing = "\n".join(current_lines).strip()
    if trailing:
        statements.append(trailing)
    return statements

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
    1: ("sys_user", "sys_login_log", "sys_login_token"),
    3: ("sys_supervisor_service",),
}


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
                _execute_script(cursor, migration_path.read_text(encoding="utf-8"))
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


def _ensure_required_tables(cursor: Cursor, migrations: list[tuple[int, Path]]) -> None:
    """校验关键业务表存在，缺表时按对应迁移补建。"""
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

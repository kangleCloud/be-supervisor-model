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
            applied_versions = _load_applied_versions(cursor)
            for version, migration_path in _iter_migrations():
                if version in applied_versions:
                    continue
                _execute_script(cursor, migration_path.read_text(encoding="utf-8"))
                cursor.execute(
                    "INSERT INTO sys_schema_migration(version, name) VALUES (%s, %s)",
                    (version, migration_path.name),
                )
        database_connection.commit()
    finally:
        database_connection.close()


def _ensure_migration_table(cursor: Cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sys_schema_migration (
            version INT NOT NULL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            applied_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
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

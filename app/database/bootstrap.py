"""Tortoise ORM 启动与事务封装。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import quote_plus

from tortoise import Tortoise
from tortoise.connection import connections
from tortoise.transactions import in_transaction as tortoise_in_transaction

from app.core.config import Settings


MODELS_APP = {
    "models": [
        "app.database.models",
    ],
    "default_connection": "default",
}

MYSQL_DIALECT = "mysql"
SUPERVISOR_SERVICE_TABLE = "sys_supervisor_service"
BASELINE_SCHEMA_SQL_PATH = "app/database/migrations/001_init_schema.sql"
LEGACY_FIX_SCHEMA_SQL_PATH = "app/database/migrations/002_fix_supervisor_service_legacy_schema.sql"
LEGACY_SUPERVISOR_COLUMNS = frozenset({"program_name", "config_name"})
REQUIRED_SUPERVISOR_COLUMNS = frozenset({"config_path", "file_name", "content_program_name"})


@dataclass(frozen=True)
class MySQLIndexMetadata:
    """MySQL 索引元数据，用于启动前 schema 校验。"""

    name: str
    columns: tuple[str, ...]
    unique: bool


def find_supervisor_service_schema_problems(
    columns: set[str],
    indexes: list[MySQLIndexMetadata],
) -> list[str]:
    """收敛旧版主表遗留问题，启动时统一 fail-fast。"""
    problems: list[str] = []

    missing_columns = sorted(REQUIRED_SUPERVISOR_COLUMNS - columns)
    if missing_columns:
        problems.append(f"缺少必要字段: {', '.join(missing_columns)}")

    legacy_columns = sorted(LEGACY_SUPERVISOR_COLUMNS & columns)
    if legacy_columns:
        problems.append(f"仍存在旧兼容字段: {', '.join(legacy_columns)}")

    legacy_indexes = [
        index.name
        for index in indexes
        if any(column in LEGACY_SUPERVISOR_COLUMNS for column in index.columns)
        or (index.unique and index.columns == ("host_ip", "port"))
    ]
    if legacy_indexes:
        problems.append(f"仍存在旧兼容索引/唯一键: {', '.join(sorted(legacy_indexes))}")

    return problems


async def _load_mysql_supervisor_columns(connection) -> set[str]:
    """读取 sys_supervisor_service 列集合，用于判断是否仍停留在旧 schema。"""
    rows = await connection.execute_query_dict(
        """
        SELECT COLUMN_NAME
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
        """,
        [SUPERVISOR_SERVICE_TABLE],
    )
    return {str(row["COLUMN_NAME"]) for row in rows}


async def _load_mysql_supervisor_indexes(connection) -> list[MySQLIndexMetadata]:
    """按索引名聚合字段顺序，便于识别 legacy 唯一键和旧字段索引。"""
    rows = await connection.execute_query_dict(
        """
        SELECT INDEX_NAME, COLUMN_NAME, NON_UNIQUE, SEQ_IN_INDEX
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
        ORDER BY INDEX_NAME ASC, SEQ_IN_INDEX ASC
        """,
        [SUPERVISOR_SERVICE_TABLE],
    )
    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        index_name = str(row["INDEX_NAME"])
        bucket = grouped.setdefault(
            index_name,
            {
                "unique": int(row["NON_UNIQUE"]) == 0,
                "columns": [],
            },
        )
        cast_columns = bucket["columns"]
        assert isinstance(cast_columns, list)
        cast_columns.append(str(row["COLUMN_NAME"]))
    return [
        MySQLIndexMetadata(name=name, columns=tuple(item["columns"]), unique=bool(item["unique"]))
        for name, item in grouped.items()
    ]


async def _validate_mysql_supervisor_schema(connection) -> None:
    """MySQL 启动前校验主表结构，发现旧库直接阻止继续运行。"""
    # 这里故意 fail-fast：手工 SQL 没有执行完整时，继续启动只会把问题拖到更隐蔽的业务路径里。
    table_rows = await connection.execute_query_dict(
        """
        SELECT TABLE_NAME
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
        """,
        [SUPERVISOR_SERVICE_TABLE],
    )
    if not table_rows:
        raise RuntimeError(
            f"数据库缺少表 {SUPERVISOR_SERVICE_TABLE}，请先手工执行 {BASELINE_SCHEMA_SQL_PATH}"
        )

    columns = await _load_mysql_supervisor_columns(connection)
    indexes = await _load_mysql_supervisor_indexes(connection)
    problems = find_supervisor_service_schema_problems(columns, indexes)
    if problems:
        raise RuntimeError(
            "数据库结构落后于当前版本，请先手工执行 "
            f"{LEGACY_FIX_SCHEMA_SQL_PATH}；当前问题：{'；'.join(problems)}"
        )


async def _validate_runtime_schema() -> None:
    """仅对 MySQL 做运行时 fail-fast，SQLite 测试环境继续走轻量初始化。"""
    connection = connections.get("default")
    if connection.schema_generator.DIALECT != MYSQL_DIALECT:
        return
    await _validate_mysql_supervisor_schema(connection)


def build_mysql_dsn(settings: Settings) -> str:
    """沿用现有配置语义，派生 Tortoise MySQL DSN。"""
    password = quote_plus(settings.database.password)
    user = quote_plus(settings.database.user)
    database = quote_plus(settings.database.database)
    return (
        f"mysql://{user}:{password}@{settings.database.host}:{settings.database.port}/{database}"
        f"?charset=utf8mb4"
    )


def build_tortoise_config(settings: Settings, *, connection_url: str | None = None) -> dict[str, object]:
    """构造 Tortoise ORM 运行配置。"""
    return {
        "connections": {
            "default": connection_url or build_mysql_dsn(settings),
        },
        "apps": {
            "models": MODELS_APP,
        },
        "use_tz": False,
        "timezone": "UTC",
    }


def build_sqlite_test_config(database_url: str = "sqlite://:memory:") -> dict[str, object]:
    """为测试提供轻量 SQLite 配置。"""
    return {
        "connections": {
            "default": database_url,
        },
        "apps": {
            "models": MODELS_APP,
        },
        "use_tz": False,
        "timezone": "UTC",
    }


async def init_database(settings: Settings, *, tortoise_config: dict[str, object] | None = None) -> None:
    """初始化 ORM 连接，并在 MySQL 场景下提前阻断旧 schema 继续运行。"""
    await Tortoise.init(config=tortoise_config or build_tortoise_config(settings))
    try:
        # 运行期不再偷偷修库；发现 schema 落后时直接阻止继续启动，避免业务在半兼容状态下写脏数据。
        await _validate_runtime_schema()
    except Exception:
        await close_database()
        raise


async def close_database() -> None:
    """关闭 ORM 连接。"""
    await Tortoise.close_connections()


@asynccontextmanager
async def transaction_context():
    """统一事务上下文，供仓储层复用。"""
    async with tortoise_in_transaction() as connection:
        yield connection


__all__ = [
    "build_mysql_dsn",
    "build_sqlite_test_config",
    "build_tortoise_config",
    "close_database",
    "find_supervisor_service_schema_problems",
    "init_database",
    "transaction_context",
]

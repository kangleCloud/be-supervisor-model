"""Tortoise ORM 启动与事务封装。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote_plus

import yaml
from tortoise import Tortoise
from tortoise.connection import connections
from tortoise.transactions import in_transaction as tortoise_in_transaction

from app.core.config import (
    DEFAULT_DATABASE_HOST,
    DEFAULT_DATABASE_NAME,
    DEFAULT_DATABASE_PORT,
    DEFAULT_DATABASE_USER,
    Settings,
)
from app.core.env_loader import build_runtime_environ


MODELS_APP = {
    "models": [
        "app.database.models",
        "aerich.models",
    ],
    "default_connection": "default",
}

MYSQL_DIALECT = "mysql"
SUPERVISOR_SERVICE_TABLE = "sys_supervisor_service"
LEGACY_SUPERVISOR_COLUMNS = frozenset({"program_name", "config_name"})
REQUIRED_SUPERVISOR_COLUMNS = frozenset({"config_path", "file_name", "content_program_name"})


@dataclass(frozen=True)
class MySQLIndexMetadata:
    """MySQL 索引元数据，用于启动前 schema 校验。"""

    name: str
    columns: tuple[str, ...]
    unique: bool


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_yaml_config(config_path: Path) -> dict[str, object]:
    """复用应用配置语义，读取 YAML 主配置。"""
    if not config_path.exists():
        return {}

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("config.yaml 顶层结构必须是对象")
    return payload


def _get_nested(config_data: Mapping[str, object], *keys: str, default: Any = None) -> Any:
    current: Any = config_data
    for key in keys:
        if not isinstance(current, Mapping) or key not in current:
            return default
        current = current[key]
    return current


def _resolve_external_runtime_sources(
    environ: Mapping[str, str] | None = None,
    *,
    repo_root: Path | None = None,
) -> tuple[dict[str, str], dict[str, object], Path]:
    """为 Aerich 和外部脚本复用与应用一致的 .env + config.yaml 解析规则。"""
    # 优先级固定为：显式环境变量 > .env.dev/.env.prod > config.yaml > 代码默认值。
    actual_repo_root = repo_root or _repo_root()
    raw_environ = dict(environ or os.environ)
    runtime_environ = build_runtime_environ(raw_environ, actual_repo_root)
    config_path = Path((runtime_environ.get("APP_CONFIG_PATH") or "").strip() or actual_repo_root / "config.yaml").expanduser()
    if not config_path.is_absolute():
        config_path = (actual_repo_root / config_path).resolve()
    return runtime_environ, _load_yaml_config(config_path), config_path


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
    # 这里故意 fail-fast：代码已经按新 schema 运行时，继续容忍旧列/旧索引只会把问题拖到更隐蔽的业务路径里。
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
            "数据库缺少表 sys_supervisor_service，请先执行 Aerich 升级：APP_ENV=dev .venv/bin/aerich upgrade"
        )

    columns = await _load_mysql_supervisor_columns(connection)
    indexes = await _load_mysql_supervisor_indexes(connection)
    problems = find_supervisor_service_schema_problems(columns, indexes)
    if problems:
        raise RuntimeError(
            "数据库结构落后于当前版本，请先执行 Aerich 升级：APP_ENV=dev .venv/bin/aerich upgrade；"
            f"当前问题：{'；'.join(problems)}"
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
    """构造 Tortoise/Aerich 共用配置。"""
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


def _load_db_config_for_aerich() -> tuple[str, int, str, str, str]:
    """为 Aerich 复用与应用一致的 APP_ENV/.env.* 解析规则。"""
    runtime_environ, config_data, _ = _resolve_external_runtime_sources()
    host = (runtime_environ.get("DATABASE_HOST") or _get_nested(config_data, "database", "host", default=DEFAULT_DATABASE_HOST) or DEFAULT_DATABASE_HOST).strip()
    port = int(runtime_environ.get("DATABASE_PORT") or _get_nested(config_data, "database", "port", default=DEFAULT_DATABASE_PORT) or DEFAULT_DATABASE_PORT)
    database = str(
        runtime_environ.get("DATABASE_NAME")
        or _get_nested(config_data, "database", "name", default=DEFAULT_DATABASE_NAME)
        or DEFAULT_DATABASE_NAME
    ).strip()
    user = str(
        runtime_environ.get("DATABASE_USER")
        or _get_nested(config_data, "database", "user", default=DEFAULT_DATABASE_USER)
        or DEFAULT_DATABASE_USER
    ).strip()
    password = str(runtime_environ.get("DATABASE_PASSWORD") or _get_nested(config_data, "database", "password", default="") or "")
    return host, port, database, user, password


def _build_aerich_default_dsn() -> str:
    host, port, database, user, password = _load_db_config_for_aerich()
    return f"mysql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{quote_plus(database)}?charset=utf8mb4"


def get_tortoise_orm(settings: Settings) -> dict[str, object]:
    """为调用方显式提供 ORM 配置。"""
    return build_tortoise_config(settings)


# Aerich 与应用共享同一份 Tortoise 配置，避免“应用能连、迁移命令却读了另一套环境变量”的双轨行为。
TORTOISE_ORM = {
    "connections": {
        "default": _build_aerich_default_dsn(),
    },
    "apps": {
        "models": MODELS_APP,
    },
    "use_tz": False,
    "timezone": "UTC",
}


__all__ = [
    "TORTOISE_ORM",
    "build_mysql_dsn",
    "build_sqlite_test_config",
    "build_tortoise_config",
    "close_database",
    "get_tortoise_orm",
    "init_database",
    "transaction_context",
]
